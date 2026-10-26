from workspace_support import reconstruct_legacy_rules
"""Exercise the shipped Workspace, not the retained PCW compatibility package."""
from importlib.resources import files
import json
import yaml
from unittest.mock import patch

import pytest

from workspace.cycles import CycleRuntime
from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
from workspace.runtime import _parse, PCWError
from contextopus.core.resources import ProcessResources
from contextopus.startup import Application
from workspace_support import install_workspace
from test_runtime import Factory


@pytest.mark.parametrize("interruption", ["snapshotting", "applying"])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_recovers_interrupted_preceding_format_before_v3(tmp_path, interruption, line_ending):
    from workspace.workspaces import encode
    runtime = workspace(tmp_path)
    root = runtime.engine.root
    project = runtime.project_read()
    reconstruct_legacy_rules(runtime)
    (root / project["path"]).unlink()
    (root / "PROJECT.md").write_bytes(project["content"].encode("utf-8"))
    source = "# Important fact\nPreserve this identity\n".replace("\n", line_ending)
    converted = "---\nid: old-id.md\nlinks: []\n---\n# Important fact\nPreserve this identity\n".replace("\n", line_ending)
    (root / "knowledge/old-id.md").write_bytes(source.encode("utf-8"))
    runtime.control.write(runtime._format_path(), encode({"format_version": 1}))
    backup = runtime.control.root / "backups/preceding"
    operation = {
        "revision": "preceding-v2", "status": "snapshotting",
        "sources": runtime._v2_sources(), "control_sources": runtime._v2_control(),
        "workspace_snapshot": runtime._migration_tree(root),
        "backup": str(backup / "workspace"), "control_backup": str(backup / "control"),
        "transforms": {"knowledge/old-id.md": {
            "id": "old-id.md", "before": source,
            "path": "knowledge/Important fact.md",
            "text": converted,
        }}, "overlays": {}, "instruction_initialization": {},
    }
    receipt = runtime.control.root / "migration-v2.json"
    runtime.control.write(receipt, encode(operation))
    if interruption == "applying":
        runtime._migration_copy(root, backup / "workspace", operation["workspace_snapshot"])
        runtime._migration_copy(runtime.control.root, backup / "control", operation["control_sources"])
        operation["status"] = "applying"
        runtime.control.write(receipt, encode(operation))
        # Crash between writing the descriptive file and removing its old name.
        (root / "knowledge/Important fact.md").write_bytes(converted.encode("utf-8"))
    plan = runtime.upgrade_plan()
    assert plan["ready"] and plan["recover_format_two_first"] == "preceding-v2", plan
    assert runtime.upgrade_apply(plan["revision"], "Upgrade")["status"] == "complete"
    found = runtime.read(material_id="old-id.md")
    assert found["type"] == "knowledge" and "Preserve this identity" in found["content"]
    assert not (root / "knowledge/old-id.md").exists()
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "complete"
    assert runtime.upgrade_plan()["compatible"]
    runtime.close()


def workspace(root, name="alpha", factory=None):
    from workspace_support import prepare_workspace_storage
    prepare_workspace_storage(root)
    runtime = CycleRuntime(root, name, provider_factory=factory or Factory())
    rules = files("workspace").joinpath("default_instructions.md").read_text(encoding="utf-8").replace("material_max_tokens: 1024", "material_max_tokens: null")
    project = f"---\ndescription: Defines shared observability goals and the project boundaries.\n---\n# {name}\nProject for shared observability"
    created = runtime.workspace_create("create", rules, project, "Create this context",
                                       settings={**WORKSPACE_CREATION_DEFAULTS, "material_max_tokens": None})
    assert created["status"] == "created", created
    return runtime


def put(runtime, body, kind="knowledge", status=None, links=None):
    cycle = runtime.startup("write-" + str(len(runtime._records()["cycles"])))['cycle_id']
    change = {"key": "new", "content": body, "description": "Records the verified state and the conditions for its use.", "type": kind, "links": links or []}
    if status is not None:
        change["status"] = status
    proposal = runtime.proposal_prepare(cycle, [change], expected_task_revision=runtime.status(cycle)["task_revision"])
    result = runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
    assert result["status"] == "applied", result
    return runtime.read(material_id=proposal["created_ids"]["new"])


def test_types_statuses_links_and_project_are_common_records(tmp_path):
    runtime = workspace(tmp_path)
    project = runtime.project_read()
    assert project["type"] == "project"
    assert not (runtime.engine.root / "PROJECT.md").exists()
    assert not (runtime.engine.root / "current").exists()
    task = put(runtime, "# Task\nInvestigate shared observability", "task", "blocked", [project["id"]])
    put(runtime, "# Decision\nObservability is shared", "decision", "accepted", [task["id"]])
    found = runtime.search(query="observability", types=["task"], statuses=["blocked"], N=1, d_max=2)
    assert [r["id"] for r in found["results"]] == [task["id"]]
    assert runtime.read(material_id=project["id"], from_id=task["id"])["type"] == "project"
    assert {i["type"] for i in runtime.record_types()["types"]} == {"task", "project", "decision"}
    runtime.close()


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_migrate_current_preserves_ids_pending_proposal_and_retry(tmp_path, line_ending):
    runtime = workspace(tmp_path)
    item = put(runtime, "# State\nVerified before migration")
    cycle = runtime.startup("pending")["cycle_id"]
    proposal = runtime.proposal_prepare(cycle, [{"material_id": item["id"], "expected_revision": item["revision"], "content": "# State\nApproved later", "description": "Records the state approved after migration."}], expected_task_revision=runtime.status(cycle)["task_revision"])
    project = runtime.project_read()
    # Reconstruct the machine flags and proposal contract a real pre-v4 context used.
    rules = runtime.engine.root / "INSTRUCTIONS.md"
    rules_text = "---\n" + yaml.safe_dump(runtime._policies()) + "---\n" + runtime.instructions_read()["content"]
    rules_text = rules_text.replace("\n", line_ending)
    reconstruct_legacy_rules(runtime, rules_text)
    with runtime._cycle(cycle) as (records, state):
        old = runtime._load_plan(cycle, proposal["proposal_id"], state["proposals"][proposal["proposal_id"]])
        old.pop("settings_revision", None)
        old.pop("required_instructions_revision", None)
        # A real pre-v4 proposal predates edit ownership.
        runtime._edits().release(cycle)
        old.pop("edit_reservations", None)
        from workspace.runtime import _revision
        # Revisions cover the actual old writer's bytes, including CRLF.
        old["instructions_revision"] = _revision(rules.read_bytes().decode("utf-8"))
        assert old["instructions_revision"] == _revision(rules_text) == runtime._instructions()["revision"]
        proposal = runtime._save_plan(records, state, old, "prepared")
    proposal_path = runtime._tmp(cycle) / "proposals" / (proposal["proposal_id"] + ".json")
    original_proposal = proposal_path.read_bytes()
    root = runtime.engine.root
    (root / "PROJECT.md").write_text(project["content"], encoding="utf-8", newline="")
    (root / project["path"]).unlink()
    (root / "current").mkdir()
    (root / "current/task-id.md").write_text("---\ngoal: Find archive\nconstraints: Preserve data\nverified_state: Found schema\nnext_step: Verify files\nblockers: Authentication\nstatus: blocked\nresults: 12 rows\n---\nExtra evidence\n", encoding="utf-8", newline="")
    (root / "current/Descriptive task.md").write_text("---\nid: stable-task-id\ngoal: Keep named task\nstatus: completed\n---\n", encoding="utf-8", newline="")
    runtime.control.write(runtime._format_path(), json.dumps({"format_version": 2}))
    plan = runtime.upgrade_plan()
    assert plan["ready"], plan
    result = runtime.upgrade_apply(plan["revision"], "Upgrade")
    assert result["status"] == "complete", result
    assert runtime.upgrade_apply(plan["revision"], "Retry")["replayed"]
    assert proposal_path.read_bytes() == original_proposal
    assert not (root / "current").exists() and not (root / "PROJECT.md").exists()
    task = runtime.read(material_id="task-id")
    assert task["type"] == "task" and task["status"] == "blocked"
    assert runtime.read(material_id="stable-task-id")["status"] == "completed"
    for text in ("Find archive", "Preserve data", "Found schema", "Verify files", "Authentication", "12 rows", "Extra evidence"):
        assert text in task["content"]
    assert runtime.search(query="archive", types=["task"], statuses=["blocked"], d_max=2)["results"][0]["id"] == "task-id"
    applied = runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
    assert applied["status"] == "applied", applied
    runtime.close()


def test_discovery_before_selection_and_external_project_edit(tmp_path):
    (tmp_path / 'modules/workspace/workspace_contexts').mkdir(parents=True)
    factory = Factory()
    runtime = workspace(tmp_path, factory=factory)
    project = runtime.project_read()
    with ProcessResources(factory) as resources, Application(tmp_path, resources=resources, discover=False) as app:
        install_workspace(tmp_path, app=app)
        found = app.query("workspace.contexts")
        assert found["contexts"] == [{"name": "alpha", "path": str(runtime.engine.root)}]
        assert app.query("workspace.contexts", {"name": "missing"})["total"] == 0
        result = app.query("workspace.projects_search", {"query": "observability", "distance": 2.0})
        assert result["results"][0]["material_id"] == project["id"]
        path = runtime.engine.root / project["path"]
        path.write_text(path.read_text(encoding="utf-8").replace("observability", "robotics"), encoding="utf-8", newline="")
        assert not app.query("workspace.projects_search", {"query": "observability", "distance": 2.0})["results"]
        runtime.engine.reconcile()
        assert "robotics" in app.query("workspace.projects_search", {"query": "robotics", "distance": 2.0})["results"][0]["excerpt"]
        assert app.context.workspace is None
        names = app.query("describe")["operations"]
        assert not any("current_" in n or "project_update" in n for group in names.values() for n in group)
    runtime.close()


def test_metadata_changes_reuse_vectors_and_conflict_with_old_revision(tmp_path):
    factory = Factory()
    runtime = workspace(tmp_path, factory=factory)
    task = put(runtime, "# Task\nExact result", "task", "active")
    calls = len(factory.calls)
    cycle = runtime.startup("change-status")["cycle_id"]
    change = {"material_id": task["id"], "expected_revision": task["revision"], "status": "completed"}
    proposal = runtime.proposal_prepare(cycle, [change], expected_task_revision=runtime.status(cycle)["task_revision"])
    assert proposal["review_changes"][0]["metadata_changes"]["status"] == {"before": "active", "after": "completed"}
    assert runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])["status"] == "applied"
    assert len(factory.calls) == calls
    current = runtime.read(material_id=task["id"])
    assert current["revision"] != task["revision"]
    with pytest.raises(PCWError, match="changed"):
        runtime.proposal_prepare(cycle, [change], expected_task_revision=runtime.status(cycle)["task_revision"])
    rows = runtime.engine.index.read_rows(runtime.engine.table)
    assert all(json.loads(r["metadata"])["status"] == "completed" for r in rows if r["material_id"] == task["id"])
    runtime.close()


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("proposal_state", ["prepared", "applying"])
def test_interrupted_migration_resumes_and_preserves_legacy_project_approval(tmp_path, line_ending, proposal_state):
    from workspace.runtime import _revision
    runtime = workspace(tmp_path)
    cycle = runtime.startup("change-project")["cycle_id"]
    before = runtime.project_read()
    reconstruct_legacy_rules(runtime)
    root = runtime.engine.root
    legacy_body = before["content"].replace("\n", line_ending)
    (root / "PROJECT.md").write_bytes(legacy_body.encode("utf-8"))
    (root / before["path"]).unlink()
    (root / "current").mkdir()
    runtime.control.write(runtime._format_path(), json.dumps({"format_version": 2}))
    text = "# Project\nChanged purpose"
    with runtime._cycle(cycle) as (records, state):
        proposal = runtime._save_plan(records, state, {
            "cycle_id": cycle, "proposal_id": "old-project-proposal", "kind": "project",
            "created_ids": {}, "instructions_revision": runtime._instructions()["revision"],
            "knowledge_approval_required": True,
            "entries": {"PROJECT.md": {
                "before": {"path": "PROJECT.md", "text": legacy_body, "fingerprint": _revision(legacy_body)},
                "after": {"path": "PROJECT.md", "text": text, "fingerprint": _revision(text)},
            }},
        }, proposal_state)
    proposal_path = runtime._tmp(cycle) / "proposals" / (proposal["proposal_id"] + ".json")
    original_proposal = proposal_path.read_bytes()
    plan = runtime.upgrade_plan()
    assert plan["ready"], plan
    with patch.object(runtime.engine._adapter, "reconcile", side_effect=OSError("injected interruption")):
        with pytest.raises(OSError, match="injected"):
            runtime.upgrade_apply(plan["revision"], "Upgrade")
    assert runtime.upgrade_plan()["resume_revision"] == plan["revision"]
    assert runtime.upgrade_apply(plan["revision"], "Retry")["status"] == "complete"
    assert runtime.project_read()["content"] == legacy_body
    assert proposal_path.read_bytes() == original_proposal
    if proposal_state == "prepared":
        result = runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
        assert result["status"] == "reprepare_required", result
        assert result["knowledge_changed"] is False
        result = runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"], "I approve the displayed project change")
        assert result["status"] == "reprepare_required", result
        assert runtime.project_read()["content"] == legacy_body
        assert proposal_path.read_bytes() == original_proposal
        runtime.close()
        return
    with pytest.raises(PCWError, match="confirmation"):
        runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
    result = runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"], "I approve the displayed project change")
    assert result["status"] == "applied", result
    assert runtime.project_read()["content"] == "# Project\nChanged purpose"
    runtime.close()


def test_migration_review_rejects_newline_only_source_change(tmp_path):
    runtime = workspace(tmp_path)
    project = runtime.project_read()
    reconstruct_legacy_rules(runtime)
    root = runtime.engine.root
    legacy = root / "PROJECT.md"
    legacy.write_bytes(project["content"].encode("utf-8"))
    (root / project["path"]).unlink()
    runtime.control.write(runtime._format_path(), json.dumps({"format_version": 2}))
    plan = runtime.upgrade_plan()
    assert plan["ready"], plan
    legacy.write_bytes(project["content"].replace("\n", "\r\n").encode("utf-8"))
    with pytest.raises(PCWError, match="changed since migration review"):
        runtime.upgrade_apply(plan["revision"], "Upgrade")
    runtime.close()
