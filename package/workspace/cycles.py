"""Explicit, host-independent PCW cycles over the existing knowledge engine.

Human approval follows independent workspace policies for knowledge and
instructions. This module binds application to an
exact prepared revision; it cannot authenticate the origin of a chat message.
Only cycle metadata survives cleanup. Drafts, returns and proposal bodies do not.
"""

from __future__ import annotations

from .text_io import read_utf8

import contextlib
import copy
import difflib
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import uuid
from typing import Literal, TypedDict, get_type_hints

import yaml

from .runtime import PCWRuntime, PCWError, Material, _id, _revision, _parse
from .shared import SharedRuntime
from .sources import migrate_sources
from .control import RecoveryRequired
from .policies import policies, extract_settings
from .workspaces import WorkspaceMixin
from .migration import FormatMigrationMixin
from .documents import DocumentsMixin
from .records import validate_metadata, validate_description, record_revision
from .history import HistoryMixin
from .history_events import transformations as validate_transformations
from .analytics import AnalyticsMixin
from .audit import AuditMixin
from .temporary_materials import TemporaryMaterialsMixin
from .context_identity import context_metadata
from .edit_reservations import ReservationOperations
from .instruction_bindings import InstructionBindingsMixin
from .operation_types import CandidateLimit, ContextNames, MaterialIDs, Offset, PageLimit, SearchOutput



class ProposalChange(TypedDict, total=False):
    """One final record change. Create with key/content/description; update with
    material_id/expected_revision and changed fields. Delete with only
    material_id/expected_revision/delete:true. Title comes from the Markdown
    heading in content; there is no separate title field. Paths are relative
    to knowledge. Types/statuses are open vocabulary; null status clears it.
    """

    __pydantic_config__ = {"extra": "forbid"}

    key: str
    material_id: str | None
    expected_revision: str
    content: str
    description: str
    links: list[str]
    path: str
    delete: bool
    type: str
    status: str | None
    instructions: dict[str, str]


# The runtime guard and generated operation schema share this contract.
PROPOSAL_CHANGE_FIELDS = frozenset(get_type_hints(ProposalChange))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PCWError(f"{field} must be nonempty text")
    return value


def _sync_directory(path):
    from contextopus.core.storage import sync_directory

    sync_directory(path)


def _knowledge_approval_required(content):
    """Read only the explicit setting, never infer permission from prose.

    Older/custom instructions without the setting retain manual approval.
    """
    return policies(content)["knowledge_approval_required"]


class CycleRuntime(
    InstructionBindingsMixin,
    ReservationOperations,
    TemporaryMaterialsMixin,
    FormatMigrationMixin,
    WorkspaceMixin,
    DocumentsMixin,
    HistoryMixin,
    AnalyticsMixin,
    AuditMixin,
):
    """MCP facade. Direct permanent mutations are deliberately not exposed."""

    def __init__(self, work_root, workspace, **kwargs):
        self.engine = SharedRuntime(
            work_root, workspace, initialize=False, allow_create=False, **kwargs
        )
        self.control = self.engine.control
        self.record_path = self.control.root / "cycles.json"
        self.instruction_reconcile = None

    def close(self):
        self.engine.close()

    def _records(self):
        self.control.safe(self.record_path)
        return (
            json.loads(read_utf8(self.record_path))
            if self.record_path.exists()
            else {"starts": {}, "cycles": {}}
        )

    def _persist(self, records):
        self.control.write(self.record_path, _json(records))

    def _tmp(self, cycle_id):
        path = self.engine.root / ".tmp" / _id(cycle_id)
        self.engine._assert_safe(path)
        legacy = self.engine.root / "tmp" / _id(cycle_id)
        self.engine._assert_safe(legacy)
        if not path.exists() and legacy.exists():
            return legacy
        return path

    def _purge(self, cycle_id):
        path = self._tmp(cycle_id)
        if path.exists():
            # Never follow a link, including a link injected below tmp.
            for entry in path.rglob("*"):
                self.engine._assert_safe(entry)
            shutil.rmtree(path)
            _sync_directory(path.parent)

    @contextlib.contextmanager
    def _cycle(self, cycle_id, states=("active",)):
        _id(cycle_id)
        self.control.safe(self.record_path)
        if not self.record_path.exists():
            raise PCWError(
                "No PCW cycle. Only an explicit user invocation authorizes pcw_startup."
            )
        with self.control.guard():
            records = self._records()
            cycle = records["cycles"].get(cycle_id)
            if cycle is None or cycle["state"] not in states:
                raise PCWError(
                    "Unknown or inactive cycle; do not reuse authorization from an earlier cycle"
                )
            yield records, cycle

    def _instructions(self, initialize=False):
        from .context_document import read_instructions
        current = read_instructions(self)
        if current is not None:
            return current
        path = self.engine.root / "INSTRUCTIONS.md"
        self.engine._assert_safe(path)
        marker = self.control.root / "instructions-initialized.json"
        self.control.safe(marker)
        if not path.exists():
            if not initialize or marker.exists():
                raise PCWError(
                    "INSTRUCTIONS.md is missing; restore the user's instructions, do not reset them to defaults"
                )
            content = (
                files(__package__)
                .joinpath("default_instructions.md")
                .read_text(encoding="utf-8")
            )
            self.engine._write(path, content)
        content = path.read_bytes().decode("utf-8")
        if initialize and not marker.exists():
            self.control.write(marker, _json({"initialized": True}))
        return {
            "path": "INSTRUCTIONS.md",
            "content": content,
            "revision": _revision(content),
        }

    def _require_work(self, cycle):
        if cycle.get("purpose", "work") not in {"work", "rework"}:
            raise PCWError(
                "This cycle is only for instructions; start a work cycle for knowledge operations"
            )
        if (
            cycle.get("invocation") == "implicit"
            and not self._policies()["allow_implicit_updates"]
        ):
            raise PCWError("Implicit updates are disabled by current instructions")

    @staticmethod
    def _task_receipt(cycle):
        request = {
            "user_request": cycle.get("user_request"),
            "clarifications": list(cycle.get("clarifications", [])),
        }
        canonical = json.dumps([request["user_request"], request["clarifications"]],
                               ensure_ascii=False, separators=(",", ":"))
        return {**request, "task_revision": _revision(canonical),
                "clarification_count": len(request["clarifications"])}

    def _task_conflict(self, cycle_id, cycle, expected_revision, clarification_count=None):
        current = self._task_receipt(cycle)
        if expected_revision == current["task_revision"]:
            return None
        if clarification_count is None:
            # Preparation has no stored count yet. Find the exact earlier receipt
            # without changing or normalizing the original request and messages.
            clarification_count = 0
            for count in range(len(current["clarifications"]) + 1) if expected_revision is not None else ():
                previous = {**cycle, "clarifications": current["clarifications"][:count]}
                if self._task_receipt(previous)["task_revision"] == expected_revision:
                    clarification_count = count
                    break
        return {
            "status": "reprepare_required" if expected_revision is None else "task_changed",
            "cycle_id": cycle_id,
            "knowledge_changed": False,
            "prepared_task_revision": expected_revision,
            "current_task_revision": current["task_revision"],
            "new_clarifications": current["clarifications"][clarification_count:],
            **current,
            "next_action": "reprepare_proposal",
            "next_call": {
                "tool": "read", "operation": "workspace.status",
                "arguments": {"cycle_id": cycle_id},
            },
        }

    def startup(
        self,
        request_id: str,
        purpose: str = "work",
        restart_cycle_id: str | None = None,
        invocation: str = "explicit",
        task_id: str | None = None,
        user_request: str | None = None,
    ):
        """One fresh request_id per human invocation; retries reuse that ID."""
        _id(request_id)
        self._require_format(storage=False)
        if purpose not in {"work", "instructions", "audit", "rework"}:
            raise PCWError("purpose must be work, instructions, audit or rework")
        if invocation not in {"explicit", "implicit"}:
            raise PCWError("invocation must be explicit or implicit")
        if invocation == "implicit" and (
            purpose != "work"
            or not self._policies()["allow_implicit_updates"]
        ):
            raise PCWError("This procedure requires an explicit user invocation")
        if task_id is not None:
            if purpose not in {"work", "rework"}:
                raise PCWError("Only work and rework cycles bind a task")
            self.read(material_id=self.engine.effective_task_id(task_id))
        if user_request is not None:
            _text(user_request, "user_request")
        signature = _revision(_json([purpose, restart_cycle_id, invocation, task_id] + ([user_request] if user_request is not None else [])))
        with self.control.guard():
            records = self._records()
            cycle_id = records["starts"].get(request_id)
            if cycle_id:
                cycle = records["cycles"][cycle_id]
                if cycle["start_signature"] != signature:
                    raise PCWError(
                        "request_id already identifies a different invocation"
                    )
                if cycle["state"] != "starting":
                    return {
                        "cycle_id": cycle_id,
                        "status": cycle["state"],
                        "replayed": True,
                        "tmp_cleared": False,
                        "purpose": cycle.get("purpose", "work"),
                        "task_id": self.engine.effective_task_id(cycle.get("task_id")),
                        **self._task_receipt(cycle),
                        **(
                            {"instructions": self._instructions()}
                            if cycle["state"] == "active"
                            else {}
                        ),
                    }
            else:
                if restart_cycle_id is not None:
                    old = records["cycles"].get(_id(restart_cycle_id))
                    if old is None or old["state"] not in {
                        "active",
                        "closing",
                        "cancelled",
                        "closed",
                    }:
                        raise PCWError(
                            "restart_cycle_id must identify the explicitly restarted work"
                        )
                    if self._cleanup_snapshot(restart_cycle_id)["temporary_files"] or any(
                        entry["state"] not in {"applied", "discarded", "no_changes"}
                        for entry in old.get("proposals", {}).values()
                    ):
                        raise PCWError("Existing work has temporary files or pending proposals; continue it or explicitly finish it before restarting")
                    old["state"] = "restarting"
                cycle_id = uuid.uuid4().hex
                cycle = {
                    "state": "starting",
                    "start_signature": signature,
                    "proposals": {},
                    "restart_cycle_id": restart_cycle_id,
                    "purpose": purpose,
                    "invocation": invocation,
                    "task_id": task_id,
                    "user_request": user_request,
                    "clarifications": [],
                    "clarification_requests": {},
                }
                records["starts"][request_id] = cycle_id
                records["cycles"][cycle_id] = cycle
                self._persist(records)
            try:
                self.engine._ensure_storage()
            except RecoveryRequired as exc:
                return {**exc.details, "cycle_id": cycle_id}
            with (
                self.engine.read_guard() if purpose == "audit" else self.engine._guard()
            ):
                instructions = self._instructions()
                if restart_cycle_id is not None:
                    self._purge(restart_cycle_id)
                    records["cycles"][restart_cycle_id] = {
                        "state": "replaced",
                        "proposals": {},
                        "start_signature": records["cycles"][restart_cycle_id][
                            "start_signature"
                        ],
                    }
                self._purge(cycle_id)
                cycle["state"] = "active"
                self._persist(records)
                status = self.engine.status()
                status.pop("active")
                return {
                    "cycle_id": cycle_id,
                    "status": "active",
                    "tmp_cleared": True,
                    "purpose": purpose,
                    "task_id": self.engine.effective_task_id(task_id),
                    "instructions": instructions,
                    "project": self.project_read(),
                    **status,
                    **self._task_receipt(cycle),
                }

    def _call(self, cycle_id, method, **kwargs):
        with self._cycle(cycle_id) as (_, cycle):
            self._require_work(cycle)
            return getattr(self.engine, method)(**kwargs)

    def status(self, cycle_id: str):
        with self._cycle(cycle_id) as (_, cycle):
            outstanding = [
                {"id": key, **value}
                for key, value in cycle["proposals"].items()
                if value["state"] not in {"applied", "discarded", "no_changes"}
            ]
            return {
                "cycle_id": cycle_id,
                "cycle_status": cycle["state"],
                **self._cleanup_snapshot(cycle_id),
                **self._task_receipt(cycle),
                "proposals": outstanding[:50],
                "outstanding_proposal_count": len(outstanding),
                **self.engine.status(),
                "task_id": self.engine.effective_task_id(cycle.get("task_id")),
            }

    def _cleanup_snapshot(self, cycle_id):
        files = {}
        root = self._tmp(cycle_id)
        for path in sorted(root.rglob("*")) if root.exists() else []:
            self.engine._assert_safe(path)
            if path.is_file():
                files[path.relative_to(self.engine.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"cleanup_revision": _revision(_json(files)), "temporary_files": list(files)}

    def cycles_list(self, offset: Offset = 0, limit: PageLimit = 50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise PCWError("Use a nonnegative offset and limit between 1 and 100")
        with self.control.guard():
            items = [{"cycle_id": ident, "status": cycle["state"],
                      "purpose": cycle.get("purpose", "work"),
                      "user_request": cycle.get("user_request"),
                      "outstanding_proposal_count": sum(entry["state"] not in {"applied", "discarded", "no_changes"}
                                                        for entry in cycle.get("proposals", {}).values())}
                     for ident, cycle in self._records()["cycles"].items()
                     if cycle["state"] not in {"closed", "cancelled", "replaced"}]
            return {"cycles": items[offset:offset + limit], "total": len(items)}

    def clarification_add(self, cycle_id: str, text: str, request_id: str):
        _text(text, "text")
        _id(request_id)
        with self._cycle(cycle_id) as (records, cycle):
            requests = cycle.setdefault("clarification_requests", {})
            if request_id in requests:
                if requests[request_id]["text_hash"] != _revision(text):
                    return {"status": "request_conflict", "cycle_id": cycle_id,
                            "request_id": request_id, **self._task_receipt(cycle)}
                return {"cycle_id": cycle_id, "request_id": request_id,
                        "replayed": True, **self._task_receipt(cycle)}
            cycle.setdefault("clarifications", []).append(text)
            requests[request_id] = {"index": len(cycle["clarifications"]) - 1,
                                    "text_hash": _revision(text)}
            self._persist(records)
            return {"cycle_id": cycle_id, "request_id": request_id,
                    "replayed": False, **self._task_receipt(cycle)}

    def _read_call(self, cycle_id, method, **kwargs):
        self._require_format()
        with self._cycle(cycle_id) if cycle_id else contextlib.nullcontext():
            with self.engine.read_guard():
                return getattr(self.engine, method)(**kwargs)

    def list(self, cycle_id: str | None = None, offset: Offset = 0, limit: PageLimit = 50,
             scope: Literal["all"] | None = None, request_id: str | None = None,
             cursor: str | None = None, scope_id: str | None = None,
             delta_refresh: bool = False, delta_cursor: str | None = None):
        if any(value is not None for value in (scope, request_id, cursor, scope_id, delta_cursor)) or delta_refresh:
            from .review_scopes import ReviewScopes
            if cycle_id:
                with self._cycle(cycle_id):
                    pass
            return ReviewScopes(self).page("list", scope=scope, request_id=request_id,
                cursor=cursor, scope_id=scope_id, delta_refresh=delta_refresh,
                delta_cursor=delta_cursor, offset=offset, limit=limit)
        return self._read_call(cycle_id, "list", offset=offset, limit=limit)

    def read(
        self,
        cycle_id: str | None = None,
        material_id: str = "",
        from_id: str | None = None,
        relation_offset: Offset = 0,
        relation_limit: PageLimit = 50,
    ):
        self._require_format()
        with self.engine.read_guard():
            if (
                from_id is not None
                and self.engine.resolve(material_id).id not in self.engine.resolve(from_id).links
            ):
                raise PCWError(
                    "from_id must have an existing link to the requested material"
                )
            return self._read_call(cycle_id, "read", material_id=material_id,
                                   relation_offset=relation_offset, relation_limit=relation_limit)

    def read_many(
        self, cycle_id: str | None = None, material_ids: MaterialIDs | None = None,
        scope_id: str | None = None,
    ):
        if scope_id is not None:
            from .review_scopes import ReviewScopes
            if cycle_id:
                with self._cycle(cycle_id):
                    pass
            return ReviewScopes(self).read_many(material_ids, scope_id)
        return self._read_call(cycle_id, "read_many", material_ids=material_ids)

    def search(
        self,
        cycle_id: str | None = None,
        query: str = "",
        N: int | None = None,
        d_max: float | None = None,
        min_jump: float | None = None,
        multiplier: float | None = None,
        categories: ContextNames | None = None,
        all_categories: bool = False,
        types: list[str] | None = None,
        statuses: list[str] | None = None,
        selection: dict | None = None,
        output: SearchOutput = "materials",
        purpose: str | None = None,
        task_id: str | None = None,
        candidate_limit: CandidateLimit | None = None,
    ):
        return self._read_call(
            cycle_id,
            "search",
            query=query,
            N=N,
            d_max=d_max,
            min_jump=min_jump,
            multiplier=multiplier,
            categories=categories,
            all_categories=all_categories,
            types=types, statuses=statuses,
            selection=selection, output=output, purpose=purpose, task_id=task_id, candidate_limit=candidate_limit,
        )

    def instructions_read(self, cycle_id: str | None = None):
        self._require_format()
        with self._cycle(cycle_id) if cycle_id else self.control.guard():
            return self._instructions()

    def _snapshot(self, material):
        path = self.engine.root / material.path
        self.engine._assert_safe(path)
        text = path.read_bytes().decode("utf-8")
        return {"path": material.path, "text": text, "fingerprint": _revision(text),
                "link_ids": list(material.links)}

    def _plan_path(self, cycle_id, proposal_id):
        path = self._tmp(cycle_id) / "proposals" / (_id(proposal_id) + ".json")
        self.engine._assert_safe(path)
        legacy = self.engine.root / "tmp" / _id(cycle_id) / "proposals" / (_id(proposal_id) + ".json")
        self.engine._assert_safe(legacy)
        if not path.exists() and legacy.exists():
            return legacy
        return path

    def _load_plan(self, cycle_id, proposal_id, entry):
        plan = json.loads(read_utf8(self._plan_path(cycle_id, proposal_id)))
        if _revision(_json(plan)) != entry["revision"]:
            raise PCWError(
                "Prepared proposal was changed; prepare and present it again"
            )
        review = self._plan_path(cycle_id, proposal_id).with_suffix(".md")
        self.engine._assert_safe(review)
        if not review.exists():
            for temporary_root in (".tmp", "tmp"):
                alternate = self.engine.root / temporary_root / _id(cycle_id) / "proposals" / (_id(proposal_id) + ".md")
                self.engine._assert_safe(alternate)
                if alternate.exists():
                    review = alternate
                    break
        if read_utf8(review) != self._review(plan):
            raise PCWError("Prepared review was changed; prepare and present it again")
        return plan

    def _proposal_id(self, cycle_id, cycle, proposal_id, kind):
        if proposal_id is None:
            return uuid.uuid4().hex
        previous = cycle["proposals"].get(_id(proposal_id))
        if previous is None or previous["state"] not in {
            "prepared",
            "discarded",
            "no_changes",
        }:
            raise PCWError("Only an unapplied proposal may be replaced")
        plan = self._load_plan(cycle_id, proposal_id, previous)
        if plan.get("kind", "knowledge") != kind:
            raise PCWError("Cannot change a proposal's kind")
        return proposal_id

    def _save_plan(self, records, cycle, plan, state):
        revision = _revision(_json(plan))
        review = self._review(plan)
        path = self._plan_path(plan["cycle_id"], plan["proposal_id"])
        self.engine._write(path, _json(plan))
        self.engine._write(path.with_suffix(".md"), review)
        if (
            json.loads(read_utf8(path)) != plan
            or read_utf8(path.with_suffix(".md")) != review
        ):
            raise PCWError("Proposal write verification failed")
        cycle["proposals"][plan["proposal_id"]] = {"revision": revision, "state": state}
        self._persist(records)
        effects = self._effects(plan)
        return {
            "proposal_id": plan["proposal_id"],
            "revision": revision,
            "status": state,
            **({"task_revision": plan["task_revision"],
                "clarification_count": plan["clarification_count"]} if "task_revision" in plan else {}),
            "kind": plan.get("kind", "knowledge"),
            "created_ids": plan["created_ids"],
            "review_path": path.with_suffix(".md")
            .relative_to(self.engine.root)
            .as_posix(),
            "effects": effects[:50],
            "effects_count": len(effects),
            **({"reservations": self._edits().rows(list(plan["entries"]))}
               if plan.get("edit_reservations") else {}),
            "review_changes": self._review_changes(plan),
            **self._proposal_relations(plan),
            "history_summary": plan.get("summary"),
            "transformations": plan.get("transformations", []),
            **(
                {"no_changes_reason": plan["no_changes_reason"]}
                if state == "no_changes"
                else {}
            ),
            **self._review_policy(plan, state),
        }

    def _review_policy(self, plan, state):
        key = ("instructions_approval_required" if plan.get("kind") == "instructions"
               else "knowledge_approval_required")
        required = plan.get(key, True)
        if state == "prepared":
            required = required or self._policies()[key]
        if plan.get("instruction_bindings_changed"):
            required = required or plan.get("instructions_approval_required", True)
            if state == "prepared":
                required = required or self._policies()["instructions_approval_required"]
        result = {
            "approval_required": required,
            "next_action": "report_no_changes"
            if state == "no_changes"
            else "present_changes_in_chat_for_approval"
            if required
            else "present_changes_in_chat_then_apply",
        }
        if state in {"prepared", "partial", "applying"}:
            result["next_call"] = {
                "tool": "write",
                "operation": "workspace.instructions_apply" if plan.get("kind") == "instructions" else "workspace.proposal_apply",
                "arguments": {"cycle_id": plan["cycle_id"], "proposal_id": plan["proposal_id"],
                              "expected_revision": _revision(_json(plan))},
                "requires": (["present_review_changes"] + (["user_confirmation"] if required else []))
                            if state == "prepared" else [],
            }
        return result

    def instructions_prepare(
        self,
        cycle_id: str,
        content: str,
        expected_revision: str,
        user_request: str,
        proposal_id: str | None = None,
    ):
        """Only a direct user request starts an instructions edit; preparation is temporary."""
        _text(user_request, "user's direct request to change instructions")
        if not isinstance(content, str):
            raise PCWError("content must be ordinary Markdown text")
        if extract_settings(content)[1] != content:
            raise PCWError("Machine policy keys belong in Core configuration; use workspace.settings_preview/settings_apply")
        with self._cycle(cycle_id) as (records, cycle):
            if cycle.get("purpose") == "audit":
                raise PCWError("Audit cannot change instructions")
            current = self._instructions()
            if current["revision"] != expected_revision:
                raise PCWError(
                    "Instructions changed since they were read; read them again"
                )
            ident = self._proposal_id(cycle_id, cycle, proposal_id, "instructions")
            before = {
                "path": current["path"],
                "text": current["content"],
                "fingerprint": current["revision"],
            }
            after = {
                "path": current["path"],
                "text": content,
                "fingerprint": _revision(content),
            }
            plan = {
                "cycle_id": cycle_id,
                "proposal_id": ident,
                "kind": "instructions",
                "instructions_approval_required": self._policies()["instructions_approval_required"],
                "settings_revision": self._settings_snapshot()["revision"],
                "required_instructions_revision": current.get("required_revision"),
                "user_request": user_request,
                "created_ids": {},
                "entries": {"INSTRUCTIONS.md": {"before": before, "after": after}},
            }
            state = "prepared"
            if content == current["content"]:
                state = "no_changes"
                plan["no_changes_reason"] = (
                    "The proposed instructions match the existing file."
                )
            return self._save_plan(records, cycle, plan, state)

    def instructions_apply(
        self, cycle_id: str, proposal_id: str, expected_revision: str, confirmation: str | None = None
    ):
        with self._cycle(cycle_id) as (records, cycle):
            if cycle.get("purpose") == "audit":
                raise PCWError("Audit cannot apply instructions")
            entry = cycle["proposals"].get(_id(proposal_id))
            if (
                entry is None
                or entry["revision"] != expected_revision
                or entry["state"] in {"discarded", "no_changes"}
            ):
                raise PCWError(
                    "Apply only the exact presented and confirmed proposal revision"
                )
            plan = self._load_plan(cycle_id, proposal_id, entry)
            if plan.get("kind") != "instructions":
                raise PCWError("Use proposal_apply for knowledge changes")
            if entry["state"] == "applied":
                return {
                    "proposal_id": proposal_id,
                    "status": "applied",
                    "replayed": True,
                }
            if (entry["state"] == "prepared" and plan.get("settings_revision") is not None
                    and plan["settings_revision"] != self._settings_snapshot()["revision"]):
                raise PCWError("Settings changed; read instructions and use instructions_prepare again")
            plan = self._execution_plan(plan)
            change = plan["entries"]["INSTRUCTIONS.md"]
            if (plan.get("instructions_approval_required", True)
                    or entry["state"] == "prepared" and self._policies()["instructions_approval_required"]):
                _text(confirmation, "current user's explicit confirmation")
            retry = entry["state"] != "prepared"
            if (not retry and plan.get("required_instructions_revision") is not None
                    and plan["required_instructions_revision"] != self._instructions().get("required_revision")):
                raise PCWError("Instructions conflict: required instructions changed; prepare again")
            if not (retry and self._matches(change["after"])) and not self._matches(
                change["before"]
            ):
                raise PCWError("Instructions conflict: read, prepare and approve again")
            entry["state"] = "applying"
            self._persist(records)
            try:
                from .context_document import save_instructions
                save_instructions(self, change["after"]["text"])
                if not self._matches(change["after"]):
                    raise PCWError("Instructions write verification failed")
                history = self._history_record(
                    proposal_id,
                    force=self._policies()["git_history_enabled"],
                    plan=plan,
                )
                entry["state"] = "applied"
                self._persist(records)
                return {
                    "proposal_id": proposal_id,
                    "revision": expected_revision,
                    "status": "applied",
                    "path": "INSTRUCTIONS.md",
                    "instructions_revision": change["after"]["fingerprint"],
                    "tmp_preserved": True,
                    "history": history,
                }
            except Exception as exc:
                entry["state"] = "partial"
                self._persist(records)
                return {
                    "proposal_id": proposal_id,
                    "status": "partial",
                    "error": str(exc),
                    "tmp_preserved": True,
                    "next_action": "instructions_apply",
                }

    def proposal_prepare(
        self,
        cycle_id: str,
        changes: list[ProposalChange],
        replacements: dict[str, str] | None = None,
        proposal_id: str | None = None,
        no_changes_reason: str | None = None,
        transformations: list[dict] | None = None,
        summary: str | None = None,
        *,
        expected_task_revision: str,
    ):
        return self._prepare_proposal(cycle_id, changes, replacements, proposal_id,
                                      no_changes_reason, transformations, summary,
                                      expected_task_revision=expected_task_revision)

    def _prepare_proposal(
        self, cycle_id, changes, replacements=None, proposal_id=None,
        no_changes_reason=None, transformations=None, summary=None,
        instruction_documents=None, *, expected_task_revision,
    ):
        """Prepare a complete, reviewed set without changing permanent files.

        Creates use key/content/links; @key addresses a new material in this set.
        Existing entries use material_id/expected_revision plus content, links,
        path (relative to knowledge) or delete:true. A replacement redirects all
        incoming links and requires the old entry to be explicitly deleted.
        """
        with self._cycle(cycle_id) as (records, cycle), self.engine._guard():
            self._require_work(cycle)
            _text(expected_task_revision, "expected_task_revision")
            conflict = self._task_conflict(cycle_id, cycle, expected_task_revision)
            if conflict is not None:
                return conflict
            instructions = self._instructions()
            approval_required = self._policies()["knowledge_approval_required"]
            if summary is not None and (not isinstance(summary, str) or not summary.strip()
                                        or len(summary) > 240 or any(ord(c) < 32 for c in summary)):
                raise PCWError("summary must be one nonempty line of at most 240 characters")
            if not isinstance(changes, list) or len(changes) > 100:
                raise PCWError("changes must contain 0 to 100 final material changes")
            if not changes:
                _text(
                    no_changes_reason,
                    "no_changes_reason after comparing results with permanent knowledge",
                )
            elif no_changes_reason is not None:
                raise PCWError(
                    "no_changes_reason is only valid for an empty changes list"
                )
            if not changes and (transformations or summary):
                raise PCWError("A no-change review cannot declare a transformation or commit summary")
            ident = self._proposal_id(cycle_id, cycle, proposal_id, "knowledge")
            previous_bindings = (self._load_plan(cycle_id, ident, cycle["proposals"][ident]).get("edit_reservations", {})
                                 if proposal_id else {})
            aliases, entries, touched = {}, {}, set()
            instruction_binding_revisions = {}
            instruction_bindings_changed = False
            for change in changes:
                if not isinstance(change, dict) or set(change) - PROPOSAL_CHANGE_FIELDS:
                    raise PCWError("Unknown change fields")
                existing = change.get("material_id")
                if existing is not None:
                    if "key" in change:
                        raise PCWError("Use material_id or a new key, not both")
                    material = self.engine._material(existing)
                    if change.get("expected_revision") != record_revision(material):
                        raise PCWError(
                            "Material changed since it was read; read it again"
                        )
                    before = self._snapshot(material)
                    metadata, content, path = (
                        copy.deepcopy(material.metadata),
                        material.content,
                        material.path,
                    )
                    target_id = material.id
                else:
                    key = _id(_text(change.get("key"), "key"))
                    if (
                        key in aliases
                        or change.get("delete")
                        or "expected_revision" in change
                    ):
                        raise PCWError("Invalid or duplicate new-material key")
                    target_id = self.engine._allocate_material_id()
                    aliases[key] = target_id
                    before, metadata, content, path = (
                        None,
                        {"links": [], "id": target_id, "type": "knowledge"},
                        None,
                        None,
                    )
                if target_id in touched:
                    raise PCWError("One final change per material is required")
                touched.add(target_id)
                if "delete" in change and type(change["delete"]) is not bool:
                    raise PCWError("delete must be a boolean")
                if change.get("delete"):
                    if before and before["path"] == "Context.md":
                        raise PCWError("The canonical Context.md project cannot be deleted")
                    if set(change) - {"material_id", "expected_revision", "delete"}:
                        raise PCWError(
                            "Deletion cannot include a new body, links or path"
                        )
                    entries[target_id] = {"before": before, "after": None}
                    continue
                if "content" in change:
                    content = change["content"]
                if not isinstance(content, str):
                    raise PCWError(
                        "Supply ordinary Markdown content for a new material"
                    )
                # Old custom instructions may still name the legacy fields.
                # Normalize syntax before review, never after user approval.
                content = migrate_sources(content)
                if path is None:
                    path = self.engine.new_path(
                        content,
                        target_id,
                        [e["after"]["path"] for e in entries.values() if e["after"]],
                    )
                if "links" in change:
                    metadata["links"] = change["links"]
                for field in ("type", "status"):
                    if field in change:
                        if field == "status" and change[field] is None:
                            metadata.pop(field, None)
                        else:
                            metadata[field] = change[field]
                validate_metadata(metadata)
                if before and before["path"] == "Context.md" and metadata.get("type") != "project":
                    raise PCWError("Context.md must remain type project")
                if metadata.get("type") == "project":
                    path = "Context.md"
                    if "instructions" not in metadata:
                        from .context_document import instruction_name
                        metadata["instructions"] = {"workspace": "[[" + instruction_name(self.engine.workspace).removesuffix(".md") + "]]"}
                if "instructions" in change:
                    if metadata.get("type") != "project":
                        raise PCWError("Only Context.md can bind required instructions")
                    references = change["instructions"]
                    if (not isinstance(references, dict) or "workspace" not in references
                            or any(not isinstance(role, str) or not isinstance(ref, str)
                                   or not ref.startswith("[[") or not ref.endswith("]]")
                                   for role, ref in references.items())):
                        raise PCWError("instructions must map workspace and optional roles to wikilinks")
                    from .context_document import library
                    resolved = library(self).resolve(list(references.values()), prepared=instruction_documents)
                    instruction_binding_revisions[target_id] = resolved["revision"]
                    if references != metadata.get("instructions"):
                        instruction_bindings_changed = True
                    metadata["instructions"] = dict(references)
                if "description" in change:
                    metadata["description"] = change["description"]
                body_changed = before is None or content != _parse(before["text"])[1]
                if body_changed and "description" not in change:
                    raise PCWError("Supply description for a new or changed body after reviewing its content and applicability")
                if body_changed or "description" in change:
                    validate_description(metadata, required=True)
                if "path" in change:
                    if metadata.get("type") == "project":
                        raise PCWError("The canonical project path is Context.md and cannot be moved")
                    destination = self.engine._knowledge_path(change["path"])
                    path = destination.relative_to(self.engine.root).as_posix()
                    if (
                        before is None or path != before["path"]
                    ) and destination.exists():
                        raise PCWError("Move destination already exists")
                if any(
                    e["after"] and e["after"]["path"] == path for e in entries.values()
                ):
                    raise PCWError("Two materials cannot share a destination path")
                entries[target_id] = {
                    "before": before,
                    "after": {"path": path, "content": content, "metadata": metadata},
                }

            project_count = sum(m.metadata.get("type") == "project" for i, m in self.engine.materials.items() if i not in entries)
            project_count += sum(e["after"] is not None and e["after"]["metadata"].get("type") == "project" for e in entries.values())
            if project_count > 1:
                raise PCWError("Keep one canonical project record; update or consolidate the existing description")

            limit = self._policies()["material_max_tokens"]
            if limit is not None:
                exceeded = []
                for target_id, entry in entries.items():
                    if entry["after"] is not None:
                        if (entry["before"] is not None and
                                entry["after"]["content"] == _parse(entry["before"]["text"])[1]):
                            continue
                        count = self.engine._get_chunker().count(
                            entry["after"]["content"]
                        )
                        if count > limit:
                            exceeded.append(
                                {
                                    "id": target_id,
                                    "key": next(
                                        (
                                            key
                                            for key, value in aliases.items()
                                            if value == target_id
                                        ),
                                        None,
                                    ),
                                    "tokens": count,
                                    "limit": limit,
                                }
                            )
                if exceeded:
                    return {
                        "status": "needs_split",
                        "materials": exceeded,
                        "next_action": "revise_semantic_materials",
                        "knowledge_changed": False,
                    }

            def resolve(value):
                if not isinstance(value, str):
                    raise PCWError("Links must be exact IDs or @keys")
                if value.startswith("@"):
                    if value[1:] not in aliases:
                        raise PCWError("Unknown new-material link key")
                    return aliases[value[1:]]
                return _id(value)

            if replacements is not None and not isinstance(replacements, dict):
                raise PCWError(
                    "replacements must map deleted IDs to surviving IDs or @keys"
                )
            mapping = {_id(k): resolve(v) for k, v in (replacements or {}).items()}
            deleted = {key for key, entry in entries.items() if entry["after"] is None}
            self.engine.assert_no_foreign_incoming(deleted)
            moved = {key for key, entry in entries.items()
                     if entry["before"] and entry["after"] and
                     entry["before"]["path"] != entry["after"]["path"]}
            self._assert_no_foreign_rename_incoming(moved)
            survivors = (set(self.engine.catalog) | set(entries)) - deleted
            if any(
                old not in deleted or new not in survivors
                for old, new in mapping.items()
            ):
                raise PCWError(
                    "Replacement source must be deleted and its target must survive"
                )
            # Mechanical incoming-link consequences are part of the same review.
            for material in self.engine.materials.values():
                if material.id not in entries and any(
                    link in deleted or link in moved for link in material.links
                ):
                    entries[material.id] = {
                        "before": self._snapshot(material),
                        "after": {
                            "path": material.path,
                            "content": material.content,
                            "metadata": copy.deepcopy(material.metadata),
                        },
                    }
            for key, entry in entries.items():
                after = entry["after"]
                if after is None:
                    continue
                temporary = self._temporary_cards()
                dependencies = self._temporary_dependencies(after, temporary)
                if dependencies:
                    raise PCWError("Permanent knowledge cannot depend on temporary materials; move the source into knowledge first")
                raw = after["metadata"].get("links", [])
                if not isinstance(raw, list):
                    raise PCWError("links must be a list")
                links = [resolve(value) for value in raw]
                links = [
                    mapping.get(link, link)
                    for link in links
                    if link not in deleted or link in mapping
                ]
                if any(link not in survivors for link in links):
                    raise PCWError("Unknown link target")
                after["metadata"]["links"] = list(
                    dict.fromkeys(link for link in links if link != key)
                )
            final_catalog = {i: m for i, m in self.engine.catalog.items() if i not in deleted}
            final_owners = dict(self.engine.owners)
            for key, entry in entries.items():
                after = entry["after"]
                if after:
                    final_catalog[key] = Material(key, after["path"],
                        context_metadata(after["metadata"], self.engine.workspace), after["content"])
                    final_owners[key] = self.engine.workspace
            codec = self.engine.link_codec(final_catalog, final_owners)
            previous_codec = self.engine.link_codec()
            for key, entry in entries.items():
                after = entry["after"]
                if after is None:
                    continue
                display_aliases = previous_codec.display_aliases(
                    _parse(entry['before']['text'])[0].get('links', []), key
                ) if entry['before'] else {}
                after['link_ids'] = list(after['metadata']['links'])
                after["metadata"] = context_metadata(after["metadata"], self.engine.workspace)
                after["text"] = codec.record_text(after["metadata"], after.pop("content"), aliases=display_aliases)
                after.pop("metadata")
                after["fingerprint"] = _revision(after["text"])
            plan = {
                "format_version": 2,
                "cycle_id": cycle_id,
                "proposal_id": ident,
                "task_revision": expected_task_revision,
                "clarification_count": len(cycle.get("clarifications", [])),
                "created_ids": aliases,
                "entries": entries,
                "instructions_revision": instructions["revision"],
                "required_instructions_revision": instructions.get("required_revision"),
                "settings_revision": self._settings_snapshot()["revision"],
                "knowledge_approval_required": approval_required,
                "instruction_bindings_changed": instruction_bindings_changed,
                "instruction_bindings_revisions": instruction_binding_revisions,
                "instructions_approval_required": self._policies()["instructions_approval_required"] if instruction_bindings_changed else False,
            }
            if instruction_documents:
                plan["instruction_documents"] = instruction_documents
            relation_catalog = {**self.engine.catalog, **final_catalog}
            relation_owners = {**self.engine.owners, **final_owners}
            relation_edges = {(source, target)
                              for catalog in (self.engine.catalog, final_catalog)
                              for source, material in catalog.items()
                              for target in material.links}
            relation_pages, relation_offset = [], 0
            while True:
                relation_page = self.engine.relation_candidates(
                    entries, relation_offset, 100, catalog=relation_catalog,
                    owners=relation_owners, edges=relation_edges, in_proposal=entries)
                relation_pages.extend(relation_page["items"])
                if relation_page["next_offset"] is None:
                    break
                relation_offset = relation_page["next_offset"]
            plan["relation_candidates"] = {**relation_page, "items": relation_pages,
                                           "captured_at_preparation": True}
            linked_ids = {target for entry in entries.values() for side in ('before', 'after')
                          for target in (entry.get(side) or {}).get('link_ids', [])}
            plan['link_targets'] = {}
            for target in linked_ids:
                material = final_catalog.get(target) or self.engine.catalog.get(target)
                if material:
                    plan['link_targets'][target] = {
                        'id': target,
                        'title': next((line[2:].strip() for line in material.content.splitlines()
                                       if line.startswith('# ')), target),
                        'path': material.path,
                        'workspace': final_owners.get(target, self.engine.owners.get(target)),
                    }
            if transformations is not None or mapping:
                plan["transformations"] = validate_transformations(transformations, entries, resolve, mapping)
            if summary is not None:
                plan["summary"] = summary.strip()
            if not changes:
                plan["no_changes_reason"] = no_changes_reason
            state = "prepared" if changes else "no_changes"
            if entries:
                # Covers automatic incoming-link edits and newly allocated IDs too.
                plan["edit_reservations"] = self._edits().acquire(
                    cycle_id, list(entries), summary or "Prepared knowledge proposal")
            result = self._save_plan(records, cycle, plan, state)
            self._release_unreferenced_edits(cycle_id, cycle, previous_bindings)
            return result

    def _plan_codec(self, plan, side):
        catalog, owners = dict(self.engine.catalog), dict(self.engine.owners)
        for ident, entry in plan["entries"].items():
            snapshot = entry.get(side)
            if snapshot:
                metadata, body = _parse(snapshot["text"])
                catalog[ident] = Material(ident, snapshot["path"], metadata, body)
                owners[ident] = self.engine.workspace
            else:
                catalog.pop(ident, None)
                owners.pop(ident, None)
        return self.engine.link_codec(catalog, owners)

    def _assert_no_foreign_rename_incoming(self, moved):
        for material in self.engine.catalog.values():
            if self.engine.owners[material.id] != self.engine.workspace and set(material.links) & moved:
                raise PCWError(
                    f"Rename would change links in material {material.id} in context "
                    f"{self.engine.owners[material.id]}; explicitly update that context through its own reviewed proposal first"
                )

    def _effects(self, plan):
        result = []
        instructions = plan.get("kind") in {"instructions", "project"}
        old_codec = self._plan_codec(plan, "before") if not instructions else None
        new_codec = self._plan_codec(plan, "after") if not instructions else None
        for ident, entry in sorted(plan["entries"].items()):
            before, after = entry["before"], entry["after"]
            instructions = plan.get("kind") in {"instructions", "project"}
            old_links = (
                _parse(before["text"])[0].get("links", [])
                if before and not instructions
                else []
            )
            new_links = (
                _parse(after["text"])[0].get("links", [])
                if after and not instructions
                else []
            )
            if not instructions:
                old_links = before.get('link_ids') if before and 'link_ids' in before else old_codec.decode(old_links, ident)
                new_links = after.get('link_ids') if after and 'link_ids' in after else new_codec.decode(new_links, ident)
            result.append(
                {
                    "id": ident,
                    "action": "delete"
                    if after is None
                    else "create"
                    if before is None
                    else "update",
                    "old_path": before["path"] if before else None,
                    "path": after["path"] if after else None,
                    "links_added": [x for x in new_links if x not in old_links],
                    "links_removed": [x for x in old_links if x not in new_links],
                }
            )
        return result

    def _review_changes(self, plan):
        """Exact changed passages for chat presentation; never a generated summary.

        Keep the on-disk review format stable so pending 0.9.0 proposals remain
        verifiable. Labels are supplied by the agent in the conversation language.
        """

        def body(snapshot):
            return (
                snapshot["text"]
                if plan.get("kind") in {"instructions", "project"}
                else _parse(snapshot["text"])[1]
            )

        def describe(ident, snapshot=None, previous=False):
            if snapshot is None:
                entry = plan["entries"].get(ident, {})
                snapshot = entry.get("before" if previous else "after") or entry.get(
                    "before"
                )
            if snapshot:
                text, path = body(snapshot), snapshot["path"]
            else:
                if ident in plan.get('link_targets', {}):
                    return dict(plan['link_targets'][ident])
                material = self.engine.materials.get(ident)
                text, path = (
                    (material.content, material.path)
                    if material
                    else ("", "knowledge/" + ident)
                )
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = next(
                (line[2:].strip() for line in lines if line.startswith("# ")),
                lines[0] if lines else ident,
            )
            return {"id": ident, "title": title, "path": path}

        changes = []
        for effect in self._effects(plan):
            ident = effect["id"]
            before, after = (plan["entries"][ident][key] for key in ("before", "after"))
            old = body(before).splitlines(keepends=True) if before else []
            new = body(after).splitlines(keepends=True) if after else []
            passages = []
            for tag, i, j, k, end in difflib.SequenceMatcher(
                None, old, new, autojunk=False
            ).get_opcodes():
                if tag != "equal":
                    passages.append(
                        {
                            "before": "".join(old[i:j]),
                            "after": "".join(new[k:end]),
                            "before_start_line": i + 1,
                            "after_start_line": k + 1,
                            "context_before": "".join(old[max(0, i - 3) : i]),
                            "context_after": "".join(old[j : j + 3]),
                        }
                    )
            changes.append(
                {
                    **effect,
                    "title": describe(ident, after or before)["title"],
                    "old_title": describe(ident, before)["title"] if before else None,
                    "body_changes": passages,
                    "links_added": [describe(link) for link in effect["links_added"]],
                    "links_removed": [
                        describe(link, previous=True)
                        for link in effect["links_removed"]
                    ],
                }
            )
            if plan.get("kind", "knowledge") == "knowledge":
                changes[-1]["metadata_changes"] = {
                    key: {"before": _parse(before["text"])[0].get(key) if before else None,
                          "after": _parse(after["text"])[0].get(key) if after else None}
                    for key in ("type", "status", "instructions")
                    if (_parse(before["text"])[0].get(key) if before else None) !=
                       (_parse(after["text"])[0].get(key) if after else None)
                }
                old_description = (
                    _parse(before["text"])[0].get("description", "") if before else ""
                )
                new_description = (
                    _parse(after["text"])[0].get("description", "") if after else ""
                )
                if old_description != new_description:
                    changes[-1]["description_change"] = {
                        "before": old_description,
                        "after": new_description,
                    }
                old_navigation = _parse(before["text"])[0].get("links", []) if before else []
                new_navigation = _parse(after["text"])[0].get("links", []) if after else []
                if old_navigation != new_navigation:
                    changes[-1]["navigation_changes"] = {"before": old_navigation, "after": new_navigation}
        for document in plan.get("instruction_documents", []):
            changes.append({
                "id": document["id"], "kind": "create", "scope": "core/instructions",
                "path": "core/instructions/" + document["path"], "title": document["title"],
                "body_changes": [{"before": "", "after": document["text"],
                                  "before_start_line": 1, "after_start_line": 1}],
                "links_added": [], "links_removed": [],
            })
        return changes

    @staticmethod
    def _review(plan):
        heading = (
            "# Proposed instructions changes\n"
            if plan.get("kind") == "instructions"
            else "# Proposed permanent-memory changes\n"
        )
        parts = [
            heading,
            "Temporary context remains until this cycle is explicitly closed.\n",
        ]
        if "task_revision" in plan:
            parts.append("\nTask revision: " + plan["task_revision"] + "\n"
                         + "Clarification count: " + str(plan["clarification_count"]) + "\n")
        if "no_changes_reason" in plan:
            parts.append(
                "\nNo permanent changes after result review:\n"
                + plan["no_changes_reason"]
                + "\n"
            )
        if plan.get("summary"):
            parts.append("\nCommit summary: " + plan["summary"] + "\n")
        for transform in plan.get("transformations", []):
            parts.append("\n" + transform["kind"] + ": " + ", ".join(transform["sources"])
                         + " -> " + ", ".join(transform["targets"]) + "\n")
        for ident, entry in sorted(plan["entries"].items()):
            before, after = entry["before"], entry["after"]
            parts.append(f"\n## {ident}\n")
            parts.append(
                f"Path: {before['path'] if before else '/dev/null'} -> {after['path'] if after else '/dev/null'}\n"
            )
            diff = "\n".join(
                difflib.unified_diff(
                    before["text"].splitlines() if before else [],
                    after["text"].splitlines() if after else [],
                    fromfile=before["path"] if before else "/dev/null",
                    tofile=after["path"] if after else "/dev/null",
                    lineterm="",
                )
            )
            parts.append(diff or "No content change.\n")
        for document in plan.get("instruction_documents", []):
            parts.append("\n## New Core instruction: " + document["id"] + "\n"
                         + "Path: core/instructions/" + document["path"] + "\n\n" + document["text"])
        return "\n".join(parts)

    @staticmethod
    def _proposal_relations(plan, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise PCWError("Use a nonnegative relation_offset and relation_limit between 1 and 100")
        inventory = plan.get("relation_candidates")
        if inventory is None:
            return {}
        items = inventory["items"]
        return {"relation_candidates": {**inventory, "items": items[offset:offset + limit],
                "offset": offset, "limit": limit, "total": len(items),
                "next_offset": offset + limit if offset + limit < len(items) else None}}

    def proposal_read(self, cycle_id: str, proposal_id: str,
                      relation_offset: Offset = 0, relation_limit: PageLimit = 50):
        with self._cycle(cycle_id) as (_, cycle), self.engine.read_guard():
            entry = cycle["proposals"].get(_id(proposal_id))
            if entry is None:
                raise PCWError("Unknown proposal")
            plan = self._load_plan(cycle_id, proposal_id, entry)
            task_conflict = None
            if entry["state"] == "prepared" and plan.get("kind", "knowledge") == "knowledge":
                task_conflict = self._task_conflict(cycle_id, cycle, plan.get("task_revision"),
                                                    plan.get("clarification_count"))
            return {
                "proposal_id": proposal_id,
                **entry,
                **({"task_revision": plan["task_revision"],
                    "clarification_count": plan["clarification_count"]} if "task_revision" in plan else {}),
                "review": self._review(plan),
                "review_changes": self._review_changes(plan),
                **self._proposal_relations(plan, relation_offset, relation_limit),
                "history_summary": plan.get("summary"),
                "transformations": plan.get("transformations", []),
                **self._review_policy(plan, entry["state"]),
                **(task_conflict or {}),
                **(
                    {"no_changes_reason": plan["no_changes_reason"]}
                    if "no_changes_reason" in plan
                    else {}
                ),
            }

    def proposal_discard(self, cycle_id: str, proposal_id: str, expected_revision: str):
        with self._cycle(cycle_id) as (records, cycle):
            entry = cycle["proposals"].get(_id(proposal_id))
            if (
                entry is None
                or entry["revision"] != expected_revision
                or entry["state"] not in {"prepared", "discarded"}
            ):
                raise PCWError("Only the exact unapplied proposal may be discarded")
            plan = self._load_plan(cycle_id, proposal_id, entry)
            entry["state"] = "discarded"
            self._persist(records)
            self._release_unreferenced_edits(cycle_id, cycle, plan.get("edit_reservations", {}))
            return {"proposal_id": proposal_id, "status": "discarded"}

    def _release_unreferenced_edits(self, cycle_id, cycle, previous_bindings):
        bindings = dict(previous_bindings)
        if not bindings:
            return
        for ident, entry in cycle["proposals"].items():
            if entry["state"] not in {"applied", "discarded", "no_changes"}:
                for material in self._load_plan(cycle_id, ident, entry).get("entries", {}):
                    bindings.pop(material, None)
        self._edits().release(cycle_id, bindings=bindings)

    def _matches(self, snapshot):
        if snapshot["path"] == "INSTRUCTIONS.md":
            return self._instructions()["revision"] == snapshot["fingerprint"]
        path = self.engine.root / snapshot["path"]
        self.engine._assert_safe(path)
        return (
            path.is_file()
            and _revision(path.read_bytes().decode("utf-8")) == snapshot["fingerprint"]
        )

    def _preflight(self, plan, retry):
        from .context_document import library
        for document in plan.get("instruction_documents", []):
            store = library(self)
            try:
                current = store.read(document["id"])
            except FileNotFoundError:
                store.prepare_create(document["path"], document["metadata"], document["content"])
            else:
                if not retry or current["path"] != document["path"] or current["revision"] != document["revision"]:
                    raise PCWError("Prepared instruction conflicts with an existing document")
        for ident, expected in plan.get("instruction_bindings_revisions", {}).items():
            after = plan["entries"][ident]["after"]
            from .context_document import library
            references = _parse(after["text"])[0]["instructions"]
            if library(self).resolve(list(references.values()), prepared=plan.get("instruction_documents"))["revision"] != expected:
                raise PCWError("Required instruction target changed; prepare and present again")
        if plan.get('kind') not in {'instructions', 'project'}:
            codec = self._plan_codec(plan, 'after')
            for ident, change in plan['entries'].items():
                after = change['after']
                if after is None:
                    continue
                try:
                    links = codec.decode(_parse(after['text'])[0].get('links', []), ident)
                    if any(target not in codec.catalog for target in links) or (
                        'link_ids' in after and links != after['link_ids']
                    ):
                        raise PCWError('Navigation target identity changed')
                except PCWError as exc:
                    raise PCWError('Proposal conflict: navigation target changed; read it and prepare and present again') from exc
        moved = {ident for ident, entry in plan["entries"].items()
                 if entry["before"] and entry["after"] and
                 entry["before"]["path"] != entry["after"]["path"]}
        self._assert_no_foreign_rename_incoming(moved)
        for material in self.engine.materials.values():
            if material.id not in plan["entries"] and set(material.links) & moved:
                raise PCWError("Proposal conflict: a new incoming rename link appeared; prepare and present again")
        for ident, entry in plan["entries"].items():
            before, after = entry["before"], entry["after"]
            material = self.engine.materials.get(ident)
            if retry and after is None and material is None:
                continue
            if (
                retry
                and after is not None
                and self._matches(after)
                and (
                    before is None
                    or before["path"] == after["path"]
                    or not (self.engine.root / before["path"]).exists()
                )
            ):
                continue
            if before is None:
                if material is not None or (self.engine.root / after["path"]).exists():
                    raise PCWError("Proposal conflict: new material already exists")
            elif not self._matches(before):
                moved = {**before, "path": after["path"]} if after is not None else None
                if not (
                    retry
                    and moved
                    and not (self.engine.root / before["path"]).exists()
                    and self._matches(moved)
                ):
                    raise PCWError(
                        "Proposal conflict: material or navigation changed; prepare and present again"
                    )
            if after and before and after["path"] != before["path"]:
                destination = self.engine.root / after["path"]
                if destination.exists() and not (
                    retry and self._matches({**before, "path": after["path"]})
                ):
                    raise PCWError("Proposal conflict: move destination exists")

    def proposal_apply(
        self,
        cycle_id: str,
        proposal_id: str,
        expected_revision: str,
        confirmation: str | None = None,
    ):
        with self._cycle(cycle_id) as (records, cycle), self.engine._guard():
            self._require_work(cycle)
            entry = cycle["proposals"].get(_id(proposal_id))
            if (
                entry is None
                or entry["revision"] != expected_revision
                or entry["state"] in {"discarded", "no_changes"}
            ):
                raise PCWError("Apply only the exact presented proposal revision")
            plan = self._load_plan(cycle_id, proposal_id, entry)
            if plan.get("kind") == "instructions":
                raise PCWError(
                    "Use instructions_apply or project_apply for document changes"
                )
            if entry["state"] == "applied":
                return {
                    "proposal_id": proposal_id,
                    "revision": expected_revision,
                    "status": "applied",
                    "replayed": True,
                }
            if entry["state"] == "prepared":
                conflict = self._task_conflict(cycle_id, cycle, plan.get("task_revision"),
                                               plan.get("clarification_count"))
                if conflict is not None:
                    return {**conflict, "proposal_id": proposal_id}
            plan = self._execution_plan(plan)
            # Finish an already-started authorized write before reviewing it under
            # new rules; otherwise a mid-recovery edit could strand partial data.
            if (
                entry["state"] == "prepared"
                and (plan.get("instructions_revision") != self._instructions()["revision"]
                     or (plan.get("required_instructions_revision") is not None
                         and plan["required_instructions_revision"] != self._instructions().get("required_revision")))
            ):
                raise PCWError(
                    "Instructions changed; read them and prepare the knowledge proposal again"
                )
            if (entry["state"] == "prepared" and plan.get("settings_revision") is not None
                    and plan["settings_revision"] != self._settings_snapshot()["revision"]):
                raise PCWError("Settings changed; read settings and use proposal_prepare again")
            if (plan.get("knowledge_approval_required", True)
                    or entry["state"] == "prepared" and self._policies()["knowledge_approval_required"]
                    or (plan.get("instruction_bindings_changed") and
                        (plan.get("instructions_approval_required", True) or
                         entry["state"] == "prepared" and self._policies()["instructions_approval_required"]))):
                _text(confirmation, "current user's explicit confirmation")
            self.engine.assert_no_foreign_incoming(
                [i for i, e in plan["entries"].items() if e["after"] is None]
            )
            self._assert_permanent_temporary_free(change["after"] for change in plan["entries"].values())
            self._preflight(plan, entry["state"] != "prepared")
            bindings = plan.get("edit_reservations")
            if bindings is None or entry["state"] != "prepared":
                # Preserve immutable pre-upgrade reviews. Recovery still passed
                # the exact before/after preflight; never overwrite newer bytes.
                bindings = self._edits().acquire(cycle_id, list(plan["entries"]),
                                               plan.get("summary") or "Apply reviewed knowledge proposal")
            with self._edits().admission(cycle_id, bindings):
                entry["state"] = "applying"
                self._persist(records)
                try:
                    for document in plan.get("instruction_documents", []):
                        from .context_document import library
                        store = library(self)
                        try:
                            current = store.read(document["id"])
                        except FileNotFoundError:
                            current = store.save(document["path"], document["metadata"], document["content"])
                        if current["revision"] != document["revision"]:
                            raise PCWError("Applied instruction verification failed")
                    # Write survivors and redirect incoming links before any deletion.
                    for ident, change in plan["entries"].items():
                        before, after = change["before"], change["after"]
                        if after is None or self._matches(after):
                            continue
                        if (
                            before is None
                            or _parse(before["text"])[1] != _parse(after["text"])[1]
                        ):
                            self.engine._delete_rows(ident)
                        target = self.engine.root / after["path"]
                        if before and before["path"] != after["path"]:
                            old = self.engine.root / before["path"]
                            if old.exists():
                                target.parent.mkdir(parents=True, exist_ok=True)
                                os.rename(old, target)
                                _sync_directory(old.parent)
                                _sync_directory(target.parent)
                        self.engine._write(target, after["text"])
                        if not self._matches(after):
                            raise PCWError("Applied text verification failed")
                    for ident, change in plan["entries"].items():
                        if change["after"] is None:
                            self.engine._delete_rows(ident)
                            target = self.engine.root / change["before"]["path"]
                            target.unlink(missing_ok=True)
                            _sync_directory(target.parent)
                    self.engine._scan()
                    self.engine._reconcile()
                    materials = [
                        self.engine._index_receipt(self.engine._material(ident))
                        for ident, change in plan["entries"].items()
                        if change["after"] is not None
                    ]
                    instruction_index = None
                    if plan.get("instruction_documents"):
                        try:
                            instruction_index = (self.instruction_reconcile()
                                if self.instruction_reconcile is not None else
                                {"status": "pending", "error": "No Core indexing callback configured"})
                        except Exception as exc:
                            instruction_index = {"status": "pending", "error": str(exc)}
                    history = self._history_record(proposal_id, plan=plan)
                    entry["state"] = (
                        "index_pending"
                        if (any(m["index_status"] != "indexed" for m in materials)
                            or instruction_index is not None and instruction_index["status"] != "indexed")
                        else "applied"
                    )
                    self._persist(records)
                    if entry["state"] == "applied":
                        self._edits().release(cycle_id, bindings=bindings)
                    return {
                        "proposal_id": proposal_id,
                        "revision": expected_revision,
                        "status": entry["state"],
                        "materials": materials,
                        "tmp_preserved": True,
                        "history": history,
                        **({"instruction_index": instruction_index} if instruction_index is not None else {}),
                        **({"next_call": {"tool": "write", "operation": "workspace.proposal_apply",
                             "arguments": {"cycle_id": cycle_id, "proposal_id": proposal_id,
                                           "expected_revision": expected_revision,
                                           **({"confirmation": confirmation} if confirmation is not None else {})}}}
                           if entry["state"] == "index_pending" else {}),
                    }
                except Exception as exc:
                    entry["state"] = "partial"
                    self._persist(records)
                    return {
                        "proposal_id": proposal_id,
                        "status": "partial",
                        "error": str(exc),
                        "tmp_preserved": True,
                        "next_action": "proposal_apply",
                        "next_call": {"tool": "write", "operation": "workspace.proposal_apply",
                                      "arguments": {"cycle_id": cycle_id, "proposal_id": proposal_id,
                                                    "expected_revision": expected_revision,
                                                    **({"confirmation": confirmation} if confirmation is not None else {})}},
                    }

    def finish(self, cycle_id: str, cancel: bool = False, confirmation: str | None = None,
               expected_revision: str | None = None):
        if type(cancel) is not bool:
            raise PCWError("cancel must be a boolean")
        with self._cycle(
            cycle_id, ("starting", "active", "closing", "closed", "cancelled")
        ) as (records, cycle):
            if cycle["state"] in {"closed", "cancelled"}:
                return {
                    "cycle_id": cycle_id,
                    "status": cycle["state"],
                    "replayed": True,
                }
            if cycle["state"] == "starting" and not cancel:
                return {
                    "status": "blocked",
                    "reason": "Startup is incomplete; retry it or explicitly cancel",
                }
            if cycle["state"] in {"starting", "active"}:
                unfinished = [
                    key
                    for key, entry in cycle["proposals"].items()
                    if entry["state"] not in {"applied", "discarded", "no_changes"}
                ]
                if unfinished and not cancel:
                    return {
                        "status": "blocked",
                        "unfinished_proposals": unfinished,
                        "reason": "Apply or discard proposals before closing; tmp is preserved",
                    }
                snapshot = self._cleanup_snapshot(cycle_id)
                if expected_revision is not None and expected_revision != snapshot["cleanup_revision"]:
                    return {"status": "blocked", "tmp_preserved": True,
                            "reason": "Temporary files changed: cleanup revision is stale; read status again",
                            **snapshot}
                if snapshot["temporary_files"] and self._policies().get("cleanup_approval_required", True):
                    if (not isinstance(confirmation, str) or not confirmation.strip()
                            or expected_revision != snapshot["cleanup_revision"]):
                        return {"status": "blocked", "tmp_preserved": True,
                                "reason": "Confirm deletion of the exact temporary files shown",
                                **snapshot}
                deleting = {card["id"] for path, card in self._temporary_cards()
                            if path.parent.parent == self._tmp(cycle_id)}
                dependencies = [card["id"] for path, card in self._temporary_cards()
                                if path.parent.parent != self._tmp(cycle_id)
                                and deleting.intersection(self._temporary_dependencies(card, self._temporary_cards()))]
                permanent = self._permanent_temporary_incoming(deleting)
                if permanent:
                    return {"status": "blocked", "tmp_preserved": True,
                            "reason": "Permanent knowledge depends on temporary materials; repair it before cleanup",
                            "incoming_dependencies": permanent}
                if dependencies:
                    return {"status": "blocked", "tmp_preserved": True,
                            "reason": "Other temporary cards depend on this work",
                            "incoming_dependencies": dependencies}
                cycle["cancelled_operations"] = unfinished if cancel else []
                cycle["closing_as"] = "cancelled" if cancel else "closed"
                cycle["state"] = "closing"
                self._persist(records)
            previous_id = cycle.get("restart_cycle_id")
            if previous_id and records["cycles"][previous_id]["state"] == "restarting":
                self._purge(previous_id)
                previous = records["cycles"][previous_id]
                previous["state"], previous["proposals"] = "replaced", {}
            self._edits().release(cycle_id)
            self._purge(cycle_id)
            cycle["state"] = cycle.pop("closing_as")
            cycle["proposals"] = {}  # no approval or draft body is carried forward
            self._persist(records)
            return {
                "cycle_id": cycle_id,
                "status": cycle["state"],
                "tmp_cleared": True,
                "cancelled_operations": cycle.get("cancelled_operations", []),
            }

    def reconcile(self, cycle_id: str):
        return self._call(cycle_id, "reconcile")

    def switch_embeddings(
        self, cycle_id: str, config: dict, search: dict, chunking: dict | None = None
    ):
        return self._call(
            cycle_id,
            "switch_embeddings",
            config=config,
            search=search,
            chunking=chunking,
        )

    def checkpoint(self, cycle_id: str, paths: list[str], message: str):
        if not paths or any(path != "Context.md" and not path.startswith("knowledge/") for path in paths):
            raise PCWError(
                "Only exact permanent knowledge paths may enter memory Git history"
            )
        return self._call(cycle_id, "checkpoint", paths=paths, message=message)

    def recover_workspace(self, cycle_id: str):
        with self._cycle(cycle_id, ("active", "starting")):
            return self.engine.recover_workspace(recreate_missing_knowledge=False)
