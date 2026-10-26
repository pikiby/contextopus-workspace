"""Prepare complete Context.md bindings using the Core instruction library."""
from typing import TypedDict

from .context_document import library
from .records import record_revision
from .runtime import PCWError


class NewInstruction(TypedDict):
    __pydantic_config__ = {"extra": "forbid"}

    path: str
    title: str
    description: str
    content: str


class InstructionBindingsMixin:
    def instruction_bind_prepare(
        self, cycle_id: str, role: str, instruction_id: str,
        expected_revision: str, new_instruction: NewInstruction | None = None,
        proposal_id: str | None = None, *, expected_task_revision: str,
    ):
        """Select an existing instruction or stage its creation with the binding."""
        with self._cycle(cycle_id) as (_, cycle), self.engine._guard():
            self._require_work(cycle)
            conflict = self._task_conflict(cycle_id, cycle, expected_task_revision)
            if conflict is not None:
                return conflict
            projects = [m for m in self.engine.materials.values() if m.path == "Context.md"]
            if len(projects) != 1:
                raise PCWError("Expected one canonical Context.md")
            project = projects[0]
            if record_revision(project) != expected_revision:
                raise PCWError("Context.md changed; read its current revision")
            store = library(self)
            prepared = []
            if new_instruction is not None:
                prepared.append(store.prepare_create(
                    new_instruction["path"],
                    {"id": instruction_id, "type": "instruction",
                     "title": new_instruction["title"], "description": new_instruction["description"]},
                    new_instruction["content"],
                ))
            bound = store.bind(project.metadata, role, instruction_id, prepared=prepared)
            return self._prepare_proposal(
                cycle_id, [{"material_id": project.id, "expected_revision": expected_revision,
                            "instructions": bound["metadata"]["instructions"]}],
                proposal_id=proposal_id, summary="Bind context instruction: " + role,
                instruction_documents=prepared,
                expected_task_revision=expected_task_revision,
            )
