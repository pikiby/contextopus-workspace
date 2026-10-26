"""Assignment changes guard drafts; already-started writes remain recoverable."""

import hashlib
import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from test_runtime import Factory
from test_base_context import base
from test_workspace_v3 import create
from workspace.cycles import CycleRuntime, _json
from workspace.runtime import PCWError


@pytest.fixture
def runtime(tmp_path):
    instance = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    create(instance, cleanup_approval_required=False)
    yield instance
    instance.close()


def draft(runtime, opened, **kwargs):
    return runtime.proposal_prepare(
        opened["cycle_id"],
        [{"key": "fact", "content": "# Finding\nVerified result.",
          "description": "Verified result of the requested assignment."}],
        expected_task_revision=opened["task_revision"], **kwargs,
    )


def apply(runtime, opened, proposal):
    return runtime.proposal_apply(opened["cycle_id"], proposal["proposal_id"],
                                  proposal["revision"], "Apply this reviewed proposal")


def clarify(runtime, opened, text="Keep the revised scope.", request_id="message-1"):
    return runtime.clarification_add(opened["cycle_id"], text, request_id)


def test_receipts_preserve_exact_request_and_order_across_reopen(runtime):
    request = "  Original request\r\nDo not normalize.  "
    opened = runtime.startup("invocation", user_request=request)
    expected = hashlib.sha256(json.dumps(
        [request, []], ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    assert opened["task_revision"] == expected
    first = clarify(runtime, opened, "Exact first clarification\r\n", "first")
    second = clarify(runtime, opened, "Second clarification", "second")
    assert len({opened["task_revision"], first["task_revision"], second["task_revision"]}) == 3
    assert second["clarifications"] == ["Exact first clarification\r\n", "Second clarification"]
    before = runtime.record_path.read_bytes()
    status = runtime.status(opened["cycle_id"])
    replay = runtime.startup("invocation", user_request=request)
    assert status["task_revision"] == replay["task_revision"] == second["task_revision"]
    assert status["user_request"] == replay["user_request"] == request
    assert runtime.record_path.read_bytes() == before
    other = CycleRuntime(runtime.engine.work_root, "project", provider_factory=Factory())
    try:
        assert other.status(opened["cycle_id"])["task_revision"] == second["task_revision"]
    finally:
        other.close()


def test_clarification_retry_is_idempotent_and_id_cannot_change_text(runtime):
    opened = runtime.startup("invocation")
    first = clarify(runtime, opened)
    before = runtime.record_path.read_bytes()
    repeated = clarify(runtime, opened)
    assert repeated["replayed"] is True
    assert repeated["task_revision"] == first["task_revision"]
    assert runtime.record_path.read_bytes() == before
    assert clarify(runtime, opened, "Different instructions with the old message ID")["status"] == "request_conflict"
    assert runtime.record_path.read_bytes() == before
    distinct = clarify(runtime, opened, request_id="message-2")
    assert distinct["clarifications"] == ["Keep the revised scope."] * 2
    assert distinct["task_revision"] != first["task_revision"]
    receipt = runtime._records()["cycles"][opened["cycle_id"]]["clarification_requests"]["message-1"]
    assert receipt == {"index": 0, "text_hash": hashlib.sha256(b"Keep the revised scope.").hexdigest()}


def test_reading_stale_proposal_preserves_review_but_does_not_offer_apply(runtime):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    before = runtime.proposal_read(opened["cycle_id"], proposal["proposal_id"])
    changed = clarify(runtime, opened)
    read = runtime.proposal_read(opened["cycle_id"], proposal["proposal_id"])
    assert read["status"] == "task_changed"
    assert read["review_changes"] == before["review_changes"]
    assert read["next_call"]["operation"] == "workspace.status"
    assert read["current_task_revision"] == changed["task_revision"]
    assert read["prepared_task_revision"] == opened["task_revision"]


def test_legacy_cycle_receipt_does_not_rewrite_original_history(runtime):
    opened = runtime.startup("invocation", user_request="Original")
    records = runtime._records()
    cycle = records["cycles"][opened["cycle_id"]]
    cycle.pop("clarification_requests")
    cycle["clarifications"] = ["An existing exact clarification"]
    runtime._persist(records)
    before = runtime.record_path.read_bytes()
    receipt = runtime.status(opened["cycle_id"])
    assert receipt["clarifications"] == ["An existing exact clarification"]
    assert runtime.record_path.read_bytes() == before
    appended = clarify(runtime, opened)
    assert appended["clarifications"] == ["An existing exact clarification", "Keep the revised scope."]


def test_schema_requires_revision_and_clarification_request_id():
    from workspace.operations import _ARGUMENT_MODELS
    with pytest.raises(ValidationError, match="expected_task_revision"):
        _ARGUMENT_MODELS["proposal_prepare"].model_validate({"cycle_id": "cycle", "changes": []})
    with pytest.raises(ValidationError, match="request_id"):
        _ARGUMENT_MODELS["clarification_add"].model_validate({"cycle_id": "cycle", "text": "Exact"})
    with pytest.raises(ValidationError, match="expected_task_revision"):
        _ARGUMENT_MODELS["instruction_bind_prepare"].model_validate({
            "cycle_id": "cycle", "role": "agents", "instruction_id": "rules", "expected_revision": "revision",
        })


def test_stale_preparation_writes_no_proposal_or_material(runtime):
    opened = runtime.startup("invocation")
    current = clarify(runtime, opened)
    before = runtime.record_path.read_bytes()
    result = draft(runtime, opened)
    assert result["status"] == "task_changed"
    assert result["knowledge_changed"] is False
    assert result["task_revision"] == current["task_revision"]
    assert result["clarifications"] == current["clarifications"]
    assert result["next_call"]["operation"] == "workspace.status"
    assert runtime.record_path.read_bytes() == before
    assert not runtime._tmp(opened["cycle_id"]).exists()
    assert runtime.list()["total"] == 1


def test_clarification_blocks_apply_until_draft_is_prepared_again(runtime):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    assert proposal["task_revision"] == opened["task_revision"]
    assert proposal["clarification_count"] == 0
    review = runtime.proposal_read(opened["cycle_id"], proposal["proposal_id"])
    assert opened["task_revision"] in review["review"]
    assert review["task_revision"] == opened["task_revision"]
    current = clarify(runtime, opened)
    before = runtime.record_path.read_bytes()
    conflict = apply(runtime, opened, proposal)
    assert conflict["status"] == "task_changed"
    assert conflict["new_clarifications"] == current["clarifications"]
    assert conflict["prepared_task_revision"] == opened["task_revision"]
    assert conflict["current_task_revision"] == current["task_revision"]
    assert runtime.record_path.read_bytes() == before
    assert runtime.list()["total"] == 1
    fresh = draft(runtime, {**opened, **current}, proposal_id=proposal["proposal_id"])
    assert fresh["revision"] != proposal["revision"]
    assert fresh["task_revision"] == current["task_revision"]
    assert fresh["clarification_count"] == 1
    with pytest.raises(PCWError, match="exact presented"):
        apply(runtime, opened, proposal)
    assert apply(runtime, opened, fresh)["status"] == "applied"


def test_other_cycles_and_temporary_material_do_not_change_assignment(runtime):
    opened = runtime.startup("invocation", user_request="First assignment")
    other = runtime.startup("other", user_request="Second assignment")
    proposal = draft(runtime, opened)
    temporary = runtime.tmp_write(opened["cycle_id"], "Working observation")
    runtime.tmp_write(opened["cycle_id"], "Updated observation", temporary["id"], temporary["revision"])
    clarify(runtime, other)
    assert runtime.status(opened["cycle_id"])["task_revision"] == opened["task_revision"]
    assert apply(runtime, opened, proposal)["status"] == "applied"


def test_conflict_returns_only_clarifications_added_after_draft(runtime):
    opened = runtime.startup("invocation")
    first = clarify(runtime, opened, "First clarification", "first")
    proposal = draft(runtime, {**opened, **first})
    clarify(runtime, opened, "Second clarification\r\n", "second")
    prepared_conflict = apply(runtime, opened, proposal)
    stale_prepare = draft(runtime, {**opened, **first})
    for result in (prepared_conflict, stale_prepare):
        assert result["status"] == "task_changed"
        assert result["new_clarifications"] == ["Second clarification\r\n"]
        assert result["next_action"] == "reprepare_proposal"


def test_instruction_binding_cannot_bypass_task_revision(runtime):
    opened = runtime.startup("invocation")
    current = clarify(runtime, opened)
    project = runtime.project_read()
    document = {"path": "agent-rules.md", "title": "Agent rules",
                "description": "Rules for delegated work", "content": "Complete rules.\n"}
    with patch("workspace.instruction_bindings.library", side_effect=AssertionError("No staging before revision check")):
        result = runtime.instruction_bind_prepare(
            opened["cycle_id"], "agents", "agent-rules", project["revision"],
            new_instruction=document, expected_task_revision=opened["task_revision"],
        )
    assert result["status"] == "task_changed"
    proposal = runtime.instruction_bind_prepare(
        opened["cycle_id"], "agents", "agent-rules", project["revision"],
        new_instruction=document, expected_task_revision=current["task_revision"],
    )
    assert proposal["status"] == "prepared"
    assert proposal["task_revision"] == current["task_revision"]
    assert proposal["clarification_count"] == 1
    assert proposal["approval_required"] is True
    from workspace.context_document import library
    assert not (library(runtime).root / document["path"]).exists()
    clarify(runtime, opened, "Do not bind this rule yet", "second")
    assert apply(runtime, opened, proposal)["status"] == "task_changed"
    assert not (library(runtime).root / document["path"]).exists()


@pytest.mark.parametrize("state", ["partial", "applying", "index_pending"])
def test_started_write_keeps_exact_recovery_after_clarification(runtime, state):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    if state == "index_pending":
        with patch.object(runtime.engine, "_get_chunker", side_effect=RuntimeError("offline")):
            assert apply(runtime, opened, proposal)["status"] == "index_pending"
    else:
        write = runtime.engine._write

        def interrupt_after_write(path, text):
            write(path, text)
            if path.parent == runtime.engine.knowledge:
                raise OSError("Injected interruption after durable material write")

        with patch.object(runtime.engine, "_write", side_effect=interrupt_after_write):
            assert apply(runtime, opened, proposal)["status"] == "partial"
        if state == "applying":
            # Simulate process death before the exception receipt was persisted.
            records = runtime._records()
            records["cycles"][opened["cycle_id"]]["proposals"][proposal["proposal_id"]]["state"] = state
            runtime._persist(records)
    clarify(runtime, opened)
    assert apply(runtime, opened, proposal)["status"] == "applied"
    assert runtime.list()["total"] == 2
    assert apply(runtime, opened, proposal)["replayed"] is True


def test_recovery_still_rejects_external_material_edit(runtime):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    with patch.object(runtime.engine, "_get_chunker", side_effect=RuntimeError("offline")):
        assert apply(runtime, opened, proposal)["status"] == "index_pending"
    clarify(runtime, opened)
    material = runtime.read(material_id=proposal["created_ids"]["fact"])
    path = runtime.engine.root / material["path"]
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("Verified result.", "New external result."), encoding="utf-8")
    with pytest.raises(PCWError, match="conflict"):
        apply(runtime, opened, proposal)
    assert "New external result." in path.read_text(encoding="utf-8")


def test_old_unstarted_proposal_requires_reprepare_without_rewriting_review(runtime):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    records = runtime._records()
    cycle = records["cycles"][opened["cycle_id"]]
    entry = cycle["proposals"][proposal["proposal_id"]]
    plan = runtime._load_plan(opened["cycle_id"], proposal["proposal_id"], entry)
    plan.pop("task_revision")
    old = runtime._save_plan(records, cycle, plan, "prepared")
    path = runtime._plan_path(opened["cycle_id"], proposal["proposal_id"])
    before = path.read_bytes(), path.with_suffix(".md").read_bytes()
    result = apply(runtime, opened, old)
    assert result["status"] == "reprepare_required"
    assert result["prepared_task_revision"] is None
    assert (path.read_bytes(), path.with_suffix(".md").read_bytes()) == before
    fresh = draft(runtime, opened, proposal_id=proposal["proposal_id"])
    assert apply(runtime, opened, fresh)["status"] == "applied"


def test_old_started_proposal_without_task_revision_remains_recoverable(runtime):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    records = runtime._records()
    cycle = records["cycles"][opened["cycle_id"]]
    entry = cycle["proposals"][proposal["proposal_id"]]
    plan = runtime._load_plan(opened["cycle_id"], proposal["proposal_id"], entry)
    plan.pop("task_revision")
    plan.pop("clarification_count")
    old = runtime._save_plan(records, cycle, plan, "applying")
    clarify(runtime, opened)
    assert apply(runtime, opened, old)["status"] == "applied"
    assert apply(runtime, opened, old)["replayed"] is True


def test_task_revision_is_bound_by_proposal_digest(runtime):
    opened = runtime.startup("invocation")
    proposal = draft(runtime, opened)
    path = runtime._plan_path(opened["cycle_id"], proposal["proposal_id"])
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["task_revision"] = "forged"
    path.write_text(_json(plan), encoding="utf-8")
    with pytest.raises(PCWError, match="Prepared proposal was changed"):
        apply(runtime, opened, proposal)


@pytest.mark.parametrize("legacy_pending", [False, True])
def test_base_install_retry_does_not_stamp_clarified_assignment(base, legacy_pending):
    revision = base.plan()["plan_revision"]

    def interrupt(instance, cycle_id, *args, **kwargs):
        instance.clarification_add(cycle_id, "The reviewed bundle scope has changed.", "changed-scope")
        raise PCWError("Injected interruption before proposal preparation")

    with patch.object(CycleRuntime, "proposal_prepare", interrupt):
        assert base.install(revision)["status"] == "needs_review"
    state = base._read()
    original_revision = state["pending"]["task_revision"]
    if legacy_pending:
        state["pending"].pop("task_revision")
        base._write(state)
    result = base.install(revision)
    assert result["status"] == ("needs_review" if legacy_pending else "task_changed")
    if not legacy_pending:
        assert result["prepared_task_revision"] == original_revision
        assert result["current_task_revision"] != original_revision
    instance = base.runtime_factory(base.layout.root, state["pending"]["plan"]["context"])
    try:
        assert instance.list()["total"] == 1
        assert instance.status(state["pending"]["cycle_id"])["outstanding_proposal_count"] == 0
    finally:
        instance.close()
