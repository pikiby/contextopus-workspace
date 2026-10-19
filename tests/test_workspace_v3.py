"""New contracts exercise real storage with the existing deterministic test provider."""
from importlib.resources import files
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from test_runtime import Factory
from pcw_runtime import PCWError, PCWRuntime
from pcw_runtime.cycles import CycleRuntime
from pcw_runtime.policies import policies
from pcw_runtime.workspaces import EMPTY_PROJECT
from pcw_runtime.runtime import _parse, _revision
from pcw_runtime.cycles import _json
from workspace.configuration import WORKSPACE_CREATION_DEFAULTS


def create(runtime, **settings):
    from workspace.analytics import EventStore
    store = EventStore(runtime.engine.work_root)
    plan = store.storage_prepare()
    if plan['needs_migration']:
        store.storage_apply(plan['revision'])
    text = files("workspace").joinpath("default_instructions.md").read_text()
    return runtime.workspace_create("create-project", text, EMPTY_PROJECT,
                                    "Create this test context with these settings",
                                    settings={**WORKSPACE_CREATION_DEFAULTS, **settings},
                                    project_description="Project purpose and boundaries for this test context")


def finish_with_cleanup_approval(runtime, cycle):
    preview = runtime.finish(cycle)
    if preview["status"] == "blocked" and "cleanup_revision" in preview:
        return runtime.finish(cycle, confirmation="Delete the reviewed temporary files", expected_revision=preview["cleanup_revision"])
    return preview


@pytest.fixture
def workspace(tmp_path):
    runtime = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    create(runtime)
    yield runtime
    runtime.close()


def test_unknown_workspace_never_created_by_opening(tmp_path):
    runtime = CycleRuntime(tmp_path, "missing", provider_factory=Factory())
    for call in (lambda: runtime.startup("start"), runtime.list, runtime.instructions_read,
                 lambda: runtime.search(query="anything"), lambda: runtime.read(material_id="one.md")):
        with pytest.raises(PCWError, match="does not exist"):
            call()
    assert not (tmp_path / ".pcw").exists()


def test_create_idempotent_no_models_and_read_without_cycle(tmp_path):
    factory = Factory()
    runtime = CycleRuntime(tmp_path, "project", provider_factory=factory)
    create(runtime)
    assert create(runtime)["replayed"]
    assert runtime.instructions_read()["content"]
    assert runtime.project_read()["content"] == EMPTY_PROJECT
    assert runtime.list()["total"] == 1
    assert runtime.search(query="facts", types=["knowledge"])["results"] == []
    assert len(factory.instances) == 1
    assert not (runtime.engine.root / "tmp").exists()
    assert not runtime.record_path.exists()


def test_read_reports_stale_index_without_repair(workspace):
    item = workspace.engine.create("old knowledge", description="Original knowledge used to check stale index reads")
    version = workspace.engine.table.version
    path = workspace.engine.root / item["path"]
    path.write_text(path.read_text().replace("old knowledge", "new knowledge"))
    assert workspace.read(material_id=item["id"])["content"] == "new knowledge"
    result = workspace.search(query="old", types=["knowledge"], d_max=2)
    assert result["results"] == []
    assert result["pending_count"] == 1
    assert workspace.engine.table.version == version


def test_legacy_migration_preserves_every_source(tmp_path):
    engine = PCWRuntime(tmp_path, "old", provider_factory=Factory())
    item = engine.create("# Exact facts\n\nSource conditions retained.")
    path = engine.root / item["path"]
    text = path.read_bytes()
    instructions = "# My own rules\nNo inferred permissions.\n"
    (engine.root / "INSTRUCTIONS.md").write_text(instructions, encoding="utf-8", newline="")
    (engine.root / "CURRENT.md").write_text("old task state")
    engine.close()
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    assert runtime.workspace_inspect()["format_version"] == 0
    with pytest.raises(PCWError, match="pcw-upgrade"):
        runtime.startup("start")
    plan = runtime.upgrade_plan()
    assert plan["project_description"]["status"] == "missing"
    result = runtime.upgrade_apply(plan["revision"], "Upgrade this workspace")
    assert result["status"] == "complete"
    assert result["project_description"]["status"] == "missing"
    replay = runtime.upgrade_apply(plan["revision"], "Retry")
    assert replay["replayed"]
    assert replay["project_description"]["status"] == "missing"
    migrated = runtime.read(material_id=item["id"])
    assert migrated["content"] == _parse(text.decode())[1]
    assert migrated["path"] == "knowledge/Exact-facts.md"
    assert runtime.instructions_read()["content"] == instructions
    assert not (engine.root / "CURRENT.md").exists()
    assert any("old task state" in m.content for m in runtime.engine.materials.values() if m.metadata.get("type") == "task")
    assert runtime.read(material_id=item["id"])["content"].startswith("# Exact facts")
    assert policies(instructions)["knowledge_approval_required"] is True
    assert policies(instructions)["allow_implicit_updates"] is False


def test_parallel_tasks_revision_conflicts_and_tmp_lifetime(workspace):
    a = workspace.startup("task-a")["cycle_id"]
    b = workspace.startup("task-b")["cycle_id"]
    p = workspace.proposal_prepare(a, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "task", "type": "task", "content": "# First task", "status": "active"}])
    workspace.proposal_apply(a, p["proposal_id"], p["revision"])
    first = workspace.read(material_id=p["created_ids"]["task"])
    change = {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": first["id"], "expected_revision": first["revision"], "content": "# First task\nChecked source"}
    updated = workspace.proposal_prepare(a, [change])
    workspace.proposal_apply(a, updated["proposal_id"], updated["revision"])
    with pytest.raises(PCWError, match="changed"):
        workspace.proposal_prepare(b, [change])
    workspace.tmp_write(b, "ongoing")
    assert finish_with_cleanup_approval(workspace, a)["status"] == "closed"
    assert "Checked source" in workspace.read(material_id=first["id"])["content"]
    assert workspace.tmp_list(b)["total"] == 1
    assert workspace.startup("resume-a", task_id=first["id"])["task_id"] == first["id"]


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("approval", [False, True])
def test_independent_permission_settings(tmp_path, implicit, approval):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    create(runtime, allow_implicit_updates=implicit, knowledge_approval_required=approval)
    if not implicit:
        with pytest.raises(PCWError, match="explicit"):
            runtime.startup("implicit", invocation="implicit")
    cycle = runtime.startup("work", invocation="implicit" if implicit else "explicit")["cycle_id"]
    plan = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "fact", "content": "Verified fact"}])
    assert plan["approval_required"] is approval
    if approval:
        with pytest.raises(PCWError, match="confirmation"):
            runtime.proposal_apply(cycle, plan["proposal_id"], plan["revision"])
    assert runtime.proposal_apply(cycle, plan["proposal_id"], plan["revision"], confirmation="Approved" if approval else None)["status"] == "applied"


def test_project_review_and_audit_cannot_write(workspace):
    cycle = workspace.startup("project-work")["cycle_id"]
    before = workspace.project_read()
    change = {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "material_id": before["id"], "expected_revision": before["revision"], "content": "# Project\nUser's accepted scope.\n"}
    plan = workspace.proposal_prepare(cycle, [change])
    assert workspace.project_read() == before
    workspace.proposal_apply(cycle, plan["proposal_id"], plan["revision"])
    assert "accepted scope" in workspace.project_read()["content"]
    audit = workspace.startup("audit", purpose="audit")["cycle_id"]
    assert workspace.list(audit)["total"] == 1
    with pytest.raises(PCWError):
        workspace.proposal_prepare(audit, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "x", "content": "x"}])
    assert workspace.finish(audit)["status"] == "closed"


@pytest.mark.parametrize("override,limit", [("standard", 1024), (None, None), (2000, 2000)])
def test_creation_material_limit_default_and_explicit_overrides(tmp_path, override, limit):
    runtime = CycleRuntime(tmp_path, "sized", provider_factory=Factory())
    try:
        create(runtime, **({} if override == "standard" else {"material_max_tokens": override}))
        instructions = runtime.instructions_read()["content"]
        assert runtime.settings_read(fields=["material_max_tokens"])["values"]["material_max_tokens"] == limit
        cycle = runtime.startup("work")["cycle_id"]
        # This fixture tokenizer counts characters, making the exact boundary deterministic.
        length = limit if limit is not None else 4096
        accepted = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "topic", "content": "x" * length}])
        assert accepted["status"] == "prepared"
        if limit is not None:
            rejected = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "large", "content": "x" * (limit + 1)}])
            assert rejected["status"] == "needs_split"
            assert rejected["materials"][0]["tokens"] == limit + 1
        assert runtime.list()["total"] == 1
        assert policies("Legacy instructions without settings")["material_max_tokens"] is None
    finally:
        runtime.close()


def test_optional_limit_rejects_before_write_and_labels_need_no_embedding(tmp_path):
    factory = Factory()
    runtime = CycleRuntime(tmp_path, "limited", provider_factory=factory)
    create(runtime, material_max_tokens=30)
    cycle = runtime.startup("work")["cycle_id"]
    oversized = runtime.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "large", "content": "x" * 31}])
    assert oversized["status"] == "needs_split"
    assert oversized["materials"][0]["tokens"] == 31
    assert runtime.list()["total"] == 1
    assert len(factory.calls) == 1  # Only initial project indexing.
    plan = runtime.proposal_prepare(cycle, [
        {"key": "a", "content": "# Storage\nFacts", "description": "Storage mechanism", "links": ["@b"]},
        {"key": "b", "content": "# Recovery\nSteps", "description": "Recovering storage"}])
    runtime.proposal_apply(cycle, plan["proposal_id"], plan["revision"])
    a, b = (runtime.read(material_id=plan["created_ids"][key]) for key in ("a", "b"))
    assert a["link_targets"][0]["description"] == "Recovering storage"
    assert runtime.read(material_id=b["id"], from_id=a["id"])["title"] == "Recovery"
    with pytest.raises(PCWError, match="existing link"):
        runtime.read(material_id=a["id"], from_id=b["id"])
    calls = len(factory.calls)
    change = runtime.proposal_prepare(cycle, [{"material_id": b["id"], "expected_revision": b["revision"], "description": "Recovery conditions"}])
    assert change["review_changes"][0]["description_change"]["after"] == "Recovery conditions"
    runtime.proposal_apply(cycle, change["proposal_id"], change["revision"])
    assert len(factory.calls) == calls
    assert runtime.read(material_id=a["id"])["link_targets"][0]["description"] == "Recovery conditions"


def test_cold_process_can_read_proposal_link_labels(workspace):
    cycle = workspace.startup("work")["cycle_id"]
    first = workspace.engine.create("# Existing topic\nContent", description="An existing target for cold-process link labels")
    plan = workspace.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "new", "content": "New facts", "links": [first["id"]]}])
    cold = CycleRuntime(workspace.engine.work_root, "project", provider_factory=Factory())
    assert cold.proposal_read(cycle, plan["proposal_id"])["review_changes"][0]["links_added"][0]["title"] == "Existing topic"
    cold.close()


def test_migration_interruption_keeps_backup_and_retries_same_operation(tmp_path):
    engine = PCWRuntime(tmp_path, "old", provider_factory=Factory())
    item = engine.create("Source unchanged")
    (engine.root / "INSTRUCTIONS.md").write_text("Custom rules")
    original = (engine.root / item["path"]).read_bytes()
    engine.close()
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    plan = runtime.upgrade_plan()
    write = runtime.engine._write
    def interrupt(path, content):
        write(path, content)
        if path.parent == runtime.engine.knowledge:
            raise OSError("lost project write acknowledgement")
    with patch.object(runtime.engine, "_write", side_effect=interrupt):
        with pytest.raises(OSError):
            runtime.upgrade_apply(plan["revision"], "Upgrade selected context")
    pending = json.loads(runtime._migration_receipt_path().read_text())
    assert pending["status"] == "applying"
    assert not runtime._format_path().exists()
    assert (Path(pending["backup"]) / item["path"]).read_bytes() == original
    result = runtime.upgrade_apply(plan["revision"], "Resume the same migration")
    assert result["backup"] == pending["backup"]
    assert result["status"] == "complete"
    assert runtime.read(material_id=item["id"])["content"] == _parse(original.decode())[1]


def legacy_with_work(tmp_path):
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    create(runtime, knowledge_approval_required=True)
    legacy_rules = "---\n" + yaml.safe_dump(runtime._policies(), sort_keys=False) + "---\n" + runtime.instructions_read()["content"]
    pending = runtime.startup("consolidate-runbooks")["cycle_id"]
    runtime.tmp_write(pending, "# Contract procedures\nVerified source findings.")
    proposal = runtime.proposal_prepare(pending, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "new", "content": "# Contract setup\nExact procedure."}])
    saved = runtime.startup("retention-rules")["cycle_id"]
    runtime.tmp_write(saved, "# Storage retention\nVerified retention rules.")
    applied = runtime.proposal_prepare(saved, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "saved", "content": "# Retention\nSaved rules."}])
    runtime.proposal_apply(saved, applied["proposal_id"], applied["revision"], confirmation="Approve this text")
    # Build a genuine preceding-format fixture: filename identity, old snapshots
    # and a per-context index. Do not disguise format-2 files by removing a marker.
    def old_snapshot(ident, snapshot):
        if snapshot is None:
            return None
        metadata, body = _parse(snapshot["text"])
        metadata.pop("id", None)
        text = "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n" + body
        return {"path": "knowledge/" + ident, "text": text, "fingerprint": _revision(text)}
    for material in list(runtime.engine.materials.values()):
        if material.metadata.get("type") == "project":
            (runtime.engine.root / material.path).unlink()
            continue
        path = runtime.engine.root / material.path
        snapshot = old_snapshot(material.id, {"text": path.read_text()})
        path.unlink()
        (runtime.engine.root / snapshot["path"]).write_text(snapshot["text"])
    records = runtime._records()
    for cycle_id, cycle in records["cycles"].items():
        for proposal_id, entry in cycle["proposals"].items():
            plan = runtime._load_plan(cycle_id, proposal_id, entry)
            plan.pop("format_version", None)
            plan.pop("settings_revision", None)
            plan["instructions_revision"] = _revision(legacy_rules)
            for ident, change in plan["entries"].items():
                for which in ("before", "after"):
                    change[which] = old_snapshot(ident, change[which])
            runtime.engine._write(runtime._plan_path(cycle_id, proposal_id), _json(plan))
            runtime.engine._write(runtime._plan_path(cycle_id, proposal_id).with_suffix(".md"), runtime._review(plan))
            entry["revision"] = _revision(_json(plan))
            for supplied in (proposal, applied):
                if supplied["proposal_id"] == proposal_id:
                    supplied["revision"] = entry["revision"]
    runtime._persist(records)
    runtime.engine._write(runtime.engine.root / "INSTRUCTIONS.md", legacy_rules)
    from contextopus.core.settings import Settings
    Settings(runtime.control.layout)._scope_path("workspace", "old").unlink()
    runtime.engine.legacy_state.mkdir(exist_ok=True)
    old_engine = PCWRuntime(tmp_path, "old", provider_factory=Factory())
    old_engine.reconcile()
    old_engine.close()
    runtime._format_path().unlink()
    return runtime, pending, proposal, saved, applied


def test_upgrade_preserves_pending_and_applied_work_and_both_backup_trees(tmp_path):
    runtime, pending, proposal, saved, applied = legacy_with_work(tmp_path)
    before = runtime._source_files()
    control = runtime._migration_tree(runtime.control.root, control=True)
    plan = runtime.upgrade_plan()
    assert plan["ready"] and not plan["blockers"]
    work = {row["cycle_id"]: row for row in plan["pending_work"]}
    assert work[pending]["request_ids"] == ["consolidate-runbooks"]
    assert work[pending]["temporary_materials"][0]["title"] == "Contract procedures"
    assert work[pending]["proposals"][0]["state"] == "prepared"
    assert work[pending]["proposals"][0]["changes"][0]["title"] == "Contract setup"
    assert work[saved]["proposals"][0]["state"] == "applied"
    result = runtime.upgrade_apply(plan["revision"], "Upgrade without cancelling work")
    assert result["pending_work_preserved"] and result["status"] == "complete"
    after = runtime._source_files()
    assert all(after[path] == digest for path, digest in before.items()
               if not path.startswith("knowledge/") and path != "INSTRUCTIONS.md")
    receipt = json.loads(runtime._migration_receipt_path().read_text())
    assert (Path(receipt["control_backup"]) / "cycles.json").read_bytes() == runtime.record_path.read_bytes()
    assert (Path(receipt["control_backup"]) / "material-ids.json").read_bytes() == (runtime.control.root / "material-ids.json").read_bytes()
    runtime._migration_verify_backup(receipt)
    assert receipt["control_sources"] == control
    assert runtime._records()["cycles"][pending]["state"] == "active"
    assert runtime._records()["cycles"][saved]["state"] == "active"
    assert not (runtime.engine.knowledge / proposal["created_ids"]["new"]).exists()
    # A cold process can continue the same review with the same revisions and approval requirement.
    runtime.close()
    cold = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    current_plan = cold.upgrade_plan()
    assert current_plan["compatible"] and current_plan["changes"] == []
    assert current_plan["pending_work"] == plan["pending_work"]
    assert current_plan["project_description"]["status"] == "missing"
    unchanged = cold._source_files(), cold._migration_tree(cold.control.root, control=True)
    current_result = cold.upgrade_apply(current_plan["revision"], "Check current context")
    assert current_result["status"] == "current"
    assert current_result["pending_work"] == plan["pending_work"]
    assert current_result["project_description"]["status"] == "missing"
    assert unchanged == (cold._source_files(), cold._migration_tree(cold.control.root, control=True))
    read = cold.proposal_read(pending, proposal["proposal_id"])
    assert read["revision"] == proposal["revision"] and read["approval_required"]
    assert cold.tmp_list(pending)["total"] == 1
    with pytest.raises(PCWError, match="confirmation"):
        cold.proposal_apply(pending, proposal["proposal_id"], proposal["revision"])
    assert cold.proposal_apply(pending, proposal["proposal_id"], proposal["revision"], confirmation="Approve now")["status"] == "applied"
    assert finish_with_cleanup_approval(cold, pending)["status"] == "closed"
    assert cold.proposal_apply(saved, applied["proposal_id"], applied["revision"])["replayed"]
    assert finish_with_cleanup_approval(cold, saved)["status"] == "closed"
    cold.close()


@pytest.mark.parametrize("content,status", [
    (None, "missing"), ("\n \n", "empty"), (EMPTY_PROJECT, "placeholder"),
    ("# My project\nVerified purpose and boundaries.\n", "present"),
])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_current_format_reports_project_content_without_rewriting(workspace, content, status, line_ending):
    project = workspace.project_read()
    path = workspace.engine.root / project["path"]
    if content is None:
        path.unlink()
    else:
        from workspace.records import record_text
        metadata, _ = _parse(path.read_text())
        stored = record_text(metadata, content).replace("\n", line_ending).encode("utf-8")
        path.write_bytes(stored)
    before = workspace._source_files()
    plan = workspace.upgrade_plan()
    assert plan["project_description"]["status"] == status
    assert plan["project_description"]["content_reviewed"] is False
    assert before == workspace._source_files()
    if content is not None:
        assert path.read_bytes() == stored


def test_current_format_reports_damaged_unfinished_work(workspace):
    cycle = workspace.startup("pending-review")["cycle_id"]
    proposal = workspace.proposal_prepare(cycle, [{"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "new", "content": "# Fact\nChecked."}])
    workspace._plan_path(cycle, proposal["proposal_id"]).unlink()
    plan = workspace.upgrade_plan()
    assert plan["compatible"] and plan["changes"] == []
    assert not plan["ready"] and plan["next_action"] == "inspect_blockers"
    assert plan["pending_work"][0]["cycle_id"] == cycle
    assert any(row["proposal_id"] == proposal["proposal_id"] for row in plan["blockers"])


@pytest.mark.parametrize("changed", ["tmp", "registry"])
def test_upgrade_review_detects_changes_to_work(tmp_path, changed):
    runtime, pending, proposal, _, _ = legacy_with_work(tmp_path)
    plan = runtime.upgrade_plan()
    if changed == "tmp":
        runtime.tmp_write(pending, "Another finding")
    else:
        records = runtime._records()
        records["cycles"][pending]["proposals"][proposal["proposal_id"]]["state"] = "discarded"
        runtime._persist(records)
    with pytest.raises(PCWError, match="changed since migration review"):
        runtime.upgrade_apply(plan["revision"], "Upgrade")
    assert not (runtime.control.root / "migration.json").exists()
    assert not runtime._format_path().exists()


@pytest.mark.parametrize("problem", ["missing_proposal", "unknown_kind", "closing"])
def test_upgrade_reports_incompatible_work_before_apply(tmp_path, problem):
    runtime, pending, proposal, _, _ = legacy_with_work(tmp_path)
    if problem == "missing_proposal":
        runtime._plan_path(pending, proposal["proposal_id"]).unlink()
    elif problem == "unknown_kind":
        records = runtime._records()
        records["cycles"][pending]["purpose"] = "future-purpose"
        runtime._persist(records)
    else:
        records = runtime._records()
        records["cycles"][pending]["state"] = "closing"
        runtime._persist(records)
    before = runtime.record_path.read_bytes()
    plan = runtime.upgrade_plan()
    assert not plan["ready"] and plan["next_action"] == "inspect_blockers"
    assert any(row["cycle_id"] == pending for row in plan["blockers"])
    with pytest.raises(PCWError, match="preflight blocked"):
        runtime.upgrade_apply(plan["revision"], "Upgrade")
    assert runtime.record_path.read_bytes() == before
    assert not runtime._format_path().exists()


@pytest.mark.parametrize("backup_directory", [".backups", "backups"])
def test_snapshot_interruption_resumes_same_backup_without_overwrite(tmp_path, backup_directory):
    runtime, _, _, _, _ = legacy_with_work(tmp_path)
    plan = runtime.upgrade_plan()
    copy = runtime._migration_copy
    def interrupt(source, target, manifest):
        copy(source, target, manifest)
        if target.name == "workspace":
            raise OSError("lost snapshot acknowledgement")
    with patch.object(runtime, "_migration_copy", side_effect=interrupt):
        with pytest.raises(OSError):
            runtime.upgrade_apply(plan["revision"], "Upgrade")
    operation = json.loads(runtime._migration_receipt_path().read_text())
    assert Path(operation["backup"]).parent.parent.name == ".backups"
    if backup_directory == "backups":
        # Reconstruct a pre-change interrupted receipt without changing its
        # revision, source manifests or retained snapshot bytes.
        source = Path(operation["backup"]).parent
        destination = runtime.control.root / "backups" / source.name
        destination.parent.mkdir()
        source.rename(destination)
        operation["backup"] = str(destination / "workspace")
        operation["control_backup"] = str(destination / "control")
        runtime.control.write(runtime._migration_receipt_path(), _json(operation))
    first = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(operation["backup"]).rglob("*") if p.is_file()}
    assert runtime.upgrade_plan()["resume_revision"] == plan["revision"]
    result = runtime.upgrade_apply(plan["revision"], "Retry same operation")
    assert result["backup"] == operation["backup"]
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == values for p, values in first.items())
    assert len(list((runtime.control.root / backup_directory).iterdir())) == 1
    runtime._migration_verify_backup(json.loads(runtime._migration_receipt_path().read_text()))


def test_changed_work_after_interruption_blocks_resume(tmp_path):
    runtime, pending, _, _, _ = legacy_with_work(tmp_path)
    plan = runtime.upgrade_plan()
    write = runtime.engine._write
    def interrupt(path, content):
        write(path, content)
        if path.parent == runtime.engine.knowledge:
            raise OSError("interrupted")
    with patch.object(runtime.engine, "_write", side_effect=interrupt):
        with pytest.raises(OSError):
            runtime.upgrade_apply(plan["revision"], "Upgrade")
    runtime.tmp_write(pending, "Work changed while migration was interrupted")
    changed_plan = runtime.upgrade_plan()
    assert not changed_plan["ready"]
    assert changed_plan["resume_revision"] == plan["revision"]
    assert any("Source changed" in issue["reason"] for issue in changed_plan["blockers"])
    with pytest.raises(PCWError, match="Source changed"):
        runtime.upgrade_apply(plan["revision"], "Retry")
    assert not runtime._format_path().exists()


def test_backup_corruption_does_not_overwrite_or_mark_format_current(tmp_path):
    runtime, _, _, _, _ = legacy_with_work(tmp_path)
    plan = runtime.upgrade_plan()
    original = runtime._migration_verify_backup
    def corrupt(operation):
        (Path(operation["control_backup"]) / "cycles.json").write_text("corrupt")
        original(operation)
    with patch.object(runtime, "_migration_verify_backup", side_effect=corrupt):
        with pytest.raises(PCWError, match="backup verification failed"):
            runtime.upgrade_apply(plan["revision"], "Upgrade")
    with pytest.raises(PCWError, match="Retained backup changed"):
        runtime.upgrade_apply(plan["revision"], "Retry")
    assert not runtime._format_path().exists()


def test_audit_reports_evidence_and_preserves_all_files(workspace):
    a = workspace.engine.create("# Same\nBody", description="First duplicate body for structural audit")
    b = workspace.engine.create("# Same\nBody", description="Second duplicate body for structural audit")
    path = workspace.engine.root / a["path"]
    path.write_text(path.read_text(encoding="utf-8").replace("links: []", "links: [missing.md]"), encoding="utf-8", newline="")
    before = {p: p.read_bytes() for p in workspace.engine.knowledge.rglob("*.md")}
    result = workspace.audit()
    problems = [issue for row in result["materials"] for issue in row["issues"]]
    assert {p["problem"] for p in problems} == {"duplicate_body", "missing_link_target"}
    assert all(p["fragment"] and p["basis"] for p in problems)
    assert before == {p: p.read_bytes() for p in workspace.engine.knowledge.rglob("*.md")}
    c = workspace.startup("audit-only", purpose="audit")["cycle_id"]
    rules = workspace.instructions_read()
    with pytest.raises(PCWError, match="Audit"):
        workspace.instructions_prepare(c, "New rules", rules["revision"], "Change rules")


def test_rework_split_redirects_links_and_keeps_whole_procedure(workspace):
    first = workspace.startup("initial")["cycle_id"]
    seed = workspace.proposal_prepare(first, [
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "mixed", "content": "# Storage\nConnect with A.\nRecovery requires B."},
        {"description": "Verified knowledge used to exercise workspace lifecycle and proposal rules", "key": "entry", "content": "# Entry", "links": ["@mixed"]}])
    workspace.proposal_apply(first, seed["proposal_id"], seed["revision"])
    assert finish_with_cleanup_approval(workspace, first)["status"] == "closed"
    c = workspace.startup("rework", purpose="rework")["cycle_id"]
    old = workspace.read(c, seed["created_ids"]["mixed"])
    workspace.tmp_write(c, "Both facts and their conditions were retained in separate topics")
    plan = workspace.proposal_prepare(c, [
        {"material_id": old["id"], "expected_revision": old["revision"], "delete": True},
        {"key": "connection", "content": "# Connection\nConnect with A.", "description": "Connection procedure", "links": ["@recovery"]},
        {"key": "recovery", "content": "# Recovery\nRecovery requires B.", "description": "Recovery conditions"}],
        replacements={old["id"]: "@connection"})
    assert workspace.finish(c)["status"] == "blocked"
    workspace.proposal_apply(c, plan["proposal_id"], plan["revision"])
    assert workspace.read(material_id=seed["created_ids"]["entry"])["links"] == [plan["created_ids"]["connection"]]
    assert finish_with_cleanup_approval(workspace, c)["tmp_cleared"]


def test_control_snapshot_excludes_hidden_and_legacy_backup_trees(workspace):
    for name in ("backups", ".backups"):
        path = workspace.control.root / name / "previous" / "control"
        path.mkdir(parents=True)
        (path / "retained.txt").write_text("Do not recursively copy or modify this snapshot")
    manifest = workspace._migration_tree(workspace.control.root, control=True)
    assert not any(key.split("/", 1)[0] in {"backups", ".backups"} for key in manifest)
    for name in ("backups", ".backups"):
        assert (workspace.control.root / name / "previous/control/retained.txt").read_text() == "Do not recursively copy or modify this snapshot"
