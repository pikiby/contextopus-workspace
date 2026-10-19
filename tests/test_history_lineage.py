"""History navigation must survive semantic rewriting, deletion and retries."""

import json
from importlib.resources import files
from unittest.mock import patch

import pytest

from pcw_runtime import PCWError
from pcw_runtime.cycles import CycleRuntime
from pcw_runtime.policies import policies
from test_runtime import Factory
from test_workspace_v3 import create


@pytest.fixture
def history(tmp_path):
    runtime = CycleRuntime(tmp_path, "history", provider_factory=Factory())
    create(runtime)
    cycle = runtime.startup("history-test")["cycle_id"]
    yield runtime, cycle
    runtime.close()


def save(runtime, cycle, changes, **kwargs):
    plan = runtime.proposal_prepare(cycle, changes, **kwargs)
    result = runtime.proposal_apply(cycle, plan["proposal_id"], plan["revision"])
    assert result["status"] == "applied"
    return plan, result["history"]["commit"]


def test_new_default_enables_git_but_legacy_and_opt_out_stay_unchanged(tmp_path):
    template = files("workspace").joinpath("default_instructions.md").read_text()
    from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
    assert WORKSPACE_CREATION_DEFAULTS["git_history_enabled"] is True
    assert policies("Custom legacy instructions")["git_history_enabled"] is False
    runtime = CycleRuntime(tmp_path, "off", provider_factory=Factory())
    try:
        assert create(runtime, git_history_enabled=False)["history"]["status"] == "disabled"
        assert runtime.history_list()["status"] == "no_history"
    finally:
        runtime.close()


def test_rewritten_merge_split_and_renamed_record_history(history):
    r, c = history
    original, before = save(r, c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "a", "content": "# Authentication\nOld credential condition."},
                                   {"description": "Verified behavior used to test historical replay and policy evidence", "key": "b", "content": "# Connection\nPrevious endpoint rule."}])
    a, b = [r.read(material_id=original["created_ids"][key]) for key in ("a", "b")]
    merged, commit = save(r, c, [
        {"material_id": a["id"], "expected_revision": a["revision"], "delete": True},
        {"material_id": b["id"], "expected_revision": b["revision"], "delete": True},
        {"description": "Verified behavior used to test historical replay and policy evidence", "key": "combined", "content": "# Access\nEntirely rewritten project access procedure."},
    ], replacements={a["id"]: "@combined", b["id"]: "@combined"},
        transformations=[{"kind": "merge", "sources": [a["id"], b["id"]], "targets": ["@combined"]}],
        summary="Consolidate access requirements without duplicated rules")
    ident = merged["created_ids"]["combined"]
    relation = {"kind": "merge", "sources": [a["id"], b["id"]], "targets": [ident]}
    assert merged["transformations"] == [relation]
    assert r.proposal_read(c, merged["proposal_id"])["transformations"] == [relation]
    details = r.history_changes(commit)
    assert details["parents"] == [before]
    assert details["transformations"] == [relation]
    assert {x["action"] for x in details["files"]} == {"A", "D"}
    assert len(details["changes"]) == 3
    assert r.history_list(material_id=a["id"])["versions"][0]["commit"] == commit
    assert "Old credential condition" in r.history_read(commit=before, material_id=a["id"])["materials"][0]["text"]
    assert r.history_search("Old credential condition")["versions"][0]["commit"] == commit
    combined = r.read(material_id=ident)
    split, split_commit = save(r, c, [
        {"description": "Verified behavior used to test historical replay and policy evidence", "material_id": ident, "expected_revision": combined["revision"], "content": "# Access\nNew authentication rule."},
        {"description": "Verified behavior used to test historical replay and policy evidence", "key": "connection", "content": "# Connection\nNew transport rule."}],
        transformations=[{"kind": "split", "sources": [ident], "targets": [ident, "@connection"]}],
        summary="Separate authentication and transport")
    assert r.history_changes(split_commit)["transformations"][0]["targets"] == [ident, split["created_ids"]["connection"]]
    combined = r.read(material_id=ident)
    _, renamed = save(r, c, [{"material_id": ident, "expected_revision": combined["revision"], "path": "Renamed.md"}])
    assert r.history_read(commit=renamed, material_id=ident)["materials"][0]["path"] == "knowledge/Renamed.md"
    assert r.history_read(commit=commit, material_id=ident)["materials"][0]["path"] != "knowledge/Renamed.md"
    pages = r.history_list(material_id=ident, limit=1)
    assert pages["next_offset"] == 1
    assert r.history_list(material_id=ident, limit=1, offset=1, ref=pages["head"])["versions"][0]["commit"] == split_commit
    fragment = r.history_diff(before, commit, max_chars=20)
    assert fragment["truncated"] and len(fragment["diff"]) == 20
    rest = r.history_diff(before, commit, offset=fragment["next_offset"], max_chars=20)
    assert rest["offset"] == 20
    body = r.history_read(commit=before, material_id=a["id"], max_chars=10)["materials"][0]
    assert body["truncated"] and body["next_offset"] == 10
    assert len(r.history_list(query="Consolidate access requirements")["versions"]) == 1
    assert not r.history_list(since="2100-01-01")["versions"]


def test_provenance_is_revision_bound_and_immutable_on_retry(history):
    r, c = history
    plan = r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "new", "content": "# Fact\nFirst state."}], summary="Record original evidence")
    revision = plan["revision"]
    updated = r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "new", "content": "# Fact\nFirst state."}],
                                 proposal_id=plan["proposal_id"], summary="Record corrected reason")
    assert updated["revision"] != revision
    with pytest.raises(PCWError, match="exact presented"):
        r.proposal_apply(c, plan["proposal_id"], revision)
    git = r._history_git
    def fail(*args, **kwargs):
        if "commit-tree" in args:
            raise OSError("Git temporarily unavailable")
        return git(*args, **kwargs)
    with patch.object(r, "_history_git", side_effect=fail):
        result = r.proposal_apply(c, updated["proposal_id"], updated["revision"])
    assert result["history"]["status"] == "pending"
    r.history_retry()
    newest = r.history_list()["versions"][0]
    assert "Record corrected reason" in newest["subject"]
    assert r.history_changes(newest["commit"])["operation_id"] == updated["proposal_id"]
    count = len(r.history_list()["versions"])
    r.history_retry()
    assert len(r.history_list()["versions"]) == count


@pytest.mark.parametrize("declaration", [
    {"kind": "merge", "sources": ["absent.md"], "targets": ["@new"]},
    {"kind": "split", "sources": ["@new"], "targets": ["@new", "@new"]},
    {"kind": "invented", "sources": [], "targets": []},
])
def test_invalid_provenance_does_not_change_permanent_files(history, declaration):
    r, c = history
    before = r._permanent_files()
    with pytest.raises(PCWError):
        r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "new", "content": "# Fact"}], transformations=[declaration])
    assert before == r._permanent_files()


def test_legacy_commits_have_no_fabricated_lineage(history):
    r, c = history
    plan, _ = save(r, c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "new", "content": "# Fact\nHistorical evidence."}])
    r.engine._git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Old external commit")
    old = r.history_changes()
    assert old["provenance"] == "unrecorded"
    assert old["transformations"] == []
    item = r.read(material_id=plan["created_ids"]["new"])
    assert r.history_list(paths=[item["path"]])["versions"]


def test_pending_provenance_tampering_is_rejected(history):
    r, c = history
    with patch.object(r, "_history_flush", return_value={"status": "pending"}):
        plan, _ = save_without_commit(r, c)
    path = r._history_root() / (plan["proposal_id"] + ".json")
    snapshot = json.loads(path.read_text())
    snapshot["event"]["summary"] = "Not the reviewed summary"
    path.write_text(json.dumps(snapshot))
    with pytest.raises(PCWError, match="provenance was changed"):
        r.history_retry()


def save_without_commit(r, c):
    plan = r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "x", "content": "# X\nEvidence"}], summary="Approved meaning")
    return plan, r.proposal_apply(c, plan["proposal_id"], plan["revision"])


def test_history_queries_are_available_through_mcp_and_paginate(history):
    from pcw_runtime import server
    r, c = history
    plan, commit = save(r, c, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "a", "content": "# A\nFirst fact."},
                              {"description": "Verified behavior used to test historical replay and policy evidence", "key": "b", "content": "# B\nSecond fact."}])
    with patch.object(server, "runtime", return_value=r):
        def query(operation, **arguments):
            return server.pcw_query(operation, str(r.engine.work_root), "history", arguments)
        found = query("history_search", text="First fact.", limit=1)
        assert found["versions"][0]["commit"] == commit
        changed = query("history_changes", commit=commit, limit=1)
        assert len(changed["files"]) == len(changed["changes"]) == 1
        assert changed["next_offset"] == 1
        assert query("history_changes", commit=commit, offset=1, limit=1)["next_offset"] is None
        initial = r.history_list()["versions"][-1]["commit"]
        assert r.history_list(material_id=r.project_read()["id"])["versions"][-1]["commit"] == initial
        root = query("history_changes", commit=initial)
        assert root["parents"] == [] and root["files_count"] == 3
        assert query("history_read", commit=commit, material_id=plan["created_ids"]["a"])["materials"]
        for operation in ("history_search", "history_changes"):
            assert server.pcw_query("describe", arguments={"operation": operation})["tool"] == "pcw_query"


def test_disabling_history_records_the_settings_change(history):
    r, c = history
    plan = r.settings_preview({"git_history_enabled": False}, "Disable local history")
    applied = r.settings_apply(plan["preview_revision"], "Approve shown settings")
    assert applied["history"]["status"] == "committed"
    latest = r.history_list(material_id="workspace-settings")["versions"][0]
    assert r.history_changes(latest["commit"])["changes"][0]["id"] == "workspace-settings"
