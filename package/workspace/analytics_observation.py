"""Operation evidence references exact Git content; never snapshots the workspace."""

from functools import lru_cache
import hashlib
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files

from .runtime import _parse


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def runtime_identity():
    try:
        release = version("contextopus-core")
    except PackageNotFoundError:
        release = "unpackaged"
    hasher = hashlib.sha256()

    def visit(root, prefix=""):
        for item in sorted(root.iterdir(), key=lambda p: p.name):
            name = prefix + item.name
            if item.is_dir():
                visit(item, name + "/")
            elif item.name.endswith(".md"):
                hasher.update(name.encode() + b"\0" + item.read_bytes())

    visit(files(__package__).joinpath("resources/procedures"))
    return release, hasher.hexdigest()


def omitted(text, reason="not_retained"):
    return {"retained": False, "sha256": digest(text), "characters": len(text), "reason": reason}


class Observation:
    """References are verified against bytes returned, not inferred from HEAD/time."""

    def __init__(self, runtime, operation, arguments):
        self.runtime, self.operation, self.arguments = runtime, operation, arguments
        self._references = []
        self.settings_reference = None
        release, procedures = runtime_identity()
        self.metadata = {"runtime_version": release, "procedures_revision": procedures}
        path = runtime.engine.root / "INSTRUCTIONS.md"
        runtime.engine._assert_safe(path)
        if operation in {"settings_read", "settings_preview", "settings_apply"}:
            self.metadata["instructions_ref"] = {"status": "unknown", "path": "INSTRUCTIONS.md",
                "reason": "operation_does_not_read_instructions"}
        elif path.is_file() or (runtime.engine.root / "Context.md").is_file():
            text = runtime._instructions()["content"]
            reference = self._reference(runtime, "INSTRUCTIONS.md", text, "file")
            self.metadata.update(instructions_revision=digest(text), instructions_ref=reference)
        from .history_queries import SETTINGS_HISTORY_PATH
        try:
            text = runtime._settings_history_text()
            reference = self._reference(runtime, SETTINGS_HISTORY_PATH, text, "file")
            reference["role"] = "workspace_settings"
            self.settings_reference = reference
            self.metadata.update(settings_revision=digest(text), settings_ref=reference)
            if "instructions_ref" in self.metadata:
                # One historical basis binds the free text and its separate policy revision.
                self.metadata["instructions_ref"].update(settings_revision=digest(text), settings_ref=reference)
        except Exception:
            # Old events or unavailable canonical settings stay explicitly unknown.
            self.metadata.update(settings_revision=None, settings_ref=None)

    @property
    def references(self):
        # Returned-material indexes retain their existing meaning; the separate
        # observed policy basis follows them in the evidence reference list.
        return self._references + ([self.settings_reference] if self.settings_reference else [])

    def _reference(self, runtime, path, text, projection, revision=None, commit=None):
        reference = {"workspace": runtime.engine.workspace, "path": path,
                     "projection": projection, "returned_sha256": digest(text),
                     "characters": len(text), "revision": revision}
        try:
            if commit:
                full = runtime._history_git("show", commit + ":" + path).stdout
            else:
                from .history_queries import SETTINGS_HISTORY_PATH
                if path == SETTINGS_HISTORY_PATH:
                    full = runtime._settings_history_text()
                elif path == "INSTRUCTIONS.md":
                    full = runtime._instructions()["content"]
                else:
                    target = runtime.engine.root / path
                    runtime.engine._assert_safe(target)
                    full = target.read_bytes().decode("utf-8")
            expected = _parse(full)[1] if projection == "body" else full
            if expected != text:
                return {**reference, "status": "unversioned", "reason": "returned_content_differs"}
            return {**runtime._history_content_reference(path, digest(full), ref=commit or "HEAD"),
                    **reference}
        except Exception:
            # An unavailable reference does not turn the operation into a failure.
            return {**reference, "status": "unversioned", "reason": "git_reference_unavailable"}

    def _material(self, value):
        path, text = value.get("path"), value.get("content")
        if not isinstance(path, str) or not isinstance(text, str):
            return None
        if path not in {"INSTRUCTIONS.md", "PROJECT.md", "Context.md"} and not path.startswith("knowledge/"):
            return None
        owner = value.get("workspace", self.runtime.engine.workspace)
        other = None
        try:
            runtime = self.runtime
            if owner != runtime.engine.workspace:
                other = type(runtime)(runtime.engine.work_root, owner)
                runtime = other
            projection = "body" if path.startswith("knowledge/") or path == "Context.md" else "file"
            reference = self._reference(runtime, path, text, projection, value.get("revision"))
            reference.update(material_id=value.get("id"), role="returned_material")
            self._references.append(reference)
            return {"reference_index": len(self._references) - 1, **reference}
        finally:
            if other is not None:
                other.close()

    def payload(self, value):
        """Keep request/result structure and IDs, replace Markdown copies with refs."""
        if isinstance(value, list):
            return [self.payload(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = self._material(value)
        result = {}
        for key, item in value.items():
            if key == "content" and reference is not None:
                result[key] = {"retained": False, "reference_index": reference["reference_index"],
                               "sha256": reference["returned_sha256"], "characters": reference["characters"]}
            elif isinstance(item, str) and key in {
                "content", "text", "body", "diff", "excerpt", "before", "after",
                "context_before", "context_after", "instructions", "project_content",
            }:
                result[key] = omitted(item)
            else:
                result[key] = self.payload(item)
        return result

    def historical_payload(self, result):
        """Historical read pages retain the exact commit/projection/range separately."""
        if self.operation != "history_read" or not isinstance(result, dict):
            return self.payload(result)
        commit = result.get("commit")
        clean = self.payload(result)
        for item in result.get("materials", []):
            if not commit or not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            path = item["path"]
            full = self.runtime._history_git("show", commit + ":" + path).stdout
            reference = self._reference(self.runtime, path, full, "file", commit=commit)
            reference.update(role="historical_read", offset=item.get("offset", 0),
                             returned_characters=len(item["text"]), returned_sha256=digest(item["text"]))
            self._references.append(reference)
        return clean
