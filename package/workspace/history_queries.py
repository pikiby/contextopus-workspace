"""Bounded, read-only navigation of native Git history and record provenance."""

from datetime import datetime, timezone
from .runtime import PCWError, _id, _parse
from .history_events import read_event


SETTINGS_HISTORY_PATH = ".contextopus-settings.json"
PERMANENT_PATHS = ["knowledge/", "INSTRUCTIONS.md", "PROJECT.md", SETTINGS_HISTORY_PATH]


def page(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise PCWError("Use a nonnegative offset and limit between 1 and 100")


def excerpt(text, offset, max_chars):
    if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise PCWError("Use a nonnegative text offset and max_chars between 1 and 100000")
    end = offset + max_chars
    return {"offset": offset, "total_chars": len(text), "truncated": end < len(text),
            "next_offset": end if end < len(text) else None, "text": text[offset:end]}


class HistoryQueriesMixin:
    def _history_head(self, ref):
        if not (self.engine.root / ".git").exists():
            return None, "no_history"
        self.engine._own_git()
        if self._history_git("rev-parse", "--verify", "HEAD", check=False).returncode:
            return None, "no_commits"
        return self._history_revision(ref), "ok"

    def _history_commit(self, commit):
        raw = self._history_git("show", "-s", "--format=%H%x00%P%x00%cI%x00%B", commit).stdout
        ident, parents, timestamp, message = raw.split("\0", 3)
        return {"commit": ident, "parents": parents.split(), "time": timestamp,
                "subject": (message.splitlines() or [""])[0], "event": read_event(message)}

    def _history_versions(self, offset, limit, ref, filters, paths, material_id=None):
        head, status = self._history_head(ref)
        if head is None:
            return {"versions": [], "status": status, "head": None, "next_offset": None}
        commits = self._history_git("log", f"--skip={offset}", f"--max-count={limit + 1}",
                                    "--format=%H", *filters, head, "--", *paths).stdout.splitlines()
        result = []
        for commit in commits[:limit]:
            info = self._history_commit(commit)
            event = info.pop("event")
            info["provenance"] = "declared" if event else "unrecorded"
            info["operation_id"] = event.get("operation_id") if event else None
            info["changed_records"] = len(event["changes"]) if event and event.get("kind") in {"knowledge", "instructions"} else None
            info["transformations"] = [t for t in event.get("transformations", [])
                                       if material_id is None or material_id in t["sources"] + t["targets"]] if event else []
            result.append(info)
        return {"versions": result, "status": status, "head": head,
                "next_offset": offset + limit if len(commits) > limit else None,
                "coverage": "declared record IDs; use path/text search for older commits" if material_id else "Git history"}

    def history_list(self, offset: int = 0, limit: int = 20, material_id: str | None = None,
                     paths: list[str] | None = None, query: str | None = None,
                     since: str | None = None, until: str | None = None, ref: str = "HEAD"):
        """Filter commit metadata; ID lookup follows declarations across renames and deletions."""
        self._require_format()
        page(offset, limit)
        filters = []
        if material_id is not None:
            _id(material_id)
            filters += ["--grep=^Workspace-Record: " + material_id.replace(".", r"\.") + "$", "--all-match"]
        if query is not None:
            if not isinstance(query, str) or not query.strip() or len(query) > 512 or any(ord(c) < 32 for c in query):
                raise PCWError("query must contain 1 to 512 characters")
            # Escape BRE metacharacters; callers supply ordinary text, never Git flags.
            escaped = "".join("\\" + c if c in r"\.[]^$*" else c for c in query)
            filters += ["--grep=" + escaped, "--all-match"]
        for flag, value in (("--max-age", since), ("--min-age", until)):
            if value is not None:
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    raise PCWError("History dates must use ISO format") from None
                filters.append(flag + "=" + str(int(parsed.timestamp())))
        with self.control.guard():
            selected = self._history_paths(paths) if paths is not None else PERMANENT_PATHS
            return self._history_versions(offset, limit, ref, filters, selected, material_id)

    def history_search(self, text: str, paths: list[str] | None = None,
                       offset: int = 0, limit: int = 20, ref: str = "HEAD"):
        """Native Git pickaxe: find commits adding/removing occurrences of exact text."""
        self._require_format()
        page(offset, limit)
        if not isinstance(text, str) or not text or len(text) > 1024 or "\0" in text:
            raise PCWError("text must contain 1 to 1024 characters without NUL")
        with self.control.guard():
            selected = self._history_paths(paths) if paths is not None else PERMANENT_PATHS
            return self._history_versions(offset, limit, ref, ["-S" + text], selected)

    def history_changes(self, commit: str = "HEAD", offset: int = 0, limit: int = 50):
        """Describe a commit, file changes and declared transformations without file bodies."""
        self._require_format()
        page(offset, limit)
        with self.control.guard():
            revision = self._history_revision(commit)
            info = self._history_commit(revision)
            parents = info["parents"]
            args = [parents[0], revision] if parents else ["--root", revision]
            raw = self._history_git("diff-tree", "--no-commit-id", "--no-ext-diff", "--no-textconv",
                                    "--no-renames", "--name-status", "-r", "-z", *args,
                                    "--", *PERMANENT_PATHS).stdout.split("\0")
            files = [{"action": raw[i], "path": raw[i + 1]} for i in range(0, len(raw) - 1, 2)]
            event = info.pop("event")
            changes = event.get("changes", []) if event else []
            transforms = event.get("transformations", []) if event else []
            snapshot_ids = event.get("snapshot_record_ids", []) if event else []
            total = max(len(files), len(changes), len(transforms), len(snapshot_ids))
            return {**info, "provenance": "declared" if event else "unrecorded",
                    "operation_id": event.get("operation_id") if event else None,
                    "cycle_id": event.get("cycle_id") if event else None,
                    "snapshot_record_ids": snapshot_ids[offset:offset + limit],
                    "snapshot_record_ids_count": len(snapshot_ids),
                    "comparison_parent": parents[0] if parents else None,
                    "files": files[offset:offset + limit], "files_count": len(files),
                    "changes": changes[offset:offset + limit], "changes_count": len(changes),
                    "transformations": transforms[offset:offset + limit], "transformations_count": len(transforms),
                    "next_offset": offset + limit if offset + limit < total else None}

    def _history_record_paths(self, revision, material_id):
        ident = _id(material_id)
        result = self._history_git("grep", "-z", "-l", "-F", "-e", ident, revision, "--", "knowledge/", check=False)
        if result.returncode not in (0, 1):
            raise PCWError(result.stderr.strip() or "Git record lookup failed")
        selected = []
        for match in result.stdout.split("\0"):
            if not match:
                continue
            path = match.split(":", 1)[1]
            self._history_paths([path])
            text = self._history_git("show", revision + ":" + path).stdout
            if _parse(text)[0].get("id", path.rsplit("/", 1)[-1]) == ident:
                selected.append(path)
        if not selected:
            # Legacy materials used filename identity and might not contain the ID in their body.
            path = "knowledge/" + ident
            if self._history_git("cat-file", "-e", revision + ":" + path, check=False).returncode == 0:
                selected.append(path)
        if len(selected) != 1:
            raise PCWError("Record ID is absent or ambiguous at this commit; inspect history_changes")
        return selected
