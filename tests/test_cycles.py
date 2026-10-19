"""0.9 cycles: real files and LanceDB; only embedding vectors are injected."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_runtime import Factory
from pcw_runtime import PCWError
from pcw_runtime.cycles import CycleRuntime
from test_workspace_v3 import create


@pytest.fixture
def workspace(tmp_path):
    factory = Factory()
    runtime = CycleRuntime(tmp_path, "project", provider_factory=factory)
    create(runtime, cleanup_approval_required=False)  # Most cases exercise knowledge approval; cleanup policy has dedicated tests.
    factory.calls.clear()  # Measure the cycle, excluding initial project indexing.
    yield runtime, factory
    runtime.close()


def start(runtime, request="user-request", **kwargs):
    return runtime.startup(request, **kwargs)["cycle_id"]


def prepare(runtime, cycle, text="Verified fact", **kwargs):
    return runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "topic", "content": text}], **kwargs)


def apply(runtime, cycle, proposal):
    return runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"], "Apply the shown changes")


def test_mutations_need_cycle_and_startup_never_loads_old_current(workspace):
    runtime, factory = workspace
    with pytest.raises(PCWError, match="No PCW cycle"):
        runtime.status("invented")
    assert not runtime.record_path.exists()
    assert runtime.list()["total"] == 1
    legacy = runtime.engine.root / "current.md"
    cycle = start(runtime)
    legacy.write_text("Old instruction: write anything")
    assert "Old instruction" not in runtime.instructions_read(cycle)["content"]
    assert legacy.read_text() == "Old instruction: write anything"
    assert len(factory.instances) == 1
    assert runtime.status(cycle)["materials"] == 1


def test_temporary_results_never_enter_search_or_knowledge(workspace):
    runtime, factory = workspace
    cycle = start(runtime)
    saved = runtime.tmp_write(cycle, "A hypothesis, not established")
    assert runtime.tmp_read(cycle, saved["id"])["content"] == "A hypothesis, not established"
    assert runtime.tmp_list(cycle)["total"] == 1
    assert runtime.status(cycle)["materials"] == 1
    assert factory.calls == []
    assert runtime.search(cycle, "hypothesis", types=["knowledge"])["results"] == []
    runtime.tmp_write(cycle, "New observation", saved["id"], saved["revision"])
    with pytest.raises(PCWError, match="changed"):
        runtime.tmp_write(cycle, "Lost update", saved["id"], saved["revision"])


def test_preparation_and_revision_changes_do_not_write_knowledge(workspace):
    runtime, factory = workspace
    cycle = start(runtime)
    preview = runtime.settings_preview({"knowledge_approval_required": True}, "Require knowledge approval")
    runtime.settings_apply(preview["preview_revision"], "Approve")
    old = prepare(runtime, cycle)
    new = prepare(runtime, cycle, "Corrected proposal", proposal_id=old["proposal_id"])
    assert runtime.status(cycle)["materials"] == 1
    assert factory.calls == []
    assert "Corrected proposal" in runtime.proposal_read(cycle, new["proposal_id"])["review"]
    with pytest.raises(PCWError, match="exact presented"):
        apply(runtime, cycle, old)
    with pytest.raises(PCWError, match="confirmation"):
        runtime.proposal_apply(cycle, new["proposal_id"], new["revision"], "")
    assert apply(runtime, cycle, new)["status"] == "applied"
    assert runtime.status(cycle)["materials"] == 2


def test_preparation_returns_full_new_text_without_needing_review_file(workspace):
    runtime, factory = workspace
    cycle = start(runtime)
    text = "# Документация\r\n\r\n| File | Language |\r\n| --- | --- |\r\n| README | English |\r\n\r\n```text\r\nKeep verbatim 🙂\r\n```\r\n"
    proposal = prepare(runtime, cycle, text)
    change, = proposal["review_changes"]
    passage, = change["body_changes"]
    assert change["title"] == "Документация"
    assert change["action"] == "create"
    assert passage["before"] == ""
    assert passage["after"] == text
    assert proposal["next_action"] == "present_changes_in_chat_then_apply"
    assert proposal["approval_required"] is False
    assert runtime.proposal_read(cycle, proposal["proposal_id"])["review_changes"] == proposal["review_changes"]
    assert runtime.status(cycle)["materials"] == 1
    assert factory.calls == []


def test_review_returns_only_changed_passages_with_local_context(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    # This scenario deliberately uses a very large document to check compact
    # review output. Explicitly disable the new-context size limit for it.
    draft = runtime.settings_preview({"material_max_tokens": None},
                                     "Disable the size limit for this large-document review test")
    runtime.settings_apply(draft["preview_revision"], "Approve these test settings")
    text = "# Service\n\n" + "".join(f"Unchanged section {i}\n" for i in range(500))
    seed = prepare(runtime, cycle, text)
    apply(runtime, cycle, seed)
    material = runtime.read(cycle, seed["created_ids"]["topic"])
    addition = "\n## Local configuration\n\nConfigured only; live state has not been checked.\n"
    proposal = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": material["id"],
        "expected_revision": material["revision"], "content": text + addition}])
    change, = proposal["review_changes"]
    passage, = change["body_changes"]
    assert change["title"] == "Service"
    assert passage["before"] == ""
    assert passage["after"] == addition
    assert "Unchanged section 499" in passage["context_before"]
    assert "Unchanged section 0" not in json.dumps(proposal["review_changes"])
    assert sum(len(part["after"]) + len(part["context_before"]) for part in change["body_changes"]) < 250
    assert runtime.read(cycle, material["id"])["content"] == text


def test_review_names_deleted_moved_and_automatically_redirected_topics(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    seed = runtime.proposal_prepare(cycle, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "old", "content": "# Old details\n\nPrevious evidence."},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "kept", "content": "# Shared topic\n\nVerified evidence."},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "nav", "content": "# Navigation", "links": ["@old"]}])
    apply(runtime, cycle, seed)
    old, kept, nav = [runtime.read(cycle, seed["created_ids"][key]) for key in ("old", "kept", "nav")]
    proposal = runtime.proposal_prepare(cycle, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": kept["id"], "expected_revision": kept["revision"],
         "content": "# Combined topic\n\nVerified evidence. Previous evidence.", "path": "topics/" + kept["id"]},
        {"material_id": old["id"], "expected_revision": old["revision"], "delete": True}
    ], replacements={old["id"]: kept["id"]})
    changes = {change["id"]: change for change in proposal["review_changes"]}
    assert changes[old["id"]]["body_changes"][0]["before"] == old["content"]
    assert changes[old["id"]]["body_changes"][0]["after"] == ""
    assert changes[kept["id"]]["old_title"] == "Shared topic"
    assert changes[kept["id"]]["title"] == "Combined topic"
    assert changes[kept["id"]]["old_path"] == kept["path"]
    assert changes[kept["id"]]["path"] == "knowledge/topics/" + kept["id"]
    assert changes[nav["id"]]["body_changes"] == []
    assert changes[nav["id"]]["links_added"][0]["title"] == "Combined topic"
    assert changes[nav["id"]]["links_removed"][0]["title"] == "Old details"
    assert runtime.read(cycle, nav["id"])["links"] == [old["id"]]


def test_review_does_not_drop_topics_when_effect_receipt_is_bounded(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    proposal = runtime.proposal_prepare(cycle, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": f"topic-{i}", "content": f"# Topic {i}\n\nFact {i}."} for i in range(51)])
    assert len(proposal["effects"]) == 50
    assert len(proposal["review_changes"]) == 51
    assert {change["title"] for change in proposal["review_changes"]} == {f"Topic {i}" for i in range(51)}


def test_apply_preserves_instructions_and_tmp_then_closure_removes_only_tmp(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    b = runtime.tmp_write(cycle, "Continue B from these observations")
    nested = runtime._tmp(cycle) / "nested" / "unused.txt"
    nested.parent.mkdir()
    nested.write_text("unused draft")
    proposal = prepare(runtime, cycle)
    assert apply(runtime, cycle, proposal)["status"] == "applied"
    assert runtime.tmp_read(cycle, b["id"])["content"].startswith("Continue B")
    assert (runtime.engine.root / "INSTRUCTIONS.md").is_file()
    assert runtime.finish(cycle)["status"] == "closed"
    assert not runtime._tmp(cycle).exists()
    assert runtime.engine.read(proposal["created_ids"]["topic"])["content"] == "Verified fact"
    assert "Continue B" not in runtime.record_path.read_text()
    with pytest.raises(PCWError, match="inactive"):
        runtime.read(cycle, proposal["created_ids"]["topic"])


def test_parallel_cycles_are_isolated_and_startup_retries_do_not_purge(workspace):
    runtime, _ = workspace
    a = start(runtime, "a"); b = start(runtime, "b")
    item = runtime.tmp_write(b, "B in progress")
    kept = runtime.tmp_write(a, "A in progress")
    assert runtime.startup("a")["replayed"]
    assert runtime.tmp_read(a, kept["id"])
    runtime.proposal_prepare(a, [], no_changes_reason="A contains only a temporary working note.")
    runtime.finish(a)
    assert runtime.tmp_read(b, item["id"])["content"] == "B in progress"
    assert runtime.finish(a)["replayed"]
    assert runtime.tmp_read(b, item["id"])


def test_restart_preserves_unfinished_work_until_explicit_finish(workspace):
    runtime, _ = workspace
    a = start(runtime, "a"); b = start(runtime, "b")
    old = runtime.tmp_write(a, "Old return")
    other = runtime.tmp_write(b, "Keep this")
    proposal = prepare(runtime, a)
    with pytest.raises(PCWError, match="continue it or explicitly finish"):
        start(runtime, "restart-a", restart_cycle_id=a)
    assert runtime.tmp_read(a, old["id"])["content"] == "Old return"
    assert runtime.proposal_read(a, proposal["proposal_id"])
    runtime.proposal_discard(a, proposal["proposal_id"], proposal["revision"])
    assert runtime.finish(a)["status"] == "closed"
    new = start(runtime, "restart-a", restart_cycle_id=a)
    assert not runtime._tmp(a).exists()
    assert runtime.tmp_list(new)["total"] == 0
    assert runtime.instructions_read(new)["content"]
    assert runtime.tmp_read(b, other["id"])
    with pytest.raises(PCWError):
        apply(runtime, new, proposal)
    with pytest.raises(PCWError):
        apply(runtime, a, proposal)


def test_no_changes_and_rejected_changes_close_without_dummy_save(workspace):
    runtime, factory = workspace
    a = start(runtime, "a")
    proposal = prepare(runtime, a)
    with pytest.raises(PCWError, match="cancel must be a boolean"):
        runtime.finish(a, "Old positional confirmation must not cancel pending work")
    assert runtime.finish(a)["status"] == "blocked"
    runtime.proposal_discard(a, proposal["proposal_id"], proposal["revision"])
    assert runtime.finish(a)["status"] == "closed"
    b = start(runtime, "b")
    assert not runtime._tmp(b).exists()  # Direct work needs no fabricated temporary report or no-change proposal.
    reviewed = runtime.proposal_prepare(b, [], no_changes_reason="No new results or corrections after comparison.")
    assert reviewed["status"] == "no_changes"
    assert reviewed["next_action"] == "report_no_changes"
    assert reviewed["effects"] == reviewed["review_changes"] == []
    assert runtime.status(b)["outstanding_proposal_count"] == 0
    assert runtime.finish(b)["tmp_cleared"]
    assert runtime.engine.status()["materials"] == 1
    assert factory.calls == []


def test_finish_requires_pending_decision_but_not_a_fabricated_review(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    result = runtime.tmp_write(cycle, "Verified reusable result")
    assert runtime.tmp_read(cycle, result["id"])["content"] == "Verified reusable result"
    proposal = prepare(runtime, cycle, "Verified reusable result")
    assert runtime.finish(cycle)["status"] == "blocked"
    assert runtime.engine.status()["materials"] == 1
    assert apply(runtime, cycle, proposal)["status"] == "applied"
    assert runtime.finish(cycle)["status"] == "closed"
    assert not runtime._tmp(cycle).exists()
    assert runtime.engine.read(proposal["created_ids"]["topic"])["content"] == "Verified reusable result"


@pytest.mark.parametrize("update", [False, True])
def test_temporary_churn_does_not_replace_semantic_review_or_invalidate_proposals(workspace, update):
    runtime, _ = workspace
    cycle = start(runtime)
    result = runtime.tmp_write(cycle, "Initial working note")
    runtime.proposal_prepare(cycle, [], no_changes_reason="The initial note adds no reusable knowledge.")
    args = {"material_id": result["id"], "expected_revision": result["revision"]} if update else {}
    runtime.tmp_write(cycle, "New verified finding", **args)
    assert runtime._tmp(cycle).exists()
    proposal = prepare(runtime, cycle, "New verified finding")
    runtime.tmp_write(cycle, "Unrelated later scratch note")
    assert apply(runtime, cycle, proposal)["status"] == "applied"
    assert runtime.finish(cycle)["status"] == "closed"


def test_no_change_review_requires_reason_and_does_not_override_pending_approval(workspace):
    runtime, factory = workspace
    cycle = start(runtime)
    for reason in (None, "", " "):
        with pytest.raises(PCWError, match="no_changes_reason"):
            runtime.proposal_prepare(cycle, [], no_changes_reason=reason)
    with pytest.raises(PCWError, match="only valid"):
        prepare(runtime, cycle, no_changes_reason="Conflicting claim")
    proposal = prepare(runtime, cycle)
    reviewed = runtime.proposal_prepare(cycle, [], no_changes_reason="No additional changes beyond the pending proposal.")
    assert runtime.proposal_read(cycle, reviewed["proposal_id"])["no_changes_reason"] == reviewed["no_changes_reason"]
    with pytest.raises(PCWError, match="Apply only"):
        apply(runtime, cycle, reviewed)
    assert runtime.finish(cycle)["status"] == "blocked"
    runtime.proposal_discard(cycle, proposal["proposal_id"], proposal["revision"])
    assert runtime.finish(cycle)["status"] == "closed"
    assert factory.calls == []


def test_identical_tmp_updates_do_not_invalidate_review(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    result = runtime.tmp_write(cycle, "Working note")
    runtime.proposal_prepare(cycle, [], no_changes_reason="This working note is not reusable project knowledge.")
    runtime.tmp_write(cycle, "Working note", material_id=result["id"], expected_revision=result["revision"])
    assert runtime.finish(cycle)["status"] == "closed"


def test_cancellation_can_clear_unreviewed_results_without_writing_knowledge(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    runtime.tmp_write(cycle, "Unfinished investigation cancelled by the user")
    assert runtime.finish(cycle, cancel=True)["status"] == "cancelled"
    assert not runtime._tmp(cycle).exists()
    assert runtime.engine.status()["materials"] == 1


def test_cross_links_merge_and_split_review_includes_incoming_changes(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    p = runtime.proposal_prepare(cycle, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "a", "content": "A", "links": ["@b"]},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "b", "content": "B", "links": ["@a"]},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "neighbor", "content": "Neighbor", "links": ["@b"]}])
    apply(runtime, cycle, p)
    a, b, n = [runtime.read(cycle, p["created_ids"][key]) for key in ("a", "b", "neighbor")]
    merge = runtime.proposal_prepare(cycle, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": a["id"], "expected_revision": a["revision"], "content": "A with B"},
        {"material_id": b["id"], "expected_revision": b["revision"], "delete": True}], {b["id"]: a["id"]})
    assert n["id"] in {effect["id"] for effect in merge["effects"]}
    assert runtime.read(cycle, n["id"])["links"] == [b["id"]]
    assert apply(runtime, cycle, merge)["status"] == "applied"
    assert runtime.read(cycle, n["id"])["links"] == [a["id"]]
    with pytest.raises(PCWError): runtime.read(cycle, b["id"])
    a = runtime.read(cycle, a["id"])
    split = runtime.proposal_prepare(cycle, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": a["id"], "expected_revision": a["revision"], "content": "A", "links": ["@details"]},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "details", "content": "Separate details", "links": [a["id"]]}])
    assert apply(runtime, cycle, split)["status"] == "applied"


def test_body_and_link_conflicts_require_new_proposal(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    original = prepare(runtime, cycle)
    apply(runtime, cycle, original)
    item = runtime.read(cycle, original["created_ids"]["topic"])
    p = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": item["id"], "expected_revision": item["revision"], "content": "New fact"}])
    # Competing edits are blocked before preparation. After explicit release,
    # the existing revision conflict still prevents an old proposal overwriting.
    other = start(runtime, "other")
    with pytest.raises(PCWError, match="reserved"):
        runtime.reserve(other, [item["id"]], "Concurrent edit")
    for lease in {row["reservation_id"] for row in p["reservations"]}:
        runtime.reservation_release(cycle, lease)
    q = runtime.proposal_prepare(other, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": item["id"], "expected_revision": item["revision"], "content": "Concurrent fact"}])
    apply(runtime, other, q)
    with pytest.raises(PCWError, match="conflict"):
        apply(runtime, cycle, p)
    assert runtime.read(cycle, item["id"])["content"] == "Concurrent fact"


def test_unchanged_body_and_links_only_do_not_embed_again(workspace):
    runtime, factory = workspace
    cycle = start(runtime)
    p = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "a", "content": "A"}, {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "b", "content": "B"}])
    apply(runtime, cycle, p)
    a = runtime.read(cycle, p["created_ids"]["a"])
    before = len(factory.calls)
    q = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": a["id"], "expected_revision": a["revision"],
                                        "content": "A", "links": [p["created_ids"]["b"]]}])
    apply(runtime, cycle, q)
    assert len(factory.calls) == before
    assert apply(runtime, cycle, q)["replayed"]
    assert len(factory.calls) == before


def test_index_failure_preserves_text_and_tmp_and_resumes_without_duplicate(workspace):
    runtime, factory = workspace
    cycle = start(runtime)
    proposal = prepare(runtime, cycle)
    with patch.object(runtime.engine, "_get_chunker", side_effect=RuntimeError("offline")):
        assert apply(runtime, cycle, proposal)["status"] == "index_pending"
    assert runtime._tmp(cycle).exists()
    assert runtime.finish(cycle)["status"] == "blocked"
    root = runtime.engine.work_root
    runtime.close()
    restarted_process = CycleRuntime(root, "project", provider_factory=factory)
    try:
        assert apply(restarted_process, cycle, proposal)["status"] == "applied"
        assert restarted_process.status(cycle)["materials"] == 2
        assert restarted_process.finish(cycle)["status"] == "closed"
    finally: restarted_process.close()


def test_partial_write_retry_and_move(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    proposal = prepare(runtime, cycle)
    write = runtime.engine._write
    fired = False
    def fail_after_write(path, text):
        nonlocal fired
        write(path, text)
        if path.parent == runtime.engine.knowledge and not fired:
            fired = True
            raise OSError("after write")
    with patch.object(runtime.engine, "_write", side_effect=fail_after_write):
        assert apply(runtime, cycle, proposal)["status"] == "partial"
    assert apply(runtime, cycle, proposal)["status"] == "applied"
    item = runtime.read(cycle, proposal["created_ids"]["topic"])
    move = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": item["id"], "expected_revision": item["revision"],
                                          "path": "topics/" + item["id"], "content": "Moved text"}])
    assert apply(runtime, cycle, move)["status"] == "applied"
    assert runtime.read(cycle, item["id"])["path"] == "knowledge/topics/" + item["id"]


def test_cleanup_failure_can_retry_without_touching_other_cycles(workspace):
    runtime, _ = workspace
    a = start(runtime, "a"); b = start(runtime, "b")
    tmp = runtime.tmp_write(a, "Temporary")
    runtime.proposal_prepare(a, [], no_changes_reason="Only a temporary working note; no permanent changes.")
    with patch.object(runtime, "_purge", side_effect=OSError("cleanup failed")):
        with pytest.raises(OSError): runtime.finish(a)
    assert runtime._tmp(a).exists()
    assert runtime.status(b)["cycle_status"] == "active"
    assert runtime.finish(a)["status"] == "closed"


def test_start_cleanup_failure_is_not_active_and_retry_does_not_reuse_old_context(workspace):
    runtime, _ = workspace
    old = start(runtime, "old")
    runtime.tmp_write(old, "Explicitly finish this work before restarting")
    assert runtime.finish(old)["status"] == "closed"
    with patch.object(runtime, "_purge", side_effect=OSError("cleanup failed")):
        with pytest.raises(OSError): start(runtime, "new", restart_cycle_id=old)
    with pytest.raises(PCWError): runtime.status(old)
    new = start(runtime, "new", restart_cycle_id=old)
    assert not runtime._tmp(old).exists()
    assert runtime.tmp_list(new)["total"] == 0


def test_tampered_prepared_text_cannot_be_applied(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    p = prepare(runtime, cycle)
    plan_path = runtime._plan_path(cycle, p["proposal_id"])
    plan = json.loads(plan_path.read_text())
    plan["entries"][p["created_ids"]["topic"]]["after"]["text"] = "Tampered"
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(PCWError, match="changed"):
        apply(runtime, cycle, p)
    assert runtime.status(cycle)["materials"] == 1


def test_changed_review_cannot_be_applied(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    p = prepare(runtime, cycle)
    runtime._plan_path(cycle, p["proposal_id"]).with_suffix(".md").write_text("Different shown content")
    with pytest.raises(PCWError, match="review was changed"):
        apply(runtime, cycle, p)
    assert runtime.status(cycle)["materials"] == 1


def test_crlf_and_unicode_survive_tmp_updates(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    tmp = runtime.tmp_write(cycle, "# Заголовок\r\n\r\n🙂\r\n")
    changed = runtime.tmp_write(cycle, "Changed\r\n", tmp["id"], tmp["revision"])
    assert runtime.tmp_read(cycle, changed["id"])["content"] == "Changed\r\n"


def test_changed_incoming_links_conflict_before_merge_writes(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    p = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "a", "content": "A"}, {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "b", "content": "B"},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "n", "content": "Neighbor", "links": ["@b"]}])
    apply(runtime, cycle, p)
    a, b, n = [runtime.read(cycle, p["created_ids"][key]) for key in ("a", "b", "n")]
    merge = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": a["id"], "expected_revision": a["revision"], "content": "Merged"},
        {"material_id": b["id"], "expected_revision": b["revision"], "delete": True}], {b["id"]: a["id"]})
    other = start(runtime, "other")
    with pytest.raises(PCWError, match="reserved"):
        runtime.reserve(other, [n["id"]], "Change incoming links")
    for lease in {row["reservation_id"] for row in merge["reservations"]}:
        runtime.reservation_release(cycle, lease)
    nav = runtime.proposal_prepare(other, [{"material_id": n["id"], "expected_revision": n["revision"], "links": [a["id"], b["id"]]}])
    apply(runtime, other, nav)
    with pytest.raises(PCWError, match="conflict"):
        apply(runtime, cycle, merge)
    assert runtime.read(cycle, a["id"])["content"] == "A"
    assert runtime.read(cycle, b["id"])["content"] == "B"


def test_missing_storage_returns_cycle_for_recovery_or_explicit_cancellation(workspace):
    import shutil
    runtime, _ = workspace
    a = start(runtime, "a")
    shutil.rmtree(runtime.engine.state)
    failed = runtime.startup("b")
    assert failed["status"] == "recovery_required"
    assert runtime.recover_workspace(failed["cycle_id"])["status"] == "recovered"
    assert runtime.startup("b")["cycle_id"] == failed["cycle_id"]
    shutil.rmtree(runtime.engine.knowledge)
    missing = runtime.startup("c")
    assert runtime.recover_workspace(missing["cycle_id"])["status"] == "blocked"
    assert runtime.finish(missing["cycle_id"], cancel=True)["status"] == "cancelled"


def test_symlinks_cannot_redirect_cleanup_and_tmp_cannot_be_checkpointed(workspace, tmp_path):
    runtime, _ = workspace
    cycle = start(runtime)
    outside = tmp_path / "outside"
    outside.mkdir(); (outside / "keep").write_text("keep")
    runtime.proposal_prepare(cycle, [], no_changes_reason="No task results to retain.")
    (runtime._tmp(cycle) / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PCWError, match="Symlinks"):
        runtime.finish(cycle)
    assert (outside / "keep").read_text() == "keep"
    (runtime._tmp(cycle) / "link").unlink()
    assert runtime.finish(cycle)["status"] == "closed"
    other = start(runtime, "other")
    with pytest.raises(PCWError, match="knowledge"):
        runtime.checkpoint(other, ["tmp/x.md"], "Do not persist temporary context")


def test_tmp_missing_and_stale_diagnostics_preserve_cas(workspace):
    runtime, _ = workspace
    cycle = start(runtime, 'tmp-cas-diagnostics')
    original = runtime.tmp_write(cycle, 'First observed bytes\r\n')
    current = runtime.tmp_write(cycle, 'Newer observed bytes\r\n', original['id'], original['revision'])
    with pytest.raises(PCWError, match='stale; use tmp_read'):
        runtime.tmp_write(cycle, 'Would lose the newer result', original['id'], original['revision'])
    assert runtime.tmp_read(cycle, original['id'])['content'] == 'Newer observed bytes\r\n'
    with pytest.raises(PCWError, match='missing in this cycle; use tmp_list'):
        runtime.tmp_write(cycle, 'Do not create under a caller-chosen ID', 'missing.md', current['revision'])
    assert runtime.tmp_list(cycle)['total'] == 1
    reread = runtime.tmp_read(cycle, original['id'])
    retried = runtime.tmp_write(cycle, 'Reviewed authorized replacement', original['id'], reread['revision'])
    assert retried['revision'] != current['revision']


def test_tmp_rejects_corrupted_hash_separately_from_valid_stale_hash(workspace):
    runtime, _ = workspace
    cycle = start(runtime, 'tmp-malformed-hash')
    saved = runtime.tmp_write(cycle, 'Keep these exact bytes\r\n')
    corrupted = saved['revision'][:40] + '0' + saved['revision'][40:]
    assert len(saved['revision']) == 64 and len(corrupted) == 65
    with pytest.raises(PCWError, match='exactly 64 lowercase hexadecimal characters; use tmp_read'):
        runtime.tmp_write(cycle, 'Rejected malformed input', saved['id'], corrupted)
    after_malformed = runtime.tmp_read(cycle, saved['id'])
    assert after_malformed['content'] == 'Keep these exact bytes\r\n'
    assert after_malformed['revision'] == saved['revision']
    stale = ('1' if saved['revision'][0] == '0' else '0') + saved['revision'][1:]
    assert len(stale) == 64
    with pytest.raises(PCWError, match='revision is stale; use tmp_read'):
        runtime.tmp_write(cycle, 'Rejected stale input', saved['id'], stale)
    assert runtime.tmp_read(cycle, saved['id']) == after_malformed
    retried = runtime.tmp_write(cycle, 'Authorized retry', saved['id'], after_malformed['revision'])
    assert retried['revision'] != saved['revision']


def test_proposal_typed_contract_preserves_runtime_inputs_and_heading_title(workspace):
    from workspace.operations import _ARGUMENT_MODELS

    runtime, _ = workspace
    cycle = start(runtime)
    model = _ARGUMENT_MODELS['proposal_prepare']
    change = {'key': 'typed', 'material_id': None, 'content': '# Heading title\n\nVerified fact.',
              'description': 'Reviewed subject and applicability', 'type': 'custom-kind',
              'status': None, 'links': [], 'delete': False, 'path': 'typed.md'}
    arguments = model.model_validate({'cycle_id': cycle, 'changes': [change]}).model_dump(exclude_unset=True)
    assert arguments['changes'] == [change]
    proposal = runtime.proposal_prepare(**arguments)
    reviewed, = proposal['review_changes']
    assert reviewed['title'] == 'Heading title'
    assert reviewed['path'] == 'knowledge/typed.md'
    with pytest.raises(PCWError, match='Unknown change fields'):
        runtime.proposal_prepare(cycle, [{**change, 'title': 'Unsupported'}])
    saved = apply(runtime, cycle, proposal)
    assert saved['status'] == 'applied'
    material = proposal['created_ids']['typed']
    current = runtime.read(cycle, material)
    for fields in ({'status': None}, {'delete': False}, {'delete': True}):
        update = {'material_id': material, 'expected_revision': current['revision'], **fields}
        arguments = model.model_validate({'cycle_id': cycle, 'changes': [update]}).model_dump(exclude_unset=True)
        assert arguments['changes'] == [update]
        result = runtime.proposal_prepare(**arguments)
        assert result['proposal_id']
