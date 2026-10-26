from unittest.mock import patch

from test_runtime import Factory
from test_workspace_v3 import create
from pcw_runtime.cycles import CycleRuntime


def apply(runtime, cycle, changes):
    plan = runtime.proposal_prepare(cycle, changes, expected_task_revision=runtime.status(cycle)["task_revision"])
    return plan, runtime.proposal_apply(cycle, plan["proposal_id"], plan["revision"])


def test_automatic_history_tracks_permanent_files_only(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    initial = create(runtime, git_history_enabled=True)
    assert initial["history"]["status"] == "committed"
    cycle = runtime.startup("work")["cycle_id"]
    temporary = runtime.tmp_write(cycle, "Some result")
    ignored = runtime.engine._git("ls-files", "--others", "--ignored", "--exclude-standard", "--", temporary["path"])
    assert ignored.stdout.strip() == temporary["path"]
    plan, result = apply(runtime, cycle, [
        {"description": "Verified behavior used to test historical replay and policy evidence", "key": "a", "content": "# Fact\nFirst"},
        {"description": "Verified behavior used to test historical replay and policy evidence", "key": "task", "type": "task", "status": "active", "content": "# Some task\nNext action"},
    ])
    assert result["history"]["status"] == "committed"
    item = runtime.read(cycle, plan["created_ids"]["a"])
    _, unchanged = apply(runtime, cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", "material_id": item["id"], "expected_revision": item["revision"], "content": item["content"]}])
    assert unchanged["history"]["status"] == "unchanged"
    assert len(runtime.history_list()["versions"]) == 2
    paths = runtime.engine._git("ls-tree", "--name-only", "-r", "HEAD").stdout.splitlines()
    task = runtime.read(material_id=plan["created_ids"]["task"])
    assert sorted(paths) == sorted([runtime.project_read()["path"], "INSTRUCTIONS.md", ".contextopus-settings.json", ".contextopus-instructions/workspace.p.md", item["path"], task["path"]])
    assert not any(path.startswith(("tmp/", ".tmp/")) for path in paths)
    diff = runtime.history_diff(initial["history"]["commit"], result["history"]["commit"])
    assert "+First" in diff["diff"]
    assert "First" in runtime.history_read(paths=[item["path"]])["materials"][0]["text"]


def test_git_retry_uses_old_snapshot_and_commits_in_order(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    create(runtime, git_history_enabled=True)
    cycle = runtime.startup("work")["cycle_id"]
    git = runtime._history_git

    def fail(*args, **kwargs):
        if "commit-tree" in args:
            raise OSError("injected Git failure")
        return git(*args, **kwargs)

    with patch.object(runtime, "_history_git", side_effect=fail):
        first, saved = apply(runtime, cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "first", "content": "First captured state"}])
        assert saved["status"] == "applied"
        assert saved["history"]["status"] == "pending"
        second, saved_later = apply(runtime, cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "second", "content": "Later independent state"}])
        assert saved_later["history"]["status"] == "pending"
    # Uncommitted external change must not enter either captured operation.
    first_relative = runtime.read(material_id=first["created_ids"]["first"])["path"]
    first_path = runtime.engine.root / first_relative
    first_path.write_text(first_path.read_text().replace("First captured state", "External later edit"))
    assert runtime.history_retry()["status"] == "committed"
    versions = runtime.history_list()["versions"]
    assert len(versions) == 3
    assert second["proposal_id"] in versions[0]["subject"]
    assert first["proposal_id"] in versions[1]["subject"]
    blob = runtime.history_read(commit=versions[1]["commit"], paths=[first_relative])["materials"][0]["text"]
    assert "First captured state" in blob
    assert "External later edit" not in blob
    assert "External later edit" in first_path.read_text()
    assert len(runtime.history_list()["versions"]) == 3
    runtime.history_retry()
    assert len(runtime.history_list()["versions"]) == 3
