"""Local permanent-data history with immutable snapshots for failed-commit retries."""

from __future__ import annotations

from .text_io import read_utf8

import json
import os
from pathlib import Path
import re
import subprocess

from .runtime import PCWError, _id, _revision
from .policies import policies
from .workspaces import encode
from .history_events import event_from_plan, commit_message
from .history_queries import HistoryQueriesMixin, excerpt, PERMANENT_PATHS, SETTINGS_HISTORY_PATH
from .history_catalog import HistoryCatalogMixin


class HistoryMixin(HistoryCatalogMixin, HistoryQueriesMixin):
    def _history_root(self):
        path = self.control.root / "history"
        self.control.safe(path)
        return path

    def _permanent_files(self):
        result = {}
        for path in [
            self.engine.root / "INSTRUCTIONS.md",
            self.engine.root / "PROJECT.md",
            *sorted(self.engine.knowledge.rglob("*.md")),
        ]:
            self.engine._assert_safe(path)
            if path.is_file():
                result[path.relative_to(self.engine.root).as_posix()] = (
                    path.read_bytes().decode("utf-8")
                )
        from .settings import configuration_text
        configuration = configuration_text(self.control.layout, self.engine.workspace)
        from .configuration import WORKSPACE_POLICY_DEFAULTS
        if configuration is not None and set(WORKSPACE_POLICY_DEFAULTS) <= json.loads(configuration).keys():
            result[SETTINGS_HISTORY_PATH] = self._settings_history_text()
        return result

    def _settings_history_text(self):
        """Historical representation only; live settings exist solely in Core."""
        virtual = self.engine.root / SETTINGS_HISTORY_PATH
        self.engine._assert_safe(virtual)
        if virtual.exists():
            raise PCWError("Reserved historical settings path must not contain a live file")
        return encode(self._settings_snapshot())

    def _history_git(self, *args, text=None, index=None, check=True):
        self.engine._assert_safe(self.engine.root / ".git")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        if index is not None:
            self.control.safe(index)
            env["GIT_INDEX_FILE"] = str(index)
        command = [
            "git",
            "--literal-pathspecs",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "user.name=PCW",
            "-c",
            "user.email=pcw@localhost",
            "-C",
            str(self.engine.root),
            *args,
        ]
        result = subprocess.run(
            command,
            input=text.encode("utf-8") if text is not None else None,
            capture_output=True,
            env=env,
            timeout=60,
        )
        result.stdout = result.stdout.decode("utf-8")
        result.stderr = result.stderr.decode("utf-8", errors="replace")
        if check and result.returncode:
            raise PCWError(result.stderr.strip() or "Git history operation failed")
        return result

    def _history_capture(self, operation_id, *, force=False, plan=None):
        """Called under the workspace lock immediately after verified file writes."""
        if (
            not force
            and not self._policies()["git_history_enabled"]
        ):
            return None
        root = self._history_root()
        root.mkdir(parents=True, exist_ok=True)
        queue_path = root / "queue.json"
        self.control.safe(queue_path)
        queue = json.loads(read_utf8(queue_path)) if queue_path.exists() else []
        snapshot_path = root / (_id(operation_id) + ".json")
        self.control.safe(snapshot_path)
        if not snapshot_path.exists():
            data = self._permanent_files()
            snapshot = {
                "operation_id": operation_id,
                "files": data,
                "revision": _revision(encode(data)),
                "status": "pending",
            }
            event = event_from_plan(operation_id, plan, data)
            snapshot.update(event=event, event_revision=_revision(encode(event)))
            self.control.write(snapshot_path, encode(snapshot))
        if snapshot_path.exists() and json.loads(read_utf8(snapshot_path)).get("status") in {"committed", "unchanged"}:
            return operation_id
        if operation_id not in queue:
            queue.append(operation_id)
            self.control.write(queue_path, encode(queue))
        return operation_id

    def _history_record(self, operation_id, *, force=False, plan=None):
        operation = self._history_capture(operation_id, force=force, plan=plan)
        if operation is None:
            return {"status": "disabled"}
        return self._history_flush(operation)

    def _history_flush(self, requested=None, limit=100):
        root = self._history_root()
        queue_path = root / "queue.json"
        self.control.safe(queue_path)
        if not queue_path.exists() and requested is None:
            return {"status": "empty"}
        queue = json.loads(read_utf8(queue_path)) if queue_path.exists() else []
        pending = list(queue)
        receipt = {"status": "empty"}
        for operation_id in queue[:limit]:
            path = root / (_id(operation_id) + ".json")
            self.control.safe(path)
            snapshot = json.loads(read_utf8(path))
            if "files" in snapshot and snapshot["revision"] != _revision(encode(snapshot["files"])):
                raise PCWError(
                    "Stored Git snapshot was changed; inspect before retrying"
                )
            if "event" in snapshot and snapshot.get("event_revision") != _revision(encode(snapshot["event"])):
                raise PCWError("Stored Git provenance was changed; inspect before retrying")
            if snapshot["status"] in {"committed", "unchanged"}:
                self._history_compact_snapshot(path, snapshot)
                pending.remove(operation_id)
                self.control.write(queue_path, encode(pending))
                receipt = {key: snapshot.get(key) for key in ("status", "commit", "operation_id")}
                if requested == operation_id:
                    receipt["catalog"] = self._history_catalog_receipt(snapshot)
                continue
            try:
                if not (self.engine.root / ".git").exists():
                    self._history_git("-c", "init.templateDir=", "init", ".")
                self.engine._own_git()
                exclude = self.engine.root / ".git/info/exclude"
                self.engine._assert_safe(exclude)
                previous = read_utf8(exclude) if exclude.exists() else ""
                additions = [
                    value
                    for value in (
                        "/tmp/",
                        "/.tmp/",
                        "/current/",
                        "/.pcw-runtime/",
                        "/.analytics/",
                    )
                    if value not in previous.splitlines()
                ]
                if additions:
                    self.engine._write(
                        exclude,
                        previous.rstrip("\n") + "\n" + "\n".join(additions) + "\n",
                    )
                head_result = self._history_git(
                    "rev-parse", "--verify", "HEAD", check=False
                )
                head = (
                    head_result.stdout.strip() if head_result.returncode == 0 else None
                )
                # An acknowledgement may be lost after update-ref. Never duplicate that commit.
                if snapshot.get("commit") and head and self._history_git(
                    "merge-base", "--is-ancestor", snapshot["commit"], head, check=False
                ).returncode == 0:
                    snapshot["status"] = "committed"
                else:
                    index = root / (operation_id + ".index")
                    self.control.safe(index)
                    index.unlink(missing_ok=True)
                    self._history_git("read-tree", "--empty", index=index)
                    for relative, text in sorted(snapshot["files"].items()):
                        self._history_paths([relative])
                        blob = self._history_git(
                            "hash-object", "-w", "--no-filters", "--stdin", text=text
                        ).stdout.strip()
                        self._history_git(
                            "update-index",
                            "--add",
                            "--cacheinfo",
                            "100644",
                            blob,
                            relative,
                            index=index,
                        )
                    tree = self._history_git("write-tree", index=index).stdout.strip()
                    index.unlink(missing_ok=True)
                    old_tree = (
                        self._history_git("rev-parse", head + "^{tree}").stdout.strip()
                        if head
                        else None
                    )
                    if tree == old_tree:
                        snapshot.update(status="unchanged", commit=head)
                    else:
                        parent = ["-p", head] if head else []
                        commit = self._history_git(
                            "commit-tree",
                            tree,
                            *parent,
                            text=commit_message(snapshot["event"]) if "event" in snapshot
                            else "PCW permanent data: " + operation_id + "\n",
                        ).stdout.strip()
                        snapshot["commit"] = commit
                        self.control.write(path, encode(snapshot))
                        self._history_git(
                            "update-ref", "HEAD", commit, head or "0" * 40
                        )
                        snapshot["status"] = "committed"
                # Keep the workspace's regular index consistent without changing any file.
                self._history_git("read-tree", "HEAD")
                if self._history_blob("HEAD", SETTINGS_HISTORY_PATH):
                    self._history_git("update-index", "--skip-worktree", "--", SETTINGS_HISTORY_PATH)
                self.control.write(path, encode(snapshot))
                self._history_compact_snapshot(path, snapshot)
                pending.remove(operation_id)
                self.control.write(queue_path, encode(pending))
                catalog = self._history_catalog_receipt(snapshot)
                receipt = {
                    key: snapshot.get(key)
                    for key in ("status", "commit", "operation_id")
                }
                receipt["catalog"] = catalog
            except Exception as exc:
                if snapshot.get("status") in {"committed", "unchanged"}:
                    return {**{key: snapshot.get(key) for key in ("status", "commit", "operation_id")},
                            "cleanup": {"status": "pending", "error": str(exc), "next_action": "history_retry"},
                            "snapshot_preserved": True}
                return {
                    "status": "pending",
                    "operation_id": operation_id,
                    "error": str(exc),
                    "requested_operation": requested,
                    "next_action": "history_retry",
                    "snapshot_preserved": True,
                }
        if requested is not None:
            if receipt.get("operation_id") == requested and "catalog" in receipt:
                return receipt
            path = root / (requested + ".json")
            snapshot = json.loads(read_utf8(path))
            if snapshot["status"] == "pending":
                return {"status": "pending", "operation_id": requested,
                        "remaining_operations": len(pending), "next_action": "history_retry",
                        "snapshot_preserved": True}
            self._history_compact_snapshot(path, snapshot)
            return {**{key: snapshot.get(key) for key in ("status", "commit", "operation_id")},
                    "catalog": self._history_catalog_receipt(snapshot)}
        if pending:
            receipt["remaining_operations"] = len(pending)
            receipt["next_action"] = "history_retry"
        return receipt

    def _history_compact_snapshot(self, path, snapshot):
        """Drop retry bodies only after checking the complete captured Git tree."""
        if "files" not in snapshot:
            return
        commit = self._history_revision(snapshot["commit"])
        paths = self._history_git("ls-tree", "-r", "--name-only", "-z", commit,
                                  "--", *PERMANENT_PATHS).stdout.split("\0")
        if set(filter(None, paths)) != set(snapshot["files"]):
            raise PCWError("Git snapshot paths differ; retry body preserved")
        for relative, text in snapshot["files"].items():
            self._history_paths([relative])
            if self._history_git("show", commit + ":" + relative).stdout != text:
                raise PCWError("Git snapshot content differs; retry body preserved")
        compact = {key: snapshot.get(key) for key in ("status", "commit", "operation_id", "revision")}
        self.control.write(path, encode(compact))

    def _history_catalog_receipt(self, snapshot):
        if snapshot.get("status") != "committed":
            return {"status": "unchanged"}
        try:
            self._history_index_commit(snapshot["commit"])
            return {"status": "indexed"}
        except Exception as exc:
            return {"status": "pending", "error": str(exc), "next_action": "history_index"}

    def history_retry(self, limit: int = 100):
        self._require_format()
        from .history_queries import page
        page(0, limit)
        with self.control.guard():
            return self._history_flush(limit=limit)

    def _history_paths(self, paths):
        if (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(value, str) for value in paths)
        ):
            raise PCWError("Supply exact permanent-data paths")
        for path in paths:
            if path in {"INSTRUCTIONS.md", "PROJECT.md", SETTINGS_HISTORY_PATH}:
                self.engine._assert_safe(self.engine.root / path)
            elif path.startswith("knowledge/"):
                self.engine._knowledge_path(path[len("knowledge/") :])
            else:
                raise PCWError(
                    "History includes INSTRUCTIONS.md, PROJECT.md, knowledge/*.md and .contextopus-settings.json"
                )
        return paths

    def _history_revision(self, commit):
        if not isinstance(commit, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.~^/-]*", commit
        ):
            raise PCWError("Invalid history revision")
        self.engine._own_git()
        return self._history_git(
            "rev-parse", "--verify", "--end-of-options", commit + "^{commit}"
        ).stdout.strip()

    def history_diff(self, before: str, after: str, paths: list[str] | None = None,
                     offset: int = 0, max_chars: int = 16000):
        self._require_format()
        with self.control.guard():
            old, new = self._history_revision(before), self._history_revision(after)
            selected = (
                self._history_paths(paths)
                if paths is not None
                else PERMANENT_PATHS
            )
            return {
                "before": old,
                "after": new,
                **self._history_diff_excerpt(old, new, selected, offset, max_chars),
            }

    def _history_diff_excerpt(self, old, new, selected, offset, max_chars):
        result = excerpt(self._history_git("diff", "--no-ext-diff", "--no-textconv",
                                          old, new, "--", *selected).stdout, offset, max_chars)
        result["diff"] = result.pop("text")
        return result

    def history_read(
        self,
        cycle_id: str | None = None,
        commit: str = "HEAD",
        paths: list[str] | None = None,
        material_id: str | None = None,
        offset: int = 0,
        max_chars: int = 16000,
    ):
        self._require_format()
        with self.control.guard():
            if cycle_id is not None:
                with self._cycle(cycle_id):
                    pass
            revision = self._history_revision(commit)
            if material_id is not None and paths is not None:
                raise PCWError("Select either material_id or paths")
            selected = self._history_record_paths(revision, material_id) if material_id else self._history_paths(paths)
            if len(selected) > 10:
                raise PCWError("Read at most ten exact historical paths per call")
            return {
                "commit": revision,
                "materials": [
                    {
                        "path": path,
                        **excerpt(self._history_git("show", revision + ":" + path).stdout, offset, max_chars),
                    }
                    for path in selected
                ],
            }
