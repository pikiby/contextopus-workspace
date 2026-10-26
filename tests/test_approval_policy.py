"""Workspace approval policy with real cycle storage and deterministic vectors."""
from unittest.mock import patch

import pytest

from pcw_runtime import PCWError
from pcw_runtime.server import pcw_query
from test_cycles import workspace
from test_instructions import edit, apply as apply_instructions
from pcw_runtime.policies import policies


def policy(required):
    return f"---\nknowledge_approval_required: {str(required).lower()}\n---\n# Project\n"


def prepare(r, c, text="# Facts\nAccurate project description"):
    return r.proposal_prepare(c, expected_task_revision=r.status(c)["task_revision"], changes=[{"key": "topic", "content": text,
                                  "description": "Project facts used to test approval and partial-write recovery"}])


def set_policy(r, required):
    preview = r.settings_preview({"knowledge_approval_required": required}, "Change knowledge approval policy")
    return r.settings_apply(preview["preview_revision"], "Approve the shown settings revision")


def apply_knowledge(r, c, p, **kwargs):
    return r.proposal_apply(c, p["proposal_id"], p["revision"], **kwargs)


def test_default_shows_changes_then_applies_indexes_and_closes_without_confirmation(workspace):
    r, _ = workspace
    opened = r.startup("auto")
    c = opened["cycle_id"]
    assert r.settings_read(fields=["knowledge_approval_required"])["values"]["knowledge_approval_required"] is False
    r.tmp_write(c, "Verified result")
    p = prepare(r, c)
    assert p["approval_required"] is False
    assert p["next_action"] == "present_changes_in_chat_then_apply"
    assert p["review_changes"][0]["body_changes"][0]["after"] == "# Facts\nAccurate project description"
    assert r.proposal_read(c, p["proposal_id"])["approval_required"] is False
    assert r.status(c)["materials"] == 1
    assert r.finish(c)["status"] == "blocked"
    result = apply_knowledge(r, c, p)
    assert result["status"] == "applied" and result["materials"][0]["index_status"] == "indexed"
    assert r._tmp(c).exists()
    assert apply_knowledge(r, c, p)["replayed"]
    assert r.finish(c)["tmp_cleared"]


@pytest.mark.parametrize("content", ["# Explicit manual approval", "# Existing custom rules", "", "# Rules\nknowledge_approval_required: false"])
def test_manual_policy_remains_authoritative_under_any_instruction_text(workspace, content):
    r, f = workspace
    c = r.startup("manual")["cycle_id"]
    set_policy(r, True)
    apply_instructions(r, c, edit(r, c, content))
    p = prepare(r, c)
    assert p["approval_required"] is True
    assert p["next_action"] == "present_changes_in_chat_for_approval"
    for confirmation in (None, "", " "):
        with pytest.raises(PCWError, match="confirmation"):
            apply_knowledge(r, c, p, confirmation=confirmation)
    assert r.status(c)["materials"] == 1 and f.calls == []
    assert apply_knowledge(r, c, p, confirmation="Approve the shown text")["status"] == "applied"


@pytest.mark.parametrize("required", [False, True])
def test_instructions_always_require_approval_and_cannot_use_knowledge_apply(workspace, required):
    r, _ = workspace
    c = r.startup("rules")["cycle_id"]
    set_policy(r, required)
    apply_instructions(r, c, edit(r, c, "# Project\nCurrent user rules."))
    before = r.instructions_read(c)
    p = edit(r, c, "# Project\nProposed user rules.")
    assert p["approval_required"] is True
    assert p["next_action"] == "present_changes_in_chat_for_approval"
    with pytest.raises(PCWError, match="confirmation"):
        r.instructions_apply(c, p["proposal_id"], p["revision"], "")
    with pytest.raises(PCWError, match="instructions_apply"):
        apply_knowledge(r, c, p)
    assert r.instructions_read(c) == before


@pytest.mark.parametrize("header", [
    'knowledge_approval_required: "false"', 'knowledge_approval_required: null',
    'knowledge_approval_required: 0', 'knowledge_approval_required: no',
    'knowledge_approval_required: [false]',
    'knowledge_approval_required: true\nknowledge_approval_required: false',
    '- false', 'knowledge_approval_required: [',
])
def test_invalid_policy_fails_without_saving_a_proposal_or_changing_instructions(workspace, header):
    r, _ = workspace
    c = r.startup("invalid")["cycle_id"]
    before = r.instructions_read(c)
    with pytest.raises(PCWError):
        policies("---\n" + header + "\n---\n")
    assert r.instructions_read(c) == before
    assert r.status(c)["materials"] == 1
    assert r.status(c)["outstanding_proposal_count"] == 0


@pytest.mark.parametrize("required", [False, True])
def test_policy_change_invalidates_old_preparation_in_both_directions(workspace, required):
    r, _ = workspace
    c = r.startup("change")["cycle_id"]
    set_policy(r, required)
    p = prepare(r, c)
    set_policy(r, not required)
    with pytest.raises(PCWError, match="Settings changed"):
        apply_knowledge(r, c, p, confirmation="yes")
    assert r.status(c)["materials"] == 1
    renewed = r.proposal_prepare(c, expected_task_revision=r.status(c)["task_revision"], changes=[{"key": "topic", "content": "Fact",
                                     "description": "Reviewed fact under the changed approval policy"}], proposal_id=p["proposal_id"])
    assert renewed["approval_required"] is not required


def test_automatic_retry_recovers_same_partial_write_after_policy_changes(workspace):
    r, _ = workspace
    c = r.startup("recovery")["cycle_id"]
    p = prepare(r, c)
    write = r.engine._write
    def fail(path, text):
        write(path, text)
        if path.parent == r.engine.knowledge:
            raise OSError("write acknowledgement lost")
    with patch.object(r.engine, "_write", side_effect=fail):
        assert apply_knowledge(r, c, p)["status"] == "partial"
    set_policy(r, True)
    assert apply_knowledge(r, c, p)["status"] == "applied"
    assert r.status(c)["materials"] == 2
    # A completed recovery has no unfinished proposal; the fixture explicitly
    # disables cleanup approval so knowledge policy cannot gate cycle closure.
    assert r.finish(c)["status"] == "closed"


def test_auto_mode_still_checks_file_conflicts_and_full_revision(workspace):
    r, _ = workspace
    c = r.startup("conflict")["cycle_id"]
    seed = prepare(r, c)
    apply_knowledge(r, c, seed)
    material = r.read(c, seed["created_ids"]["topic"])
    p = r.proposal_prepare(c, expected_task_revision=r.status(c)["task_revision"], changes=[{"material_id": material["id"], "expected_revision": material["revision"],
                               "content": "Updated fact", "description": "Updated facts with a concurrent-write conflict"}])
    with pytest.raises(PCWError, match="exact presented"):
        r.proposal_apply(c, p["proposal_id"], "wrong")
    path = r.engine.root / material["path"]
    path.write_text(path.read_text() + "\nConcurrent edit")
    with pytest.raises(PCWError, match="conflict"):
        apply_knowledge(r, c, p)
    assert "Concurrent edit" in path.read_text()


def test_mcp_schema_allows_omitting_confirmation_under_runtime_policy():
    knowledge = pcw_query("describe", arguments={"operation": "proposal_apply"})["arguments_schema"]
    instructions = pcw_query("describe", arguments={"operation": "instructions_apply"})["arguments_schema"]
    assert "confirmation" not in knowledge["required"]
    assert "confirmation" not in instructions["required"]
    assert "knowledge_approval_required" not in knowledge["properties"]


@pytest.mark.parametrize("value", ["false", None, 0, "no", [False], {"required": False}])
def test_invalid_canonical_policy_has_no_settings_or_proposal_effect(workspace, value):
    r, _ = workspace
    before = r.settings_read()
    with pytest.raises(PCWError, match="boolean"):
        r.settings_preview({"knowledge_approval_required": value}, "Change approval")
    assert r.settings_read() == before
    assert not list((r.control.root / "settings").glob("*.json"))


def test_missing_canonical_policy_cannot_authorize_a_write(workspace):
    from contextopus.core.settings import Settings
    r, _ = workspace
    cycle = r.startup("lost-policy")["cycle_id"]
    files = {path: path.read_bytes() for path in r.engine.knowledge.rglob("*.md")}
    Settings(r.control.layout)._scope_path("workspace", r.engine.workspace).unlink()
    with pytest.raises(PCWError, match="settings are missing"):
        prepare(r, cycle)
    assert files == {path: path.read_bytes() for path in r.engine.knowledge.rglob("*.md")}
    assert r._records()["cycles"][cycle]["proposals"] == {}


def test_new_instruction_frontmatter_cannot_change_machine_policy(workspace):
    r, _ = workspace
    cycle = r.startup("reserved-policy")["cycle_id"]
    before = r.instructions_read()
    with pytest.raises(PCWError, match="workspace.settings_preview/settings_apply"):
        edit(r, cycle, policy(True))
    assert r.instructions_read() == before
    assert not r.settings_read(fields=["knowledge_approval_required"])["values"]["knowledge_approval_required"]
