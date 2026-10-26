"""Replayable source inventories for explicitly requested complete reviews.

Only derived manifests and delivery receipts are kept in ControlStore. They
contain no document bodies, semantic judgments, reservations or work cycles.
"""

from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from .context_document import library, record_paths
from .context_identity import context_metadata
from .control import control_store
from .links import LinkCodec
from .records import record_revision, validate_description, validate_metadata
from .runtime import Material, PCWError, _id, _parse
from .sources import declarations


INACTIVITY_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _file_revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _cursor(scope_id, fingerprint, position, kind="catalog", delta_revision=None):
    value = {"scope_id": scope_id, "fingerprint": fingerprint, "position": position, "kind": kind}
    if delta_revision is not None:
        value["delta_revision"] = delta_revision
    return base64.urlsafe_b64encode(_json(value).encode()).decode().rstrip("=")


def _decode_cursor(value):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise ValueError()
        result = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        _id(result["scope_id"])
        if (result.get("kind") not in {"catalog", "delta"}
                or type(result.get("position")) is not int or result["position"] < 0
                or not isinstance(result.get("fingerprint"), str)):
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise PCWError("Invalid review cursor") from exc


class ReviewScopes:
    def __init__(self, runtime):
        self.runtime, self.engine, self.control = runtime, runtime.engine, runtime.control

    def _require_format(self):
        from .workspaces import FORMAT_VERSION
        version = self.runtime._format()
        if version != FORMAT_VERSION:
            raise PCWError(f"Workspace format {version}; required {FORMAT_VERSION}. Use pcw-upgrade; opening does not migrate.")

    def _path(self, scope_id):
        _id(scope_id)
        path = self.control.root / ("review-scope-" + scope_id + ".json")
        self.control.safe(path)
        return path

    def _requests(self):
        path = self.control.root / "review-requests.json"
        self.control.safe(path)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _save(self, manifest):
        manifest["expires_at"] = (_now() + timedelta(days=INACTIVITY_DAYS)).isoformat()
        self.control.write(self._path(manifest["scope_id"]), _json(manifest) + "\n")

    def _prune(self):
        # Request receipts intentionally outlive their disposable manifests:
        # retrying an expired request must not silently create a different scope.
        for path in self.control.root.glob("review-scope-*.json"):
            self.control.safe(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            if datetime.fromisoformat(data["expires_at"]) <= _now():
                path.unlink()

    def _load(self, scope_id):
        self._prune()
        path = self._path(scope_id)
        if not path.is_file():
            return None
        result = json.loads(path.read_text(encoding="utf-8"))
        if result["context"] != self.engine.workspace:
            raise PCWError("Review scope belongs to a different context")
        return result

    def _expired(self, scope_id, operation):
        return {"status": "scope_expired", "scope_id": scope_id,
                "context": self.engine.workspace,
                "next_action": "start_new_review_scope",
                "start_contract": {"operation": "workspace." + operation,
                                   "arguments": {"scope": "all", "request_id": "<fresh request ID>", "limit": 50}},
                "reason": "The derived inventory expired or is missing; source files and work cycles are unchanged."}

    def _source(self, path, workspace):
        """Capture one source, including files that the normal index rejects."""
        row = {"id": None, "path": path.relative_to(self.engine.root.parent / workspace).as_posix(),
               "record_revision": None, "file_revision": None, "issues": []}
        material = None
        try:
            self.engine._assert_safe(path)
            raw = path.read_bytes()
            row["file_revision"] = _file_revision(raw)
            text = raw.decode("utf-8")
            metadata, body = _parse(text)
            if "id" not in metadata:
                row["issues"].append({"problem": "unregistered_material", "basis": "YAML id is missing"})
            else:
                row["id"] = _id(metadata["id"])
                metadata = context_metadata(metadata, workspace)
                material = Material(row["id"], row["path"], metadata, body)
                validate_metadata(metadata)
                declarations(body)
                try:
                    validate_description(metadata, required=True)
                except PCWError as exc:
                    row["issues"].append({"problem": "invalid_description", "basis": str(exc)})
                row["title"] = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), row["id"])
                row["description"] = metadata.get("description", "")
            if raw != path.read_bytes():
                row["issues"].append({"problem": "changed_during_capture", "basis": "Source bytes changed during the read"})
        except (OSError, UnicodeError, PCWError, ValueError) as exc:
            row["issues"].append({"problem": "unreadable_material" if isinstance(exc, OSError) else "invalid_material",
                                  "basis": str(exc)})
        return row, material

    def _capture(self):
        """Read files without requiring a valid derived index or creating one."""
        rows, materials, catalog, owners = [], {}, {}, {}
        for root in sorted(self.engine.root.parent.iterdir()):
            if root.name.startswith(".") or not root.is_dir():
                continue
            try:
                self.engine._assert_safe(root)
                if root != self.engine.root:
                    marker = control_store(self.engine.work_root, root.name).root / "format.json"
                    self.engine._assert_safe(marker)
                    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("format_version") not in (2, 3, 4, 5, 6):
                        continue
                paths = record_paths(root)
            except (OSError, PCWError, ValueError):
                if root == self.engine.root:
                    raise
                continue
            for path in paths:
                row, material = self._source(path, root.name)
                if root == self.engine.root:
                    rows.append(row)
                    materials[row["path"]] = material
                if material is not None:
                    # Duplicate IDs remain explicit rows; they cannot be
                    # credited as an unambiguous full-body read.
                    if material.id in catalog:
                        for existing in rows:
                            if existing["id"] == material.id:
                                existing["issues"].append({"problem": "duplicate_id", "basis": "YAML id is not unique"})
                    else:
                        catalog[material.id], owners[material.id] = material, root.name
        codec = LinkCodec(self.engine.work_root, catalog, owners)
        bodies = {}
        for row in rows:
            material = materials[row["path"]]
            if material is None:
                continue
            try:
                material.metadata["links"] = codec.decode(material.metadata.get("links", []), material.id)
                row["record_revision"] = record_revision(material)
                for target in material.metadata["links"]:
                    if target not in catalog:
                        row["issues"].append({"problem": "missing_link_target", "related_material": target,
                                              "basis": "No source with this YAML id was captured"})
            except (PCWError, ValueError, TypeError) as exc:
                row["issues"].append({"problem": "invalid_links", "basis": str(exc)})
            if material.content.strip() and material.revision in bodies:
                row["issues"].append({"problem": "duplicate_body", "related_path": bodies[material.revision],
                                      "basis": "Literal body equality; no semantic judgment"})
            bodies[material.revision] = row["path"]
        return sorted(rows, key=lambda row: (row["path"], row["id"] or "")), materials

    def _instructions(self):
        path = self.engine.root / "Context.md"
        result = {"references": {}, "documents": [], "issues": []}
        try:
            self.engine._assert_safe(path)
            metadata, _ = _parse(path.read_bytes().decode("utf-8"))
            references = metadata.get("instructions", {})
            if not isinstance(references, dict):
                raise PCWError("Context.md instructions must be a mapping")
            result["references"] = references
            if references:
                resolved = library(self.runtime).resolve(list(references.values()))
                result["documents"] = [{key: value[key] for key in ("id", "path", "revision")}
                                       for value in resolved["instructions"]]
                result["revision"] = resolved["revision"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result["issues"].append({"problem": "required_instructions_unavailable", "basis": str(exc)})
        return result

    @staticmethod
    def _counts(manifest):
        total = len(manifest["entries"])
        catalog_count, body_count = len(manifest["catalog_returned"]), len(manifest["bodies_returned"])
        return {"total": total, "catalog_entries_returned": catalog_count,
                "full_bodies_returned": body_count, "catalog_remaining": total - catalog_count,
                "bodies_remaining": total - body_count}

    def _receipt(self, manifest):
        return {"scope_id": manifest["scope_id"], "request_id": manifest["request_id"],
                "context": manifest["context"], "scope": "all", "bounds": manifest["bounds"],
                "created_at": manifest["created_at"], "expires_at": manifest["expires_at"],
                "manifest_revision": manifest["fingerprint"], **self._counts(manifest)}

    def _begin(self, operation, request_id):
        _id(request_id)
        bounds = {"operation": operation, "context": self.engine.workspace, "scope": "all",
                  "sources": ["Context.md", "knowledge/**/*.md"], "hidden_paths": "excluded"}
        with self.control.guard():
            requests = self._requests()
            previous = requests.get(request_id)
            if previous:
                if previous["bounds"] != bounds:
                    return {"status": "request_conflict", "request_id": request_id,
                            "reason": "The request ID already identifies different review bounds"}
                return self._load(previous["scope_id"]) or self._expired(previous["scope_id"], operation)
        # Source capture can be large. Do not hold the context mutation lock
        # while walking files; captured revisions detect concurrent edits.
        entries, _ = self._capture()
        instructions = self._instructions()
        manifest = {"scope_id": uuid.uuid4().hex, "request_id": request_id,
                    "context": self.engine.workspace, "bounds": bounds,
                    "created_at": _now().isoformat(), "entries": entries,
                    "required_instructions": instructions,
                    "catalog_returned": [], "bodies_returned": [], "deltas": {}, "latest_delta": None}
        manifest["fingerprint"] = _digest({"bounds": bounds, "entries": entries, "instructions": instructions})
        with self.control.guard():
            requests = self._requests()
            previous = requests.get(request_id)
            if previous:
                if previous["bounds"] != bounds:
                    return {"status": "request_conflict", "request_id": request_id,
                            "reason": "The request ID already identifies different review bounds"}
                return self._load(previous["scope_id"]) or self._expired(previous["scope_id"], operation)
            self._save(manifest)
            requests[request_id] = {"scope_id": manifest["scope_id"], "bounds": bounds}
            self.control.write(self.control.root / "review-requests.json", _json(requests) + "\n")
        return manifest

    def page(self, operation, *, scope=None, request_id=None, cursor=None, scope_id=None,
             delta_refresh=False, delta_cursor=None, offset=0, limit=50):
        self._require_format()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PCWError("limit must be between 1 and 100")
        if offset != 0 or type(offset) is not int:
            raise PCWError("Review cursors cannot be mixed with offset")
        if operation not in {"audit", "list"}:
            raise PCWError("Unknown review operation")
        if type(delta_refresh) is not bool:
            raise PCWError("delta_refresh must be boolean")
        continuation = cursor or delta_cursor
        if cursor and delta_cursor:
            raise PCWError("Use one review cursor")
        if continuation:
            if scope is not None or request_id is not None or scope_id is not None or delta_refresh:
                raise PCWError("A cursor cannot be combined with new review bounds")
            decoded = _decode_cursor(continuation)
            if decoded["kind"] != ("delta" if delta_cursor else "catalog"):
                raise PCWError("Use the matching catalog or delta cursor argument")
            scope_id = decoded["scope_id"]
        elif delta_refresh:
            if not scope_id or scope is not None or request_id is not None:
                raise PCWError("delta_refresh requires only scope_id and optional limit")
            decoded = None
        else:
            if scope != "all" or not request_id or scope_id is not None:
                raise PCWError("A complete review requires scope='all' and request_id")
            manifest = self._begin(operation, request_id)
            if "status" in manifest:
                return manifest
            scope_id, decoded = manifest["scope_id"], None
        captured = (self._capture()[0], self._instructions()) if delta_refresh else None
        with self.control.guard():
            manifest = self._load(scope_id)
            if manifest is None:
                return self._expired(scope_id, operation)
            if manifest["bounds"]["operation"] != operation:
                raise PCWError("Review cursor belongs to a different operation")
            if decoded and decoded["fingerprint"] != manifest["fingerprint"]:
                raise PCWError("Review cursor fingerprint does not match the source inventory")
            if delta_refresh or delta_cursor:
                return self._delta_page(manifest, captured, decoded, limit)
            start = decoded["position"] if decoded else 0
            if start > len(manifest["entries"]):
                raise PCWError("Review cursor position is outside the inventory")
            selected = copy.deepcopy(manifest["entries"][start:start + limit])
            positions = range(start, start + len(selected))
            manifest["catalog_returned"] = sorted(set(manifest["catalog_returned"]) | set(positions))
            self._save(manifest)
            next_position = start + len(selected)
            return {"status": "ok", **self._receipt(manifest), "materials": selected,
                    "next_cursor": _cursor(scope_id, manifest["fingerprint"], next_position)
                    if next_position < len(manifest["entries"]) else None,
                    "required_instructions": copy.deepcopy(manifest["required_instructions"]),
                    "semantic_audit": "Counts show returned records and matching full bodies, not comprehension or semantic review."}

    def _delta_page(self, manifest, captured, decoded, limit):
        if decoded:
            delta = manifest["deltas"].get(decoded.get("delta_revision"))
            if delta is None:
                raise PCWError("Unknown delta revision")
            start = decoded["position"]
        else:
            previous = manifest["deltas"].get(manifest["latest_delta"])
            if previous and len(previous["returned"]) < len(previous["entries"]):
                self._save(manifest)
                missing = next(i for i in range(len(previous["entries"])) if i not in previous["returned"])
                return {"status": "delta_in_progress", **self._receipt(manifest),
                        "delta_revision": previous["revision"], "observed_at": previous["observed_at"],
                        "delta_cursor": _cursor(manifest["scope_id"], manifest["fingerprint"], missing, "delta", previous["revision"])}
            old = {item["path"]: item for item in manifest["entries"]}
            new = {item["path"]: item for item in captured[0]}
            changes = []
            for path in sorted(old.keys() | new.keys()):
                before, after = old.get(path), new.get(path)
                if before is not None and after is not None and before == after:
                    continue
                changes.append({"change": "added" if before is None else "deleted" if after is None else "changed",
                                "id": (after or before)["id"], "path": path,
                                "before": before, "after": after})
            observed_at = _now().isoformat()
            instruction_changes = ({"before": manifest["required_instructions"], "after": captured[1]}
                                   if manifest["required_instructions"] != captured[1] else None)
            revision = _digest({"observed_at": observed_at, "entries": changes,
                                "required_instruction_changes": instruction_changes})
            delta = {"revision": revision, "observed_at": observed_at, "entries": changes, "returned": [],
                     "required_instruction_changes": instruction_changes}
            manifest["deltas"][revision] = delta
            manifest["latest_delta"] = revision
            start = 0
        if start > len(delta["entries"]):
            raise PCWError("Delta cursor position is outside its inventory")
        selected = copy.deepcopy(delta["entries"][start:start + limit])
        delta["returned"] = sorted(set(delta["returned"]) | set(range(start, start + len(selected))))
        self._save(manifest)
        position = start + len(selected)
        return {"status": "ok", **self._receipt(manifest), "changes": selected,
                "delta_revision": delta["revision"], "observed_at": delta["observed_at"],
                "required_instruction_changes": copy.deepcopy(delta["required_instruction_changes"]),
                "delta_total": len(delta["entries"]), "delta_entries_returned": len(delta["returned"]),
                "delta_cursor": _cursor(manifest["scope_id"], manifest["fingerprint"], position, "delta", delta["revision"])
                if position < len(delta["entries"]) else None}

    def read_many(self, material_ids, scope_id):
        self._require_format()
        if not isinstance(material_ids, list) or not 1 <= len(material_ids) <= 100:
            raise PCWError("material_ids must contain between 1 and 100 IDs")
        for ident in material_ids:
            _id(ident)
        with self.control.guard():
            manifest = self._load(scope_id)
            if manifest is None:
                return self._expired(scope_id, "audit")
            entries = copy.deepcopy(manifest["entries"])
        current, materials = self._capture()
        by_path = {row["path"]: row for row in current}
        results, returned = [], set()
        for ident in dict.fromkeys(material_ids):
            matches = [(i, row) for i, row in enumerate(entries) if row["id"] == ident]
            if len(matches) != 1:
                results.append({"id": ident, "status": "ambiguous" if matches else "outside_scope", "snapshot_match": False})
                continue
            position, original = matches[0]
            row = by_path.get(original["path"])
            if row is None:
                results.append({"id": ident, "path": original["path"], "status": "missing",
                                "snapshot_match": False, "snapshot_revision": original["record_revision"],
                                "current_revision": None, "revision": None})
                continue
            material = materials.get(row["path"])
            invalid = any(issue["problem"] in {"invalid_material", "unreadable_material", "invalid_links"}
                          for issue in row["issues"])
            valid = material is not None and row["id"] == ident and row["record_revision"] is not None and not invalid
            unchanged = valid and not any(issue["problem"] in {"duplicate_id", "changed_during_capture"} for issue in row["issues"])
            unchanged = unchanged and row["record_revision"] == original["record_revision"] and row["file_revision"] == original["file_revision"]
            # Check the exact bytes again immediately before crediting this read.
            try:
                path = self.engine.root / row["path"]
                self.engine._assert_safe(path)
                current_file_revision = _file_revision(path.read_bytes())
            except (OSError, PCWError):
                current_file_revision = None
            delivery_changed = current_file_revision != row["file_revision"]
            unchanged = unchanged and not delivery_changed
            result = {"id": ident, "path": row["path"], "status": "present" if valid else "invalid",
                      "snapshot_match": bool(unchanged), "snapshot_revision": original["record_revision"],
                      "revision": row["record_revision"], "current_revision": row["record_revision"],
                      "file_revision": row["file_revision"], "current_file_revision": current_file_revision,
                      "snapshot_file_revision": original["file_revision"], "issues": row["issues"]}
            if delivery_changed:
                result.update(status="changed_during_read", current_revision=None)
            if material is not None:
                result["content"] = material.content
                result["metadata"] = material.metadata
                try:
                    result["sources"] = declarations(material.content)
                except PCWError:
                    # The inventory already records malformed source wrappers.
                    result["sources"] = []
            results.append(result)
            if unchanged:
                returned.add(position)
        with self.control.guard():
            manifest = self._load(scope_id)
            if manifest is None:
                return self._expired(scope_id, "audit")
            manifest["bodies_returned"] = sorted(set(manifest["bodies_returned"]) | returned)
            self._save(manifest)
            return {"status": "ok", **self._receipt(manifest), "materials": results}
