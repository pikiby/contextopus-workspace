"""Canonical policies, free rules and recoverable legacy upgrades on real storage."""
import copy
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch

import pytest

from workspace.configuration import WorkspaceSettings as Settings, WORKSPACE_POLICY_DEFAULTS
from workspace.cycles import CycleRuntime, _json
from workspace.policies import extract_settings
from workspace.runtime import PCWError, _revision
from workspace.settings import configuration_text
from test_runtime import Factory


CHOICES = {**WORKSPACE_POLICY_DEFAULTS, "knowledge_approval_required": False,
           "git_history_enabled": True, "material_max_tokens": 1024}


def context(tmp_path, **overrides):
    from workspace.analytics import EventStore
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    if plan['needs_migration']:
        store.storage_apply(plan['revision'])
    runtime = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    runtime.workspace_create("create", "# Rules\nRead sources every time.\n", "# Project\nTesting rules.",
                             "Create this context", settings={**CHOICES, **overrides},
                             project_description="Test project purpose and source-reading boundaries")
    return runtime


def downgrade(runtime, instructions):
    """Actual prior-format storage: legacy flags and no canonical policy fields."""
    runtime.engine._write(runtime.engine.root / "INSTRUCTIONS.md", instructions)
    path = Settings(runtime.control.layout)._scope_path("workspace", runtime.engine.workspace)
    path.unlink()
    runtime.control.write(runtime._format_path(), _json({"format_version": 3}))


def legacy_instructions(**changes):
    values = {**CHOICES, **changes}
    flags = "".join(f"{key}: {'null' if value is None else str(value).lower()}\r\n" for key, value in values.items())
    return "---\r\ncustom: 'Keep these exact bytes' # user comment\r\n" + flags + "---\r\n# Правила\r\nKeep all user text.\r\n"


def _retry_readonly_git_unlink(git_object_stores, function, filename, error):
    """Handle only Windows' readonly-file deletion rule in this owned fixture."""
    path = Path(filename)
    try:
        details = path.lstat()
    except OSError:
        error.add_note(f"Snapshot removal: path={path}, metadata unavailable")
        raise error
    attributes = getattr(details, "st_file_attributes", None)
    error.add_note(f"Snapshot removal: function={function.__name__}, path={path}, "
                   f"mode={details.st_mode:o}, file_attributes={attributes}, "
                   f"winerror={getattr(error, 'winerror', None)}")
    readonly = not details.st_mode & stat.S_IWRITE and (
        attributes is None or attributes & stat.FILE_ATTRIBUTE_READONLY)
    if not (isinstance(error, PermissionError) and getattr(error, "winerror", None) == 5
            and function in (os.unlink, os.remove) and stat.S_ISREG(details.st_mode)
            and any(path.resolve().is_relative_to(store.resolve()) for store in git_object_stores)
            and readonly):
        raise error
    path.chmod(details.st_mode | stat.S_IWRITE)
    function(filename)  # One retry only; sharing violations/other failures propagate.


@pytest.mark.parametrize("winerror,readonly,location", [
    (5, True, "live"), (5, True, "retained"), (32, True, "retained"),
    (5, False, "retained"), (5, True, "outside"), (13, True, "retained"),
])
def test_snapshot_cleanup_only_retries_readonly_git_access_denied(tmp_path, winerror, readonly, location):
    owned = tmp_path / "isolated-root"
    live = owned / "project/.git/objects"
    # The Windows failure was in this retained migration backup, not the live
    # workspace's Git directory. Keep that exact path shape in the regression.
    retained = owned / ".pcw/.control/project/backups/reviewed-revision/workspace/.git/objects"
    stores = (live, retained)
    for store in stores:
        store.mkdir(parents=True)
    foreign = tmp_path / "coherent-snapshot/.git/objects"
    foreign.mkdir(parents=True)
    path = {"live": live, "retained": retained, "outside": foreign}[location] / "object"
    path.write_bytes(b"closed fixture file")
    path.chmod(0o444 if readonly else 0o644)
    mode = path.stat().st_mode
    error = PermissionError("Injected Windows deletion failure")
    error.winerror = winerror
    try:
        if winerror == 5 and readonly and location != "outside":
            _retry_readonly_git_unlink(stores, os.unlink, str(path), error)
            assert not path.exists()
        else:
            with pytest.raises(PermissionError) as caught:
                _retry_readonly_git_unlink(stores, os.unlink, str(path), error)
            assert caught.value is error
            assert path.read_bytes() == b"closed fixture file" and path.stat().st_mode == mode
            assert "file_attributes=" in error.__notes__[0]
    finally:
        if path.exists():
            path.chmod(0o644)


def test_free_rules_lightweight_settings_and_explicit_profiles(tmp_path):
    runtime = context(tmp_path)
    try:
        assert runtime.instructions_read()["content"] == "# Rules\nRead sources every time.\n"
        with patch.object(runtime, "_instructions", side_effect=AssertionError("No rules read")):
            text_read, bytes_read = Path.read_text, Path.read_bytes
            def allowed(path):
                assert path != runtime.engine.root / "INSTRUCTIONS.md"
                assert not path.is_relative_to(runtime.engine.knowledge)
                assert not path.is_relative_to(runtime.engine.state)
            def read_text(path, *args, **kwargs):
                allowed(path)
                return text_read(path, *args, **kwargs)
            def read_bytes(path, *args, **kwargs):
                allowed(path)
                return bytes_read(path, *args, **kwargs)
            with patch.object(Path, "read_text", read_text), patch.object(Path, "read_bytes", read_bytes):
                read = runtime.settings_read(fields=["allow_implicit_updates", "reread_instructions"])
                from workspace.server import dispatch
                with patch("workspace.server.runtime", return_value=runtime):
                    observed = dispatch(str(tmp_path), "project", "settings_read",
                                        {"fields": ["allow_implicit_updates", "reread_instructions"]})
                assert observed["values"] == read["values"]
            assert read["values"] == {"allow_implicit_updates": False, "reread_instructions": False}
            assert read["sources"]["allow_implicit_updates"] == "context.configuration"
        preview = runtime.settings_preview({"instruction_profiles": [
            {"agent": "agent-a", "reread_instructions": True},
            {"agent": "agent-a", "model": "model-a", "reread_instructions": False},
        ]}, "Choose repeat reading for a specific agent")
        with pytest.raises(PCWError, match="confirmation"):
            runtime.settings_apply(preview["preview_revision"])
        runtime.settings_apply(preview["preview_revision"], "Apply the shown settings")
        assert runtime.settings_read(agent="agent-a")["values"]["reread_instructions"]
        assert not runtime.settings_read(agent="agent-a", agent_model="model-a")["values"]["reread_instructions"]
        assert not runtime.settings_read()["values"]["reread_instructions"]
        assert not runtime.settings_read(agent="another")["values"]["reread_instructions"]
        assert not runtime._policies()["allow_implicit_updates"]
    finally:
        runtime.close()


def test_durable_settings_preview_history_and_disabling_history(tmp_path):
    from workspace.history_queries import SETTINGS_HISTORY_PATH
    runtime = context(tmp_path)
    old_commit = runtime._history_revision("HEAD")
    preview = runtime.settings_preview({"git_history_enabled": False}, "Turn off local history")
    runtime.close()
    runtime = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    try:
        result = runtime.settings_apply(preview["preview_revision"], "Approve this exact revision")
        assert result["saved"] and result["history"]["status"] == "committed"
        new_commit = result["history"]["commit"]
        before = runtime.history_read(commit=old_commit, paths=[SETTINGS_HISTORY_PATH])["materials"][0]["text"]
        after = runtime.history_read(commit=new_commit, paths=[SETTINGS_HISTORY_PATH])["materials"][0]["text"]
        assert json.loads(before)["values"]["git_history_enabled"]
        assert not json.loads(after)["values"]["git_history_enabled"]
        assert "git_history_enabled" in runtime.history_diff(old_commit, new_commit, paths=[SETTINGS_HISTORY_PATH])["diff"]
        assert runtime.history_search("git_history_enabled", paths=[SETTINGS_HISTORY_PATH])["versions"]
        assert not (runtime.engine.root / SETTINGS_HISTORY_PATH).exists()
        assert runtime._history_git("status", "--porcelain", "--untracked-files=no").stdout == ""
        assert runtime.settings_apply(preview["preview_revision"])["replayed"]
    finally:
        runtime.close()


def test_history_retry_uses_exact_settings_snapshot_after_later_edit(tmp_path):
    from workspace.history_queries import SETTINGS_HISTORY_PATH
    runtime = context(tmp_path)
    original_git = runtime._history_git
    def fail_commit(*args, **kwargs):
        if args[0] == "commit-tree":
            raise PCWError("Injected Git commit failure")
        return original_git(*args, **kwargs)
    try:
        first = runtime.settings_preview({"material_max_tokens": 2048}, "Change the per-material limit")
        with patch.object(runtime, "_history_git", side_effect=fail_commit):
            result = runtime.settings_apply(first["preview_revision"], "Approve this limit")
            assert result["saved"] and result["history"]["status"] == "pending"
            second = runtime.settings_preview({"material_max_tokens": None}, "Remove the per-material limit")
            result = runtime.settings_apply(second["preview_revision"], "Approve removing the limit")
            assert result["saved"] and result["history"]["status"] == "pending"
        result = runtime.settings_apply(first["preview_revision"])
        assert result["replayed"] and result["history"]["status"] == "committed"
        captured = runtime.history_read(commit=result["history"]["commit"], paths=[SETTINGS_HISTORY_PATH])["materials"][0]["text"]
        assert json.loads(captured)["values"]["material_max_tokens"] == 2048
        assert runtime._policies()["material_max_tokens"] is None
    finally:
        runtime.close()


def test_analytics_records_separate_historical_settings_basis(tmp_path):
    from time import perf_counter
    from workspace.analytics_observation import Observation
    runtime = context(tmp_path)
    try:
        store = runtime._events()
        def observe():
            observation = Observation(runtime, "read", {})
            event = store.begin("project", "read", {}, metadata=observation.metadata)
            store.end(event, "project", perf_counter(), result={}, references=observation.references)
            return event, observation
        old_event, before = observe()
        preview = runtime.settings_preview({"material_max_tokens": 2048}, "Change the material limit")
        runtime.settings_apply(preview["preview_revision"], "Approve")
        new_event, after = observe()
        assert before.metadata["instructions_revision"] == after.metadata["instructions_revision"]
        assert before.metadata["settings_revision"] != after.metadata["settings_revision"]
        assert before.metadata["settings_ref"]["status"] == "versioned"
        old = runtime.analytics_reference_read(old_event, reference_index=0)
        assert old["status"] == "available"
        assert json.loads(old["text"])["values"]["material_max_tokens"] == 1024
        report = runtime.analytics_report()
        assert len(report["instruction_basis"]) == 2
        assert all(era["instructions_ref"].get("settings_revision") for era in report["instruction_basis"])
        assert runtime.analytics_read(old_event)["instructions_ref"] != runtime.analytics_read(new_event)["instructions_ref"]
    finally:
        runtime.close()


def test_generic_settings_cannot_override_policies_and_old_policy_approves_edits(tmp_path):
    runtime = context(tmp_path)
    try:
        core = Settings(runtime.control.layout)
        for kwargs in ({}, {"module": "workspace", "context": "project"}, {"request_only": True}):
            with pytest.raises(ValueError, match="owning module settings"):
                core.preview({"knowledge_approval_required": False}, **kwargs)
        with pytest.raises(ValueError, match="overrides"):
            core.snapshot(request={"allow_implicit_updates": True})
        p = runtime.settings_preview({"instructions_approval_required": False}, "Disable settings approval")
        with pytest.raises(PCWError, match="confirmation"):
            runtime.settings_apply(p["preview_revision"])
        runtime.settings_apply(p["preview_revision"], "Approve this revision")
        p = runtime.settings_preview({"allow_implicit_updates": True}, "Enable implicit work")
        runtime.settings_apply(p["preview_revision"])
        assert runtime._policies()["allow_implicit_updates"]
    finally:
        runtime.close()


def test_repeating_the_same_toggle_creates_a_new_durable_review(tmp_path):
    runtime = context(tmp_path, git_history_enabled=False, instructions_approval_required=False)
    try:
        first = runtime.settings_preview({"allow_implicit_updates": True}, "Enable implicit updates")
        runtime.settings_apply(first["preview_revision"])
        reset = runtime.settings_preview({"allow_implicit_updates": False}, "Disable implicit updates")
        runtime.settings_apply(reset["preview_revision"])
        repeated = runtime.settings_preview({"allow_implicit_updates": True}, "Enable implicit updates")
        assert repeated["preview_revision"] != first["preview_revision"]
        assert not runtime.settings_apply(repeated["preview_revision"]).get("replayed", False)
        assert runtime._policies()["allow_implicit_updates"]
    finally:
        runtime.close()


def test_unrelated_context_change_rejects_review_before_applying_state(tmp_path):
    runtime = context(tmp_path, git_history_enabled=False, instructions_approval_required=False)
    try:
        old = runtime.settings_preview({"allow_implicit_updates": True}, "Enable implicit updates")
        core = Settings(runtime.control.layout)
        unrelated = core.preview({"searchN": 7}, module="workspace", context="project")
        core.apply(unrelated["preview_revision"])
        with pytest.raises(PCWError, match="Configuration changed"):
            runtime.settings_apply(old["preview_revision"])
        receipt = json.loads(runtime._settings_receipt_path(old["preview_revision"]).read_text())
        assert receipt["state"] == "prepared"
        fresh = runtime.settings_preview({"allow_implicit_updates": True}, "Enable implicit updates")
        runtime.settings_apply(fresh["preview_revision"])
        assert runtime._policies()["allow_implicit_updates"]
    finally:
        runtime.close()


def test_changed_settings_invalidate_prepared_proposal_without_changing_rules(tmp_path):
    runtime = context(tmp_path)
    try:
        cycle = runtime.startup("work")["cycle_id"]
        proposal = runtime.proposal_prepare(cycle, [{"key": "fact", "content": "# Fact\nVerified behavior.",
                                                     "description": "Behavior checked during this test"}])
        rules = runtime.instructions_read()
        p = runtime.settings_preview({"knowledge_approval_required": True}, "Require approval for future changes")
        runtime.settings_apply(p["preview_revision"], "Approved")
        assert runtime.instructions_read() == rules
        with pytest.raises(PCWError, match="Settings changed"):
            runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
        assert runtime.tmp_list(cycle)["total"] == 0
    finally:
        runtime.close()


def test_format_three_upgrade_preserves_user_bytes_settings_work_and_backups(tmp_path):
    runtime = context(tmp_path)
    try:
        cycle = runtime.startup("ongoing")["cycle_id"]
        temporary = runtime.tmp_write(cycle, "Ongoing work must remain")
        old = legacy_instructions(allow_implicit_updates=True, material_max_tokens=None)
        downgrade(runtime, old)
        plan = runtime.upgrade_plan()
        assert plan["from_version"] == 3 and plan["to_version"] == 5 and plan["ready"]
        result = runtime.upgrade_apply(plan["revision"], "Upgrade to 1.3")
        assert result["status"] == "complete"
        assert (Path(result["backup"]) / "INSTRUCTIONS.md").read_bytes() == old.encode()
        assert runtime.instructions_read()["content"] == (
            "---\r\ncustom: 'Keep these exact bytes' # user comment\r\n---\r\n# Правила\r\nKeep all user text.\r\n")
        assert runtime._policies() == {**CHOICES, "allow_implicit_updates": True, "material_max_tokens": None}
        assert runtime.tmp_read(cycle, temporary["id"])["content"] == "Ongoing work must remain"
        assert runtime._records()["cycles"][cycle]["state"] == "active"
        assert runtime.upgrade_apply(plan["revision"], "Retry")["replayed"]
        assert runtime.upgrade_plan()["compatible"]
    finally:
        runtime.close()


def test_settings_transition_resumes_after_instruction_write_crash(tmp_path):
    runtime = context(tmp_path)
    try:
        old = legacy_instructions()
        downgrade(runtime, old)
        plan = runtime.upgrade_plan()
        original = runtime.engine._write
        failed = False
        def crash(path, text):
            nonlocal failed
            if path.name == "INSTRUCTIONS.md" and not failed:
                failed = True
                raise OSError("Injected interruption after configuration write")
            return original(path, text)
        with patch.object(runtime.engine, "_write", side_effect=crash), pytest.raises(OSError, match="Injected"):
            runtime.upgrade_apply(plan["revision"], "Upgrade")
        assert json.loads(configuration_text(runtime.control.layout, "project"))["material_max_tokens"] == 1024
        assert runtime.upgrade_apply(plan["revision"], "Resume")["status"] == "complete"
        assert runtime.instructions_read()["content"] == extract_settings(old)[1]
    finally:
        runtime.close()


def test_legacy_instruction_flag_delta_blocks_only_its_apply_and_preserves_raw(tmp_path):
    runtime = context(tmp_path)
    try:
        before = legacy_instructions()
        cycle = runtime.startup("rules", purpose="instructions")["cycle_id"]
        runtime.engine._write(runtime.engine.root / "INSTRUCTIONS.md", before)
        after = legacy_instructions(instructions_approval_required=False)
        with runtime._cycle(cycle) as (records, state):
            raw_plan = {"cycle_id": cycle, "proposal_id": "legacy-rules", "kind": "instructions",
                        "instructions_approval_required": True, "user_request": "Change settings",
                        "created_ids": {}, "entries": {"INSTRUCTIONS.md": {
                            "before": {"path": "INSTRUCTIONS.md", "text": before, "fingerprint": _revision(before)},
                            "after": {"path": "INSTRUCTIONS.md", "text": after, "fingerprint": _revision(after)}}}}
            proposal = runtime._save_plan(records, state, raw_plan, "prepared")
        entry = runtime._records()["cycles"][cycle]["proposals"][proposal["proposal_id"]]
        raw = runtime._load_plan(cycle, proposal["proposal_id"], entry)
        downgrade(runtime, before)
        plan = runtime.upgrade_plan()
        assert runtime.upgrade_apply(plan["revision"], "Upgrade")["status"] == "complete"
        assert runtime._load_plan(cycle, proposal["proposal_id"], entry) == raw
        with pytest.raises(PCWError, match="settings_preview/settings_apply"):
            runtime.instructions_apply(cycle, proposal["proposal_id"], proposal["revision"], "Approved")
        assert runtime._records()["cycles"][cycle]["proposals"][proposal["proposal_id"]]["state"] == "prepared"
        assert runtime._policies()["instructions_approval_required"]
    finally:
        runtime.close()


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_quiescent_root_snapshot_restores_data_configuration_git_sqlite_and_rag(tmp_path, newline):
    """Restore one stopped-owner snapshot, then retry the same technical upgrade.

    This is an isolated acceptance exercise, not a live rollback operation. The
    whole root is copied only after closing LanceDB and while holding its guard;
    SQLite uses its backup API so a WAL is never copied as a moving database.
    """
    from contextlib import closing
    import shutil
    import sqlite3
    from workspace.history_queries import SETTINGS_HISTORY_PATH
    from workspace.settings import save_configuration

    root, snapshot = tmp_path / "isolated-root", tmp_path / "coherent-snapshot"
    root.mkdir()
    runtime = context(root)
    layout = runtime.control.layout
    cycle = runtime.startup("unfinished-before-snapshot")["cycle_id"]
    temporary = runtime.tmp_write(cycle, "Keep this unfinished investigation")
    proposal = runtime.proposal_prepare(cycle, [{"key": "pending", "content": f"# Pending факт Н{newline}Checked conditions.",
                                                "description": "Verified conditions awaiting application"}])
    old_rules = legacy_instructions(allow_implicit_updates=True, material_max_tokens=None).replace("\r\n", newline)
    downgrade(runtime, old_rules)
    # Reconstruct the prior Core context file, which held search settings only.
    old_configuration = '{\r\n  "searchN": 7\r\n}\r\n'.replace("\r\n", newline)
    save_configuration(layout, "project", old_configuration)
    records = runtime._records()
    entry = records["cycles"][cycle]["proposals"][proposal["proposal_id"]]
    old_proposal = runtime._load_plan(cycle, proposal["proposal_id"], entry)
    old_proposal.pop("settings_revision")
    runtime._edits().release(cycle)
    old_proposal.pop("edit_reservations", None)
    old_proposal["instructions_revision"] = _revision(old_rules)
    runtime.engine._write(runtime._plan_path(cycle, proposal["proposal_id"]), _json(old_proposal))
    entry["revision"] = proposal["revision"] = _revision(_json(old_proposal))
    runtime._persist(records)
    # The legacy HEAD has the policy frontmatter and no virtual settings path.
    runtime._history_git("update-index", "--no-skip-worktree", "--", SETTINGS_HISTORY_PATH)
    runtime._history_git("update-index", "--force-remove", "--", SETTINGS_HISTORY_PATH)
    runtime._history_git("add", "--", "INSTRUCTIONS.md", "knowledge/")
    runtime._history_git("commit", "-m", "Legacy format-three snapshot before upgrade")
    old_head = runtime._history_revision("HEAD")
    assert runtime._history_blob(old_head, SETTINGS_HISTORY_PATH) is None
    events = runtime._events()
    event_id = events.begin("project", "snapshot-acceptance", {"marker": "before"})
    from time import monotonic
    events.end(event_id, "project", monotonic(), result={"marker": "before"})
    database_relative = events.path.relative_to(root)
    runtime.close()
    # Exercise readonly Git objects deliberately and verify copy2 keeps their
    # attributes. No global Git setting or original snapshot permission is reset.
    git_objects = runtime.engine.root / ".git/objects"
    git_object = next(path for path in sorted(git_objects.rglob("*")) if path.is_file())
    git_object.chmod(git_object.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    assert not git_object.stat().st_mode & stat.S_IWRITE
    if os.name == "nt":
        assert git_object.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    with layout.guard():
        before_sources = runtime._source_files()
        before_rag = runtime._migration_tree(runtime.engine.state)
        before_records = runtime.record_path.read_bytes()
        shutil.copytree(root, snapshot)
        copied_object = snapshot / git_object.relative_to(root)
        assert copied_object.read_bytes() == git_object.read_bytes()
        assert not copied_object.stat().st_mode & stat.S_IWRITE
        if os.name == "nt":
            assert copied_object.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        # Native EventStore.connect owns a write transaction; snapshot through
        # a separate read-only connection after the fixture owner is stopped.
        with closing(sqlite3.connect(events.path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot / database_relative)) as destination:
            source.backup(destination)
        # Connection's own context manager only completes transactions. Close
        # its file handles before removing WAL sidecars, including on Windows.
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            destination.execute("SELECT 1")
        for suffix in ("-wal", "-shm"):
            Path(str(snapshot / database_relative) + suffix).unlink(missing_ok=True)
        snapshot_permissions = {path.relative_to(snapshot): path.lstat().st_mode for path in snapshot.rglob("*")}

    runtime = CycleRuntime(root, "project", provider_factory=Factory())
    plan = runtime.upgrade_plan()
    write = runtime.engine._write
    def interrupted(path, text):
        if path.name == "INSTRUCTIONS.md":
            raise OSError("Injected interruption after canonical settings save")
        return write(path, text)
    with patch.object(runtime.engine, "_write", side_effect=interrupted), pytest.raises(OSError, match="Injected"):
        runtime.upgrade_apply(plan["revision"], "Upgrade the isolated copy")
    assert json.loads(configuration_text(layout, "project"))["allow_implicit_updates"]
    assert runtime.upgrade_apply(plan["revision"], "Resume the isolated upgrade")["status"] == "complete"
    assert runtime._history_blob("HEAD", SETTINGS_HISTORY_PATH)
    after_event = runtime._events().begin("project", "snapshot-acceptance", {"marker": "after"})
    assert runtime._events().read(after_event)["state"] == "started"
    runtime.close()

    # Switch back one complete stopped-owner root, including the Core file,
    # Git objects/index, control state, SQLite snapshot and shared RAG tree.
    with layout.guard():
        # Include only actual object stores in this closed, disposable root.
        # Migration retains additional copies in hidden .backups (and legacy backups).
        git_object_stores = tuple(path / "objects" for path in root.rglob(".git")
                                  if path.is_dir() and not path.is_symlink()
                                  and (path / "objects").is_dir() and not (path / "objects").is_symlink()
                                  and (path / "objects").resolve().is_relative_to(root.resolve()))
        assert git_objects in git_object_stores
        retained_stores = [store for store in git_object_stores
                           if any(store.is_relative_to(runtime.control.root / name) for name in ("backups", ".backups"))]
        assert retained_stores, "The full restore must cover retained migration backups too"
        for store in retained_stores:
            retained_object = store / git_object.relative_to(git_objects)
            assert retained_object.read_bytes() == copied_object.read_bytes()
            assert not retained_object.stat().st_mode & stat.S_IWRITE
            if os.name == "nt":
                assert retained_object.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        for child in list(root.iterdir()):
            if child.name == ".contextopus.lock":
                continue
            if child.is_dir():
                shutil.rmtree(child, onexc=lambda function, path, error:
                              _retry_readonly_git_unlink(git_object_stores, function, path, error))
            else:
                child.unlink()
        for child in snapshot.iterdir():
            if child.name == ".contextopus.lock":
                continue
            shutil.copytree(child, root / child.name) if child.is_dir() else shutil.copy2(child, root / child.name)

    restored = CycleRuntime(root, "project", provider_factory=Factory())
    try:
        assert restored._format() == 3
        assert restored._source_files() == before_sources
        assert restored._migration_tree(restored.engine.state) == before_rag
        assert restored.record_path.read_bytes() == before_records
        assert configuration_text(layout, "project") == old_configuration
        assert restored._instructions()["content"] == old_rules
        assert restored._history_revision("HEAD") == old_head
        assert git_object.read_bytes() == copied_object.read_bytes()
        assert not git_object.stat().st_mode & stat.S_IWRITE
        if os.name == "nt":
            assert git_object.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert restored._history_blob(old_head, SETTINGS_HISTORY_PATH) is None
        assert restored._history_git("status", "--porcelain", "--untracked-files=no").stdout == ""
        assert restored._events().read(event_id)["result"]["value"] == {"marker": "before"}
        with pytest.raises(PCWError, match="Unknown analytics event"):
            restored._events().read(after_event)
        assert restored.tmp_read(cycle, temporary["id"])["content"] == "Keep this unfinished investigation"
        assert restored._load_plan(cycle, proposal["proposal_id"], entry) == old_proposal
        fresh_plan = restored.upgrade_plan()
        assert restored.upgrade_apply(fresh_plan["revision"], "Reapply after coherent restore")["status"] == "complete"
        assert restored._policies() == {**CHOICES, "allow_implicit_updates": True, "material_max_tokens": None}
        assert json.loads(configuration_text(layout, "project"))["searchN"] == 7
        assert restored.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])["status"] == "applied"
        found = restored.search(query="Checked conditions", types=["knowledge"], d_max=2)
        assert [item["id"] for item in found["results"]] == [proposal["created_ids"]["pending"]]
        assert not (restored.engine.root / SETTINGS_HISTORY_PATH).exists()
        assert {path.relative_to(snapshot): path.lstat().st_mode for path in snapshot.rglob("*")} == snapshot_permissions
    finally:
        restored.close()
