"""Workspace coordination survives loss of the workspace's derived runtime.

This is passive state behind explicit MCP calls, not an agent dispatcher.
"""

from __future__ import annotations

import contextlib
from contextopus.core import locking as fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unicodedata


class RecoveryRequired(ValueError):
    def __init__(self, workspace: Path, missing: list[str]):
        self.details = {
            "status": "recovery_required",
            "workspace": str(workspace),
            "missing": missing,
            "next_action": "recover_workspace",
            "reason": "Workspace storage disappeared; explicit recovery is required.",
        }
        super().__init__(self.details["reason"] + " Missing: " + ", ".join(missing))


_stores: dict[tuple[str, str], "ControlStore"] = {}
_stores_lock = threading.Lock()


def control_store(work_root: str | Path, workspace: str) -> "ControlStore":
    from .layout import WorkspaceLayout as StorageLayout

    root = StorageLayout.resolve(work_root).root
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("work_root must be an existing absolute directory")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", workspace):
        raise ValueError("workspace must be one safe directory name")
    key = (str(root.resolve()), workspace)
    with _stores_lock:
        if key not in _stores:
            _stores[key] = ControlStore(Path(key[0]), workspace)
        return _stores[key]


class ControlStore:
    def __init__(self, work_root: Path, workspace: str):
        self.work_root = work_root
        self.workspace = workspace
        from .layout import WorkspaceLayout as StorageLayout

        layout = StorageLayout(work_root)
        self.layout = layout
        self.root = (
            work_root / ".pcw" / ".control" / workspace
            if layout.legacy
            else layout.control(workspace)
        )
        self._mutex = threading.RLock()
        self._depth = 0
        from contextopus.core.module_storage import ModuleStorage
        self.storage = ModuleStorage(layout).for_module("workspace").native()

    def safe(self, path: Path):
        if not path.is_relative_to(self.root):
            raise ValueError("Control path escapes its workspace")
        for candidate in (path, *path.parents):
            if candidate == self.work_root:
                break
            if candidate.is_symlink():
                raise ValueError(
                    f"Symlinks are not allowed in control paths: {candidate}"
                )

    @contextlib.contextmanager
    def guard(self):
        with self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            # Equivalent portable names must share admission while creation
            # checks the root catalog, even on a case-sensitive host.
            resource = "context:" + unicodedata.normalize("NFKC", self.workspace).casefold()
            with self.storage.guard(resource):
                self.safe(self.root / "lock")
                self.root.mkdir(parents=True, exist_ok=True)
                # Keep the historical per-context lock too, so an older module
                # cannot write the same control files across an upgrade boundary.
                with (self.root / "lock").open("a") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    self._depth = 1
                    try:
                        yield
                    finally:
                        self._depth = 0
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def write(self, path: Path, content: str):
        self.safe(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self.root,
                prefix=".control-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            from contextopus.core.storage import sync_directory

            sync_directory(self.root)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def reservations(self) -> dict[str, dict]:
        path = self.root / "material-ids.json"
        self.safe(path)
        return json.loads(path.read_text()) if path.exists() else {}

    def save_reservations(self, value: dict[str, dict]):
        self.write(
            self.root / "material-ids.json", json.dumps(value, sort_keys=True) + "\n"
        )
