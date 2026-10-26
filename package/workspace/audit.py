"""Deterministic source checks. Semantic judgments belong to the audit skill."""

from __future__ import annotations

from typing import Literal

from .runtime import PCWError, _id, _parse, _revision
from .records import validate_description
from .operation_types import Offset, PageLimit


class AuditMixin:
    def audit(
        self, cycle_id: str | None = None, offset: Offset = 0, limit: PageLimit = 50,
        scope: Literal["all"] | None = None, request_id: str | None = None,
        cursor: str | None = None, scope_id: str | None = None,
        delta_refresh: bool = False, delta_cursor: str | None = None,
    ):
        # Audit must inspect repairable source defects rather than requiring the
        # very checks it reports to pass before enumeration.
        if any(value is not None for value in (scope, request_id, cursor, scope_id, delta_cursor)) or delta_refresh:
            from .review_scopes import ReviewScopes
            # A supplied cycle is checked, but its presence neither creates a
            # temporary folder nor determines the immutable inventory bounds.
            if cycle_id:
                with self._cycle(cycle_id):
                    pass
            return ReviewScopes(self).page(
                "audit", scope=scope, request_id=request_id, cursor=cursor,
                scope_id=scope_id, delta_refresh=delta_refresh, delta_cursor=delta_cursor,
                offset=offset, limit=limit,
            )
        self._require_format(storage=False)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise PCWError("Use a nonnegative offset and limit between 1 and 100")
        with (
            self._cycle(cycle_id) if cycle_id else self.control.guard(),
            self.engine.read_guard(),
        ):
            paths = sorted(self.engine.knowledge.rglob("*.md"))
            by_id, bodies, rows = {}, {}, []
            for path in paths:
                self.engine._assert_safe(path)
                ident = path.name
                text = path.read_bytes().decode("utf-8")
                issues = []
                try:
                    metadata, body = _parse(text)
                    ident = _id(metadata["id"])
                    links = self.engine.link_codec().decode(metadata.get("links", []), ident)
                    try:
                        validate_description(metadata, required=True)
                    except PCWError:
                        issues.append({
                            "problem": "missing_description" if not metadata.get("description") else "invalid_description",
                            "basis": "Read the entire material and author its content and applicability description through an ordinary proposal",
                        })
                    digest = _revision(body)
                    if digest in bodies and body.strip():
                        issues.append(
                            {
                                "problem": "duplicate_body",
                                "related_material": bodies[digest],
                                "fragment": body,
                                "basis": "Literal body equality; semantic merge is not inferred",
                            }
                        )
                    bodies[digest] = ident
                except Exception as exc:
                    body, links = text, []
                    issues.append(
                        {
                            "problem": "invalid_material",
                            "fragment": text,
                            "basis": str(exc),
                        }
                    )
                if ident in by_id:
                    issues.append(
                        {
                            "problem": "duplicate_id",
                            "fragment": ident,
                            "basis": "YAML IDs must be unique across contexts",
                            "related_path": by_id[ident],
                        }
                    )
                by_id[ident] = path.relative_to(self.engine.root).as_posix()
                rows.append(
                    {
                        "material_id": ident,
                        "path": by_id[ident],
                        "revision": _revision(body),
                        "file_revision": _revision(text),
                        "links": links,
                        "issues": issues,
                    }
                )
            selected = rows[offset : offset + limit]
            for row in selected:
                for target in row.pop("links"):
                    if target not in self.engine.catalog:
                        row["issues"].append(
                            {
                                "problem": "missing_link_target",
                                "fragment": target,
                                "basis": "No material with this YAML ID exists in compatible contexts",
                            }
                        )
                for issue in row["issues"]:
                    issue["verification"] = "observed"
            return {
                "materials": selected,
                "total": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None,
                "scope": "literal format, descriptions, YAML identity, exact duplicates and cross-context link existence",
                "semantic_audit": "Agent must assess requested meaning against current instructions and evidence; no automatic semantic verdict",
            }
