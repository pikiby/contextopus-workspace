"""Observed calls and Git references. Legacy file bodies remain readable evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .runtime import PCWError
from .analytics_migration import AnalyticsMigrationMixin


SECRET_KEY = re.compile(
    r"^(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|authorization|cookie|private[_-]?key)$",
    re.I,
)
SECRET_VALUE = re.compile(
    r"(?im)(\b(?:password|passwd|api[_-]?key|access[_-]?token|secret|authorization)\s*[:=]\s*)([^\r\n]+)"
)


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value):
    """Known credential keys/patterns plus credentials explicitly held in the environment.

    Not a semantic classifier: unknown unlabelled secrets cannot be recognized.
    """
    count = 0
    known = [
        value
        for key, value in os.environ.items()
        if len(value) >= 8
        and re.search(r"(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)$", key)
    ]

    def clean(item):
        nonlocal count
        if isinstance(item, dict):
            result = {}
            for key, part in item.items():
                if SECRET_KEY.fullmatch(str(key)):
                    result[key] = "[REDACTED_SECRET]"
                    count += 1
                else:
                    result[key] = clean(part)
            return result
        if isinstance(item, (list, tuple)):
            return [clean(part) for part in item]
        if not isinstance(item, str):
            return item
        for secret in known:
            if secret in item:
                count += item.count(secret)
                item = item.replace(secret, "[REDACTED_SECRET]")
        item, n = re.subn(
            r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
            "[REDACTED_SECRET]",
            item,
        )
        count += n
        item, n = re.subn(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED_SECRET]", item
        )
        count += n
        item, n = re.subn(
            r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED_SECRET]@", item
        )
        count += n
        item, n = SECRET_VALUE.subn(r"\1[REDACTED_SECRET]", item)
        count += n
        return item

    result = clean(value)
    return result, count


class EventStore:
    def __init__(self, work_root):
        root = Path(work_root)
        if not root.is_absolute() or not root.is_dir():
            raise PCWError("Analytics work_root must be an existing absolute directory")
        from .layout import WorkspaceLayout as StorageLayout

        self.layout = StorageLayout.resolve(root)
        self.work_root = self.layout.root
        self.legacy_root = (
            self.work_root / ".pcw/.analytics"
            if self.layout.legacy
            else self.layout.analytics
        )
        self.legacy_path = self.legacy_root / "events.sqlite"
        from contextopus.core.module_storage import ModuleStorage
        self.storage = ModuleStorage(self.layout).for_module("workspace").native()
        self.path = self.storage.path
        self.root = self.path.parent

    @property
    def schema_contract(self):
        return {"engine": "sqlite", "schema_version": 2, "migrations": "sql_migrations",
                "compatible_versions": {"min": 2, "max": 2}}

    @property
    def package(self):
        return Path(__file__).parent

    def storage_prepare(self):
        """Review the legacy import and packaged schema without creating files."""
        imported = self.storage.prepare_import(self.legacy_path) if self.legacy_path.exists() else None
        schema = self.storage.prepare_schema(self.schema_contract, self.package)
        from .material_ids import prepare as prepare_ids
        reservations = prepare_ids(self.layout)
        # Completed steps do not change the identity of the original import;
        # callers can retry an interrupted import with the original revision.
        basis = {"import": imported["revision"] if imported else None,
                 "schema": schema["migrations"], "module": "workspace", "reservations": reservations["sha256"]}
        revision = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
        return {"revision": revision, "import": imported, "schema": schema, "reservations": reservations,
                "needs_migration": bool(imported and imported["status"] != "complete") or schema["needs_migration"] or reservations["needs_migration"]}

    @staticmethod
    def _upgrade_columns(db, version):
        if version == 2:
            return  # The additive reservation table is wholly declared in SQL.
        if version != 1:
            raise PCWError("Unsupported Workspace SQL migration")
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        optional = {
            "events": {"instructions_revision": "TEXT", "instructions_ref": "TEXT",
                "runtime_version": "TEXT", "procedures_revision": "TEXT",
                "operation_id": "TEXT", "journal_format": "INTEGER NOT NULL DEFAULT 1"},
            "evaluations": {"scope": "TEXT", "instruction_basis": "TEXT"},
        }
        for table, additions in optional.items():
            if table not in existing:
                continue
            columns = {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}
            for name, kind in additions.items():
                if name not in columns:
                    db.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + kind)

    def storage_apply(self, expected_revision):
        """Explicit install/update only; preserve legacy backup and recover retries."""
        plan = self.storage_prepare()
        if plan["revision"] != expected_revision:
            raise PCWError("Workspace SQL storage changed; prepare and review again")
        imported = None
        if plan["import"]:
            imported = self.storage.apply_import(self.legacy_path, plan["import"]["revision"])
        schema = self.storage.prepare_schema(self.schema_contract, self.package)
        result = self.storage.apply_schema(self.schema_contract, self.package, schema["revision"],
                                           upgrade=self._upgrade_columns)
        from .material_ids import apply as apply_ids
        reservations = apply_ids(self.layout, plan["reservations"]["sha256"])
        return {"status": "complete", "path": str(self.path), "import": imported, "schema": result,
                "reservations": reservations}

    def storage_ready(self):
        """Read-only restart check; never creates or upgrades domain storage."""
        if not self.path.is_file():
            raise PCWError("Workspace SQL storage requires explicit module installation/update")
        schema = self.storage.prepare_schema(self.schema_contract, self.package)
        if schema["needs_migration"]:
            raise PCWError("Workspace SQL schema requires explicit module installation/update")
        if self.legacy_path.exists():
            from contextopus.core.module_storage.trusted_import import _read_marker
            with self.storage.connect(write=False) as reader:
                marker = _read_marker(reader)
                if marker is None or marker["plan"]["source"] != self.legacy_path.relative_to(self.work_root).as_posix():
                    raise PCWError("Workspace SQL storage has no matching legacy migration receipt")
        from .material_ids import prepare as prepare_ids
        if prepare_ids(self.layout)["needs_migration"]:
            raise PCWError("Workspace material reservations require explicit module installation/update")
        return {"ready": True, "path": str(self.path), "schema_version": schema["current_version"]}

    def _safe(self):
        self.storage.storage._safe_path()
        for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if path.is_symlink():
                raise PCWError("Symlinks are forbidden in the analytics store")

    @contextmanager
    def connect(self):
        self._safe()
        self.storage_ready()
        with self.storage.connect() as db:
            yield db

    @staticmethod
    def _blob(db, value):
        safe, redactions = redact(value)
        text = json.dumps(safe, ensure_ascii=False, sort_keys=True)
        ident = hashlib.sha256(text.encode()).hexdigest()
        db.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?, ?, ?)", (ident, text, redactions)
        )
        return ident

    def begin(self, workspace, tool, params, mode=None, task_id=None, before=None, metadata=None):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            params_blob = self._blob(db, params)
            db.execute(
                "INSERT INTO events(id,workspace,tool,cycle_id,task_id,mode,started_at,state,params) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    workspace,
                    tool,
                    params.get("cycle_id"),
                    task_id,
                    mode,
                    now(),
                    "started",
                    params_blob,
                ),
            )
            metadata = metadata or {}
            db.execute(
                "UPDATE events SET instructions_revision=?,instructions_ref=?,runtime_version=?,"
                "procedures_revision=?,operation_id=?,journal_format=2 WHERE id=?",
                (metadata.get("instructions_revision"),
                 json.dumps(metadata["instructions_ref"]) if metadata.get("instructions_ref") else None,
                 metadata.get("runtime_version"), metadata.get("procedures_revision"),
                 metadata.get("operation_id"), ident),
            )
        return ident

    def end(self, ident, workspace, started, before=None, after=None, result=None, error=None,
            references=None, result_bytes=None):
        with self.connect() as db:
            row = db.execute("SELECT tool FROM events WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise PCWError("Unknown analytics operation")
            result_bytes = result_bytes if result_bytes is not None else (
                len(json.dumps(result, ensure_ascii=False).encode())
                if result is not None
                else 0
            )
            # Do not recursively copy the journal into itself when it is queried.
            logged_result = (
                {"analytics_response_bytes": result_bytes}
                if row["tool"].startswith("analytics_")
                else result
            )
            db.execute(
                "UPDATE events SET finished_at=?, duration_ms=?, state=?, result=?, error=?, result_bytes=? WHERE id=?",
                (
                    now(),
                    (time.monotonic() - started) * 1000,
                    "error" if error is not None else "completed",
                    self._blob(db, logged_result),
                    self._blob(db, error) if error is not None else None,
                    result_bytes,
                    ident,
                ),
            )
            if isinstance(result, dict):
                db.execute(
                    "UPDATE events SET cycle_id=coalesce(cycle_id,?),task_id=coalesce(task_id,?),"
                    "operation_id=coalesce(operation_id,?) WHERE id=?",
                    (result.get("cycle_id"), result.get("task_id"),
                     result.get("proposal_id") or (result.get("history") or {}).get("operation_id"), ident),
                )
            db.executemany("INSERT OR REPLACE INTO event_references VALUES (?,?,?)",
                           [(ident, i, json.dumps(ref)) for i, ref in enumerate(references or [])])

    def query(self, workspace=None, cycle_id=None, task_id=None, tool=None,
              since=None, until=None, offset=0, limit=50, import_id=None,
              workspaces=None, instruction_revision=None, runtime_version=None,
              procedures_revision=None, cursor=None):
        from .analytics_queries import query_events
        if workspace is not None and workspaces is not None:
            raise PCWError("Select workspace or workspaces, not both")
        result = query_events(self, workspaces=[workspace] if workspace is not None else workspaces,
                              cycle_id=cycle_id, task_id=task_id, tool=tool,
                              since=since, until=until, offset=offset, limit=limit,
                              import_id=import_id, instruction_revision=instruction_revision,
                              runtime_version=runtime_version, procedures_revision=procedures_revision,
                              cursor=cursor)
        return {**result, "tokens": None, "cost": None,
                "measurement_limit": "Recorded tool durations and bytes; not task duration, token savings or causal quality."}

    def read(self, event_id, max_chars=16000, offset=0, limit=50):
        from .history_queries import page
        page(offset, limit)
        if type(max_chars) is not int or not 1 <= max_chars <= 100000:
            raise PCWError("Analytics body budget must be between 1 and 100000 characters")
        with self.connect() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise PCWError("Unknown analytics event")
            result = dict(row)
            remaining = max_chars
            for field in ("params", "result", "error"):
                if result[field]:
                    blob = db.execute(
                        "SELECT id, length(text) AS size, redactions FROM blobs WHERE id=?", (result[field],)
                    ).fetchone()
                    if blob["size"] > remaining:
                        result[field] = {"blob_id": blob["id"], "characters": blob["size"],
                                         "redactions": blob["redactions"], "truncated": True}
                        continue
                    text = db.execute("SELECT text FROM blobs WHERE id=?", (blob["id"],)).fetchone()[0]
                    remaining -= len(text)
                    result[field] = {
                        "blob_id": blob["id"], "value": json.loads(text),
                        "redactions": blob["redactions"],
                        "truncated": False,
                    }
            result["versions"] = [
                dict(value)
                for value in db.execute(
                    "SELECT * FROM versions WHERE event_id=? ORDER BY path,relation LIMIT ? OFFSET ?", (event_id, limit, offset)
                )
            ]
            result["references"] = [
                {"reference_index": row["ordinal"], **json.loads(row["reference"])}
                for row in db.execute("SELECT * FROM event_references WHERE event_id=? ORDER BY ordinal LIMIT ? OFFSET ?", (event_id, limit, offset))
            ]
            result["versions_count"] = db.execute("SELECT count(*) FROM versions WHERE event_id=?", (event_id,)).fetchone()[0]
            result["references_count"] = db.execute("SELECT count(*) FROM event_references WHERE event_id=?", (event_id,)).fetchone()[0]
            total = max(result["versions_count"], result["references_count"])
            result["next_offset"] = offset + limit if offset + limit < total else None
            if result.get("instructions_ref"):
                result["instructions_ref"] = json.loads(result["instructions_ref"])
            result["legacy_snapshot_evidence"] = bool(result["versions_count"])
            return result

    def blob_read(self, blob_id, offset=0, max_chars=16000):
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 100000:
            raise PCWError("Use a nonnegative offset and 1 to 100000 characters")
        with self.connect() as db:
            row = db.execute("SELECT redactions,length(text) AS size,substr(text,?,?) AS fragment FROM blobs WHERE id=?",
                             (offset + 1, max_chars, blob_id)).fetchone()
            if row is None:
                raise PCWError("Unknown analytics body")
            complete = offset == 0 and row["size"] <= max_chars
            return {"id": blob_id, "redactions": row["redactions"], "encoding": "json",
                    "offset": offset, "total_chars": row["size"],
                    "truncated": offset + max_chars < row["size"],
                    "next_offset": offset + max_chars if offset + max_chars < row["size"] else None,
                    **({"value": json.loads(row["fragment"])} if complete else {"text": row["fragment"]})}


class AnalyticsMixin(AnalyticsMigrationMixin):
    def _events(self):
        return EventStore(self.engine.work_root)

    def analytics_query(
        self,
        target_workspace: str | None = None,
        cycle_id: str | None = None,
        task_id: str | None = None,
        tool: str | None = None,
        since: str | None = None,
        until: str | None = None,
        offset: int = 0,
        limit: int = 50,
        import_id: str | None = None,
        workspaces: list[str] | None = None,
        instruction_revision: str | None = None,
        runtime_version: str | None = None,
        procedures_revision: str | None = None,
        cursor: str | None = None,
    ):
        if target_workspace is None and workspaces is None and cursor is None:
            target_workspace = self.engine.workspace
        return self._events().query(
            target_workspace,
            cycle_id,
            task_id,
            tool,
            since,
            until,
            offset,
            limit,
            import_id,
            workspaces, instruction_revision, runtime_version, procedures_revision, cursor,
        )

    def analytics_report(
        self, target_workspace: str | None = None, workspaces: list[str] | None = None,
        cycle_id: str | None = None, task_id: str | None = None,
        tool: str | None = None, since: str | None = None, until: str | None = None,
        import_id: str | None = None, instruction_revision: str | None = None,
        runtime_version: str | None = None, procedures_revision: str | None = None,
        max_events: int = 1000, cursor: str | None = None, max_blob_bytes: int = 4000000,
    ):
        from .analytics_queries import analytics_report
        if target_workspace is not None and workspaces is not None:
            raise PCWError("Select target_workspace or workspaces, not both")
        if target_workspace is None and workspaces is None and cursor is None:
            target_workspace = self.engine.workspace
        return analytics_report(
            self._events(), workspaces=[target_workspace] if target_workspace is not None else workspaces,
            cycle_id=cycle_id, task_id=task_id, tool=tool, since=since, until=until,
            import_id=import_id, instruction_revision=instruction_revision,
            runtime_version=runtime_version, procedures_revision=procedures_revision,
            max_events=max_events, cursor=cursor, max_blob_bytes=max_blob_bytes,
        )

    def analytics_read(self, event_id: str, max_chars: int = 16000, offset: int = 0, limit: int = 50):
        return self._events().read(event_id, max_chars, offset, limit)

    def analytics_blob_read(self, blob_id: str, offset: int = 0, max_chars: int = 16000):
        return self._events().blob_read(blob_id, offset, max_chars)

    def analytics_reference_read(self, event_id: str, reference_index: int = 0,
                                 offset: int = 0, max_chars: int = 16000):
        from .history_queries import excerpt
        from .runtime import _parse
        if type(reference_index) is not int or reference_index < -1:
            raise PCWError("Select an existing reference_index; -1 selects the recorded instructions")
        event = self._events().read(event_id, 1, max(reference_index, 0), 1)
        refs = event["references"]
        if reference_index == -1:
            reference = event.get("instructions_ref")
        elif refs and refs[0]["reference_index"] == reference_index:
            reference = refs[0]
        else:
            raise PCWError("Select an existing reference_index; -1 selects the recorded instructions")
        if not reference or reference.get("status") != "versioned":
            return {"status": "unavailable", "reference": reference,
                    "reason": "This observed version has no verified Git reference"}
        owner = reference.get("workspace", self.engine.workspace)
        other = None
        try:
            runtime = self
            if owner != self.engine.workspace:
                other = type(self)(self.engine.work_root, owner)
                runtime = other
            path = reference["path"]
            runtime._history_paths([path])
            commit = runtime._history_revision(reference["commit"])
            raw = runtime._history_git("show", commit + ":" + path).stdout
            if hashlib.sha256(raw.encode()).hexdigest() != reference.get("content_sha256"):
                raise PCWError("Historical content does not match the recorded reference")
            text = _parse(raw)[1] if reference.get("projection") == "body" else raw
            if "returned_characters" in reference:
                text = text[reference.get("offset", 0):][:reference["returned_characters"]]
            expected = reference.get("returned_sha256")
            if expected and hashlib.sha256(text.encode()).hexdigest() != expected:
                raise PCWError("Historical projection does not match the returned content")
            safe, redactions = redact(text)
            return {"status": "available", "reference": reference, "redactions": redactions,
                    **excerpt(safe, offset, max_chars)}
        finally:
            if other is not None:
                other.close()

    def analytics_evaluate(self, criterion: str, assessment: str, event_ids: list[str],
                           scope: dict | None = None, instruction_basis: list[dict] | None = None):
        from .analytics_queries import validate_assessment_scope
        if not criterion.strip() or not assessment.strip() or not event_ids:
            raise PCWError(
                "Evaluation requires a criterion, assessment and evidence event IDs"
            )
        events = self._events()
        evidence = validate_assessment_scope(events, event_ids, scope, instruction_basis)
        scope, instruction_basis = evidence["scope"], evidence["instruction_basis"]
        ident = uuid.uuid4().hex
        with events.connect() as db:
            for event_id in event_ids:
                if not db.execute(
                    "SELECT 1 FROM events WHERE id=?", (event_id,)
                ).fetchone():
                    raise PCWError("Evaluation references an unknown event")
            clean, redactions = redact(
                {"criterion": criterion, "assessment": assessment}
            )
            db.execute(
                    "INSERT INTO evaluations(id,created_at,criterion,assessment,evidence,scope,instruction_basis) VALUES (?,?,?,?,?,?,?)",
                (
                    ident,
                    now(),
                    clean["criterion"],
                    clean["assessment"],
                    json.dumps(event_ids),
                    json.dumps(redact(scope)[0]) if scope is not None else None,
                    json.dumps(redact(instruction_basis)[0]) if instruction_basis is not None else None,
                ),
            )
        return {
            "evaluation_id": ident,
            "status": "saved",
            "redactions": redactions,
            "kind": "analysis_not_observed_fact",
        }

    def analytics_import(
        self,
        source_path: str,
        adapter: str,
        user_request: str,
        session_id: str | None = None,
    ):
        """Import one explicitly selected transcript. Never discover other sessions."""
        from .session_import import read_session

        if not isinstance(user_request, str) or not user_request.strip():
            raise PCWError("An explicit transcript import request is required")
        path = Path(source_path)
        if not path.is_absolute() or not path.is_file():
            raise PCWError("Select an existing absolute transcript path")
        if path.stat().st_size > 100 * 1024 * 1024:
            raise PCWError(
                "Transcript exceeds 100 MiB; select an explicit smaller transcript export"
            )
        parsed = read_session(path, adapter, session_id)
        store = self._events()
        # Re-importing a growing session adds new events, not duplicates of old ones.
        ident = hashlib.sha256(
            json.dumps([adapter, parsed["session_id"], str(path.resolve())]).encode()
        ).hexdigest()
        inserted = 0
        with store.connect() as db:
            for record in parsed["records"]:
                clean, _ = redact(record)
                event_id = hashlib.sha256(
                    json.dumps(
                        [ident, clean], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()
                timestamp = (
                    record["timestamp"]
                    if isinstance(record["timestamp"], str)
                    else "unknown"
                )
                blob = store._blob(
                    db,
                    {
                        "transcript_record": record,
                        "session_id": parsed["session_id"],
                        "correlation": "unmatched; no actor or MCP event inferred from timestamps",
                    },
                )
                cursor = db.execute(
                    "INSERT OR IGNORE INTO events(id,workspace,tool,mode,started_at,state,result,source,source_event_id,import_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        self.engine.root.name,
                        "session:" + str(record["kind"]),
                        "import",
                        timestamp,
                        "imported",
                        blob,
                        adapter,
                        record["id"],
                        ident,
                    ),
                )
                inserted += cursor.rowcount
            db.execute(
                "INSERT INTO imports VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET imported_at=excluded.imported_at,records=excluded.records",
                (
                    ident,
                    adapter,
                    str(path),
                    parsed["session_id"],
                    now(),
                    len(parsed["records"]),
                ),
            )
        return {
            "import_id": ident,
            "session_id": parsed["session_id"],
            "imported_events": inserted,
            "recognized_records": len(parsed["records"]),
            "ignored_records": parsed["ignored"],
            "status": "imported",
            "scope": "selected transcript only; its workspace association is analytical, not proven provenance",
        }
