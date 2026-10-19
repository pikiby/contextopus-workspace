"""Expiring, context-qualified edit reservations in Workspace's SQL database.

Callers hold the context control guard across admission and the file mutation.
SQLite transactions last only for bookkeeping, never for model work or indexing.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import time
import uuid

from .runtime import PCWError

TABLE = "workspace_edit_reservations"
DEFAULT_TTL = 900
_admitted = ContextVar("workspace_admitted_edits", default=None)


class EditReservations:
    def __init__(self, control):
        self.control = control
        self.storage = control.storage
        self.context = control.workspace
        self.scope = (str(control.work_root), self.context)

    def _available(self, db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None

    def rows(self, material_ids=None, *, active=True):
        if not self.storage.path.exists():
            return []
        with self.storage.connect(write=False) as db:
            if not self._available(db):
                return []  # Old-format readers do not create or migrate SQL.
            return self._rows(db, material_ids, active=active)

    def _rows(self, db, material_ids=None, *, active=True, now=None):
        sql = f"SELECT material_id, cycle_id, lease_id, reason, expires_at FROM {TABLE} WHERE context=?"
        args = [self.context]
        if active:
            sql += " AND expires_at>?"
            args.append(time.time() if now is None else now)
        if material_ids is not None:
            if not material_ids:
                return []
            sql += " AND material_id IN (" + ",".join("?" for _ in material_ids) + ")"
            args.extend(material_ids)
        names = ("material_id", "cycle_id", "reservation_id", "reason", "expires_at")
        return [dict(zip(names, row)) for row in db.execute(sql + " ORDER BY material_id", args)]

    @staticmethod
    def _ttl(seconds):
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise PCWError("ttl_seconds must be an integer between 1 and 3600 (default 900)")
        return seconds

    @staticmethod
    def _reason(reason):
        if (not isinstance(reason, str) or not reason.strip() or len(reason) > 240
                or any(ord(char) < 32 for char in reason)):
            raise PCWError("reason must be one nonempty line of at most 240 characters")
        return reason.strip()

    @staticmethod
    def _busy(rows):
        if rows:
            raise PCWError("Materials reserved; continue independent work or retry after release: "
                           + json.dumps(rows, ensure_ascii=False))

    def acquire(self, cycle_id, material_ids, reason, ttl_seconds=DEFAULT_TTL):
        reason, ttl = self._reason(reason), self._ttl(ttl_seconds)
        ids = sorted(set(material_ids))
        if not ids:
            return {}
        with self.storage.connect() as db:
            if not self._available(db):
                raise PCWError("Edit reservations require an explicit Workspace module update")
            now = time.time()
            rows = self._rows(db, ids, now=now)
            self._busy([row for row in rows if row["cycle_id"] != cycle_id])
            bindings = {row["material_id"]: row["reservation_id"] for row in rows}
            db.execute(f"DELETE FROM {TABLE} WHERE context=? AND expires_at<=?", (self.context, now))
            new_id, expires = uuid.uuid4().hex, now + ttl
            # No deletion/insertion occurs before the entire requested set passes.
            for ident in ids:
                if ident in bindings:
                    continue  # Reuse ownership without silently extending its TTL.
                db.execute(f"INSERT OR REPLACE INTO {TABLE} VALUES (?,?,?,?,?,?)",
                           (self.context, ident, cycle_id, new_id, reason, expires))
                bindings[ident] = new_id
            return bindings

    def validate(self, cycle_id, bindings):
        rows = {row["material_id"]: row for row in self.rows(list(bindings))}
        if any(ident not in rows or rows[ident]["cycle_id"] != cycle_id
               or rows[ident]["reservation_id"] != lease for ident, lease in bindings.items()):
            raise PCWError("Edit reservation expired, released or replaced; read current materials and prepare and present again")

    @contextmanager
    def admission(self, cycle_id, bindings):
        self.validate(cycle_id, bindings)
        token = _admitted.set((self.scope, dict(bindings)))
        try:
            yield
        finally:
            _admitted.reset(token)

    def check_write(self, material_ids=None):
        admission = _admitted.get()
        bindings = admission[1] if admission and admission[0] == self.scope else {}
        # An admitted operation keeps its context guard until all file writes end.
        # Its lease may expire during indexing, but no new writer can enter then.
        self._busy([row for row in self.rows(material_ids)
                    if bindings.get(row["material_id"]) != row["reservation_id"]])

    def renew(self, cycle_id, reservation_id, ttl_seconds):
        ttl = self._ttl(ttl_seconds)
        with self.storage.connect() as db:
            rows = [row for row in self._rows(db) if row["reservation_id"] == reservation_id]
            if not rows or any(row["cycle_id"] != cycle_id for row in rows):
                raise PCWError("Reservation is expired, missing or belongs to another cycle; reserve again and reprepare")
            db.execute(f"UPDATE {TABLE} SET expires_at=? WHERE context=? AND cycle_id=? AND lease_id=?",
                       (time.time() + ttl, self.context, cycle_id, reservation_id))

    def release(self, cycle_id, bindings=None, reservation_id=None):
        if not self.storage.path.exists():
            return
        with self.storage.connect() as db:
            if not self._available(db):
                return
            if bindings is not None:
                db.executemany(f"DELETE FROM {TABLE} WHERE context=? AND cycle_id=? AND material_id=? AND lease_id=?",
                               [(self.context, cycle_id, ident, lease) for ident, lease in bindings.items()])
            elif reservation_id is not None:
                foreign = db.execute(f"SELECT 1 FROM {TABLE} WHERE context=? AND lease_id=? AND cycle_id<>?",
                                     (self.context, reservation_id, cycle_id)).fetchone()
                if foreign:
                    raise PCWError("Cannot release another cycle's reservation")
                db.execute(f"DELETE FROM {TABLE} WHERE context=? AND cycle_id=? AND lease_id=?",
                           (self.context, cycle_id, reservation_id))
            else:
                db.execute(f"DELETE FROM {TABLE} WHERE context=? AND cycle_id=?", (self.context, cycle_id))


class ReservationOperations:
    def _edits(self):
        return EditReservations(self.control)

    def reserve(self, cycle_id: str, material_ids: list[str], reason: str,
                ttl_seconds: int = DEFAULT_TTL):
        with self._cycle(cycle_id) as (_, cycle), self.engine.read_guard():
            self._require_work(cycle)
            if not material_ids or len(material_ids) > 100:
                raise PCWError("Reserve between 1 and 100 existing materials together")
            ids = [self.engine._material(ident).id for ident in material_ids]
            self._edits().acquire(cycle_id, ids, reason, ttl_seconds)
            return {"status": "reserved", "reservations": self._edits().rows(ids)}

    def reservations(self, cycle_id: str | None = None, offset: int = 0, limit: int = 50):
        self._require_format(storage=False)
        if offset < 0 or not 1 <= limit <= 100:
            raise PCWError("Use offset >= 0 and limit between 1 and 100")
        with self.control.guard():
            rows = self._edits().rows()
            if cycle_id is not None:
                rows = [row for row in rows if row["cycle_id"] == cycle_id]
            return {"reservations": rows[offset:offset + limit], "total": len(rows)}

    def reservation_renew(self, cycle_id: str, reservation_id: str, ttl_seconds: int = DEFAULT_TTL):
        with self._cycle(cycle_id):
            self._edits().renew(cycle_id, reservation_id, ttl_seconds)
            return {"status": "renewed", "reservations": [row for row in self._edits().rows()
                    if row["reservation_id"] == reservation_id]}

    def reservation_release(self, cycle_id: str, reservation_id: str):
        with self._cycle(cycle_id):
            self._edits().release(cycle_id, reservation_id=reservation_id)
            return {"status": "released", "reservation_id": reservation_id}
