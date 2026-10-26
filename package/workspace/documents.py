"""Read the canonical project record through the common record engine."""

from .runtime import PCWError


class DocumentsMixin:
    def preparation_read(self, agent: str | None = None, agent_model: str | None = None):
        """Read effective policy, complete rules and the project without a cycle."""
        self._require_format()
        with self.engine.read_guard():
            return {"settings": self.settings_read(fields=["reread_instructions", "allow_implicit_updates"],
                                                   agent=agent, agent_model=agent_model),
                    "instructions": self._instructions(), "project": self.project_read()}

    def project_read(self):
        self._require_format()
        with self.engine.read_guard():
            items = [m for m in self.engine.materials.values() if m.metadata.get("type") == "project"]
            if len(items) > 1:
                raise PCWError("Several project descriptions exist; select and consolidate through a record proposal")
            if not items:
                return {"status": "missing", "workspace": self.engine.workspace}
            result = self.engine._result(items[0], content=True)
            return {**result, "record_status": result.get("status"), "status": "present"}

    def record_types(self):
        self._require_format()
        with self.engine.read_guard():
            counts = {}
            for material in self.engine.materials.values():
                kind = material.metadata.get("type", "knowledge")
                counts[kind] = counts.get(kind, 0) + 1
            return {"types": [{"type": kind, "count": counts[kind]} for kind in sorted(counts)]}
