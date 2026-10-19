"""Workspace-owned filesystem conventions on top of the generic Core root."""

from contextopus.core.storage import StorageLayout


class WorkspaceLayout(StorageLayout):
    @property
    def legacy(self):
        return (not (self.root / "modules/workspace/workspace_contexts").is_dir()
                and not (self.configuration / "layout.json").is_file())

    def workspace(self, name):
        self._name(name)
        return self.root / "modules/workspace/workspace_contexts" / name

    def control(self, name):
        self._name(name)
        return self.root / "modules/workspace/control" / name

    @property
    def workspace_base(self):
        return self.root / ".pcw" if self.legacy else self.root / "modules/workspace/workspace_contexts"

    @property
    def workspace_rag(self):
        return self.root / ".pcw/.rag" if self.legacy else self.rag

    @property
    def reservations(self):
        return self.root / "modules/workspace/data/material-ids.json"
