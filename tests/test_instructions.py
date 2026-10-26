"""Persistent instructions: real storage with deterministic embedding fixtures."""
from importlib.resources import files
from unittest.mock import patch
import pytest
from pcw_runtime import PCWError
from pcw_runtime.cycles import CycleRuntime
from test_cycles import workspace
from test_runtime import Factory
from workspace.context_document import library

def external_rules(r, text):
    store = library(r)
    doc = store.read("workspace." + r.engine.workspace)
    return store.save(doc["path"], doc["metadata"], text, doc["revision"])


def edit(r, c, text, proposal_id=None):
    return r.instructions_prepare(c, text, r.instructions_read(c)["revision"], "Change these rules", proposal_id)


def apply(r, c, p):
    return r.instructions_apply(c, p["proposal_id"], p["revision"], "Apply the shown instructions")


def test_startup_delivers_defaults_without_current_or_embeddings(workspace):
    r, f = workspace
    opened = r.startup("first")
    text = files("workspace").joinpath("default_instructions.md").read_text(encoding="utf-8")
    from workspace.policies import extract_settings
    assert opened["instructions"]["content"] == extract_settings(text)[1]
    assert opened["instructions"] == r.instructions_read(opened["cycle_id"])
    assert "current" not in opened and not hasattr(r, "current_set")
    assert not (r._tmp(opened["cycle_id"]) / "current.md").exists()
    assert opened["materials"] == 1 and len(f.instances) == 1
    assert f.calls == []  # Startup does not load or embed anything after creation.
    assert r.search(opened["cycle_id"], "principles", types=["knowledge"])["results"] == []


def test_custom_instructions_survive_closure_restart_and_reopening(workspace):
    r, _ = workspace
    a = r.startup("a", purpose="instructions")["cycle_id"]
    text = "# My project\nOnly my custom rules.\n"
    assert apply(r, a, edit(r, a, text))["status"] == "applied"
    assert r.finish(a)["status"] == "closed"
    b = r.startup("b")
    assert b["instructions"]["content"] == text
    assert r.startup("b")["instructions"]["content"] == text
    c = r.startup("restart", restart_cycle_id=b["cycle_id"])
    assert c["instructions"]["content"] == text
    assert r.finish(c["cycle_id"], cancel=True)["status"] == "cancelled"
    reopened = CycleRuntime(r.engine.work_root, "project", provider_factory=Factory())
    try:
        assert reopened.startup("next")["instructions"]["content"] == text
    finally:
        reopened.close()


def test_prepare_waits_for_approval_and_keeps_arbitrary_markdown_exact(workspace):
    r, f = workspace
    c = r.startup("edit", purpose="instructions")["cycle_id"]
    old = r.instructions_read(c)
    text = "---\r\nlinks: arbitrary user text\r\n---\r\n# Цель 🙂\r\n\r\n| A | B |\r\n|---|---|\r\n| 1 | 2 |\r\n```\r\n# Not a field\r\n```\r\n"
    p = edit(r, c, text)
    assert r.instructions_read(c) == old
    assert r.finish(c)["status"] == "blocked"
    assert p["kind"] == "instructions" and p["effects"][0]["links_added"] == []
    assert "links: arbitrary user text" in "".join(x["after"] for x in p["review_changes"][0]["body_changes"])
    assert r.proposal_read(c, p["proposal_id"])["review_changes"] == p["review_changes"]
    with pytest.raises(PCWError, match="confirmation"):
        r.instructions_apply(c, p["proposal_id"], p["revision"], "")
    assert apply(r, c, p)["status"] == "applied"
    assert r.instructions_read()["content"].encode() == text.encode()
    assert r.instructions_read(c)["content"] == text
    assert apply(r, c, p)["replayed"]
    assert f.calls == [] and r.status(c)["materials"] == 1
    assert r.finish(c)["tmp_cleared"]
    assert not r._tmp(c).exists()
    assert "arbitrary user text" not in r.record_path.read_text()


def test_revised_draft_and_external_edit_reject_old_approval(workspace):
    r, _ = workspace
    c = r.startup("edit", purpose="instructions")["cycle_id"]
    p = edit(r, c, "first")
    q = edit(r, c, "second", p["proposal_id"])
    with pytest.raises(PCWError, match="exact presented"):
        apply(r, c, p)
    external_rules(r, "Human's newer rules")
    with pytest.raises(PCWError, match="Instructions conflict"):
        apply(r, c, q)
    assert r.instructions_read(c)["content"] == "Human's newer rules"
    assert apply(r, c, edit(r, c, "Agreed revision", q["proposal_id"]))["status"] == "applied"


def test_parallel_edits_conflict_and_startup_does_not_reset_file(workspace):
    r, _ = workspace
    a = r.startup("a", purpose="instructions")["cycle_id"]
    b = r.startup("b", purpose="instructions")["cycle_id"]
    p, q = edit(r, a, "A"), edit(r, b, "B")
    apply(r, a, p)
    with pytest.raises(PCWError, match="conflict"):
        apply(r, b, q)
    assert r.startup("c")["instructions"]["content"] == "A"
    assert r.finish(a)["status"] == "closed"
    assert r.proposal_read(b, q["proposal_id"])


def test_instructions_mode_cannot_run_work_or_apply_knowledge(workspace):
    r, _ = workspace
    c = r.startup("edit", purpose="instructions")["cycle_id"]
    assert r.search(c, "work")["results"] == []  # Independent reading is allowed.
    for operation in (lambda: r.tmp_write(c, "work"),
                      lambda: r.proposal_prepare(c, [], no_changes_reason="bypass", expected_task_revision=r.status(c)["task_revision"])):
        with pytest.raises(PCWError, match="only for instructions"):
            operation()
    assert not r._tmp(c).exists()  # Instructions-only invocation requires no fabricated proposal.
    with pytest.raises(PCWError, match="direct request"):
        r.instructions_prepare(c, "new", r.instructions_read(c)["revision"], "")
    with pytest.raises(PCWError, match="different invocation"):
        r.startup("edit", purpose="work")


def test_rejection_and_unchanged_instructions_need_no_knowledge_write(workspace):
    r, f = workspace
    a = r.startup("a", purpose="instructions")["cycle_id"]
    old = r.instructions_read(a)
    p = edit(r, a, "Rejected")
    r.proposal_discard(a, p["proposal_id"], p["revision"])
    assert r.finish(a)["status"] == "closed"
    b = r.startup("b", purpose="instructions")["cycle_id"]
    assert edit(r, b, old["content"])["status"] == "no_changes"
    assert r.finish(b)["status"] == "closed"
    assert f.calls == [] and r.instructions_read()["content"] == old["content"]


def test_instruction_edit_does_not_cover_work_review_or_cross_apply(workspace):
    r, _ = workspace
    c = r.startup("work")["cycle_id"]
    r.tmp_write(c, "Useful observation")
    k = r.proposal_prepare(c, [], no_changes_reason="No changes under old rules", expected_task_revision=r.status(c)["task_revision"])
    p = edit(r, c, "Updated rules")
    with pytest.raises(PCWError, match="instructions_apply"):
        r.proposal_apply(c, p["proposal_id"], p["revision"], "yes")
    with pytest.raises(PCWError):
        apply(r, c, k)
    with pytest.raises(PCWError, match="kind"):
        edit(r, c, "other", k["proposal_id"])
    apply(r, c, p)
    assert r.finish(c)["status"] == "closed"  # New rules do not require a fabricated no-change review.


def test_changed_instructions_block_pending_knowledge_proposal(workspace):
    r, _ = workspace
    c = r.startup("work")["cycle_id"]
    p = r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "a", "content": "fact"}], expected_task_revision=r.status(c)["task_revision"])
    apply(r, c, edit(r, c, "New policy"))
    with pytest.raises(PCWError, match="Instructions changed"):
        r.proposal_apply(c, p["proposal_id"], p["revision"], "yes")
    assert r.status(c)["materials"] == 1


def test_partial_write_can_retry_but_cannot_overwrite_a_later_edit(workspace):
    r, _ = workspace
    c = r.startup("edit", purpose="instructions")["cycle_id"]
    p = edit(r, c, "New instructions")
    store_type = type(library(r))
    write = store_type.save
    def fail_after_write(store, *args, **kwargs):
        write(store, *args, **kwargs)
        raise OSError("interrupted after durable write")
    with patch.object(store_type, "save", fail_after_write):
        assert apply(r, c, p)["status"] == "partial"
    assert r.finish(c)["status"] == "blocked"
    assert apply(r, c, p)["status"] == "applied"
    q = edit(r, c, "Another revision")
    with patch.object(store_type, "save", fail_after_write):
        assert apply(r, c, q)["status"] == "partial"
    external_rules(r, "Manual newer revision")
    with pytest.raises(PCWError, match="conflict"):
        apply(r, c, q)


def test_missing_instructions_are_not_reset_and_symlinks_are_rejected(workspace, tmp_path):
    r, _ = workspace
    c = r.startup("first")["cycle_id"]
    path = library(r).root / ("workspace." + r.engine.workspace + ".md")
    path.unlink()
    with pytest.raises(PCWError, match="[Mm]issing"):
        r.startup("second")
    with pytest.raises(PCWError, match="[Mm]issing"):
        r.instructions_read(c)
    assert not path.exists()
    outside = tmp_path / "outside.md"
    outside.write_text("keep")
    path.symlink_to(outside)
    with pytest.raises(PCWError, match="[Ss]ymlinks"):
        r.startup("third")
    assert outside.read_text() == "keep"


def test_empty_instructions_are_a_user_choice_not_default_fallback(workspace):
    r, _ = workspace
    c = r.startup("edit", purpose="instructions")["cycle_id"]
    assert apply(r, c, edit(r, c, ""))["status"] == "applied"
    assert r.startup("next")["instructions"]["content"] == ""


def test_started_knowledge_write_can_recover_after_rule_change_without_fake_closure_review(workspace):
    r, _ = workspace
    c = r.startup("work")["cycle_id"]
    p = r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "a", "content": "Fact already approved"}], expected_task_revision=r.status(c)["task_revision"])
    write = r.engine._write
    def interrupt(path, text):
        write(path, text)
        if path.parent == r.engine.knowledge:
            raise OSError("lost write acknowledgement")
    with patch.object(r.engine, "_write", side_effect=interrupt):
        assert r.proposal_apply(c, p["proposal_id"], p["revision"], "yes")["status"] == "partial"
    apply(r, c, edit(r, c, "Changed project instructions"))
    assert r.proposal_apply(c, p["proposal_id"], p["revision"], "yes")["status"] == "applied"
    assert r.finish(c)["status"] == "closed"
