"""Old sparse contexts are adopted only with reviewed explicit human rules."""

from importlib.resources import files
from pathlib import Path
import subprocess
import pytest
from workspace.cycles import CycleRuntime
from workspace.runtime import _parse
from contextopus.core import relocation
from test_runtime import Factory


def sparse(root):
    context = root / ".pcw/old"
    (context / "knowledge").mkdir(parents=True)
    (context / "knowledge/topic.md").write_text("# Original\nLiteral source body.\n", encoding="utf-8", newline="")
    (context / "current.md").write_bytes(b"original ongoing task\r\n")
    (context / "tmp").mkdir()
    (context / "tmp/result.md").write_bytes(b"preserved draft\x00\xff")
    subprocess.run(["git", "init", "-q", str(context)], check=True)
    subprocess.run(
        ["git", "-C", str(context), "add", "current.md", "knowledge/topic.md"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(context),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Original",
        ],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(context), "rev-parse", "HEAD"], text=True
    ).strip()
    return context, head


@pytest.mark.parametrize("relocate_first", [False, True])
def test_sparse_legacy_needs_reviewed_rules_preserves_originals(
    tmp_path, relocate_first
):
    source = tmp_path / "source"
    source.mkdir()
    original, head = sparse(source)
    expected_current = (original / "current.md").read_bytes()
    expected_tmp = (original / "tmp/result.md").read_bytes()
    if relocate_first:
        relocation.apply(relocation.plan(source, tmp_path / "destination"))
    runtime = CycleRuntime(source, "old", provider_factory=Factory())
    try:
        initial = runtime.upgrade_plan()
        assert initial["from_version"] == 0
        assert initial["ready"] is False
        assert initial["instruction_initialization"]["status"] == "never_initialized"
        assert not (runtime.engine.root / "INSTRUCTIONS.md").exists()
        rules = (
            files("workspace")
            .joinpath("default_instructions.md")
            .read_text()
        )
        reviewed = runtime.upgrade_plan(instructions_content=rules)
        assert reviewed["ready"] is True
        assert reviewed["instruction_initialization"]["content"] == rules
        with pytest.raises(ValueError, match="confirmation"):
            runtime.upgrade_apply(
                reviewed["revision"], automatic=True, instructions_content=rules
            )
        with pytest.raises(ValueError, match="changed"):
            runtime.upgrade_apply(
                reviewed["revision"],
                automatic=True,
                instructions_content=rules + "\nDifferent rules",
                instructions_confirmation="Approved prior text",
            )
        result = runtime.upgrade_apply(
            reviewed["revision"],
            automatic=True,
            instructions_content=rules,
            instructions_confirmation="Approve this complete reviewed rules revision",
        )
        assert result["status"] == "complete"
        assert runtime.instructions_read()["content"] == rules
        assert not (runtime.engine.root / "current.md").exists()
        tasks = [m for m in runtime.list()["materials"] if m["type"] == "task"]
        assert len(tasks) == 1
        assert expected_current.decode().strip() in runtime.read(material_id=tasks[0]["id"])["content"]
        assert (runtime.engine.root / ".tmp/result.md").read_bytes() == expected_tmp
        assert (Path(result["backup"]) / "tmp/result.md").read_bytes() == expected_tmp
        assert not (runtime.engine.root / "tmp").exists()
        assert runtime.project_read()["path"] == "Context.md"
        assert runtime._project_description_status()["status"] == "placeholder"
        material = next(m for m in runtime.list()["materials"] if m["type"] == "knowledge")
        assert (
            runtime.read(material_id=material["id"])["content"]
            == "# Original\nLiteral source body.\n"
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(runtime.engine.root),
                "merge-base",
                "--is-ancestor",
                head,
                "HEAD",
            ],
            check=True,
        )
        assert (
            runtime.upgrade_apply(reviewed["revision"], automatic=True)["replayed"]
            is True
        )
    finally:
        runtime.close()


def test_previously_initialized_lost_rules_are_not_replaced(tmp_path):
    original, head = sparse(tmp_path)
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    try:
        with runtime.control.guard():
            runtime.control.write(
                runtime.control.root / "instructions-initialized.json",
                '{"initialized":true}',
            )
        rules = (
            files("workspace")
            .joinpath("default_instructions.md")
            .read_text()
        )
        plan = runtime.upgrade_plan(instructions_content=rules)
        assert plan["ready"] is False
        assert plan["instruction_initialization"]["status"] == "lost_initialized_rules"
        with pytest.raises(ValueError, match="blocked"):
            runtime.upgrade_apply(
                plan["revision"],
                automatic=True,
                instructions_content=rules,
                instructions_confirmation="Replace the lost rules",
            )
        assert not (original / "INSTRUCTIONS.md").exists()
    finally:
        runtime.close()


def test_case_insensitive_current_alias_is_migrated_once(tmp_path, monkeypatch):
    """Reproduce macOS alias lookup while keeping Linux directory enumeration real."""
    from pathlib import Path
    from workspace import migration
    original, _ = sparse(tmp_path)
    runtime = CycleRuntime(tmp_path, 'old', provider_factory=Factory())
    actual = original / 'current.md'
    alias = original / 'CURRENT.md'
    is_file = Path.is_file
    read = migration.read_utf8
    try:
        monkeypatch.setattr(Path, 'is_file', lambda path: is_file(actual if path == alias else path))
        monkeypatch.setattr(migration, 'read_utf8', lambda path: read(actual if path == alias else path))
        assert alias.is_file() and actual.is_file()
        transformed, _ = runtime._v2_transforms(0)
        tasks = [item for item in transformed.values() if _parse(item['text'])[0]['type'] == 'task']
        assert len(tasks) == 1
        assert set(transformed) == {'knowledge/topic.md', 'current.md', 'Context.md'}
        assert 'original ongoing task' in tasks[0]['text']
        assert actual.read_bytes() == b'original ongoing task\r\n'
    finally:
        runtime.close()


def test_distinct_current_case_entries_are_both_preserved(tmp_path):
    original, _ = sparse(tmp_path)
    uppercase = original / 'CURRENT.md'
    if uppercase.exists():
        pytest.skip('Distinct case variants require a case-sensitive filesystem')
    uppercase.write_text('Separate uppercase task', encoding='utf-8')
    runtime = CycleRuntime(tmp_path, 'old', provider_factory=Factory())
    try:
        transformed, _ = runtime._v2_transforms(0)
        tasks = [item for item in transformed.values() if _parse(item['text'])[0]['type'] == 'task']
        assert len(tasks) == 2
        assert {'current.md', 'CURRENT.md'} <= set(transformed)
        assert len({task['id'] for task in tasks}) == 2
        assert len({task['path'] for task in tasks}) == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("existing_rules", [False, True], ids=["initial-rules", "existing-rules"])
def test_public_legacy_adoption_preserves_pending_rule_basis(tmp_path, monkeypatch, existing_rules):
    """Initializing rules must not turn an old approval into a new rules review."""
    from unittest.mock import Mock

    from workspace import server as workspace_server
    from workspace.cycles import _json
    from workspace.runtime import PCWError, _revision
    from contextopus.startup import Application

    original, _ = sparse(tmp_path)
    rules = "# Human rules\nPreserve facts and require approval for knowledge changes.\n"
    legacy_rules = "---\nknowledge_approval_required: true\ngit_history_enabled: false\n---\n" + rules
    if existing_rules:
        (original / "INSTRUCTIONS.md").write_bytes(legacy_rules.encode("utf-8"))
    foreign_root = tmp_path / ".pcw/other/knowledge"
    foreign_root.mkdir(parents=True)
    (foreign_root / "topic.md").write_bytes(b"# Foreign topic\nUnchanged foreign fact.\n")
    foreign = CycleRuntime(tmp_path, "other", provider_factory=Factory())
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    try:
        source = original / "knowledge/topic.md"
        source_before = source.read_bytes()
        snapshot = {"path": "knowledge/topic.md", "text": source_before.decode("utf-8"),
                    "fingerprint": _revision(source_before.decode("utf-8"))}
        changed = {**snapshot, "text": snapshot["text"].replace("Literal source body.", "Reviewed later body.")}
        changed["fingerprint"] = _revision(changed["text"])
        records = {"starts": {}, "cycles": {}}
        originals = {}
        revisions = {}
        for cycle, proposal, state, before, after in (
            ("recovering", "legacy-create", "applying", None, snapshot),
            ("awaiting-review", "legacy-edit", "prepared", snapshot, changed),
        ):
            plan = {"cycle_id": cycle, "proposal_id": proposal,
                    "created_ids": {"created": "topic.md"} if before is None else {},
                    "knowledge_approval_required": True,
                    "entries": {"topic.md": {"before": before, "after": after}}}
            if existing_rules:
                plan["instructions_revision"] = _revision(legacy_rules)
            raw = _json(plan)
            revisions[proposal] = _revision(raw)
            path = runtime._plan_path(cycle, proposal)
            runtime.engine._write(path, raw)
            runtime.engine._write(path.with_suffix(".md"), runtime._review(plan))
            originals[path] = path.read_bytes()
            originals[path.with_suffix(".md")] = path.with_suffix(".md").read_bytes()
            records["cycles"][cycle] = {"state": "active", "purpose": "work", "proposals": {
                proposal: {"revision": revisions[proposal], "state": state}}}
        with runtime.control.guard():
            runtime._persist(records)
        registry_before = runtime.record_path.read_bytes()

        # Exercise the same validated operation registry and observation boundary
        # used by public MCP; inject only the test-only embedding runtime.
        monkeypatch.setattr(workspace_server, "runtime", Mock(return_value=runtime))
        with Application(tmp_path, context="old", discover=False) as app:
            from workspace_support import install_workspace, workspace_module
            install_workspace(tmp_path, app=app)
            monkeypatch.setattr(workspace_module(app, 'server'), 'runtime', Mock(return_value=runtime))
            arguments = {} if existing_rules else {"instructions_content": rules}
            preview = app.query("workspace.upgrade_plan", arguments)
            assert preview["ready"], preview
            assert preview["from_version"] == 0
            assert preview["instruction_transition"]["before"] == (legacy_rules if existing_rules else None)
            assert source.read_bytes() == source_before
            assert runtime.record_path.read_bytes() == registry_before
            assert all(path.read_bytes() == raw for path, raw in originals.items())
            if not existing_rules:
                assert not (original / "INSTRUCTIONS.md").exists()
            applied = app.execute("workspace.upgrade_apply", {
                "expected_revision": preview["revision"], "user_request": "Apply reviewed technical migration",
                **({"instructions_content": rules, "instructions_confirmation": "Approve these first full rules"}
                   if not existing_rules else {}),
            })
            assert applied["status"] == "complete"
            assert runtime.instructions_read()["content"] == rules
            assert runtime.read(material_id="topic.md")["content"] == snapshot["text"]
            assert runtime.record_path.read_bytes() == registry_before
            assert all(path.read_bytes() == raw for path, raw in originals.items())
            assert app.execute("workspace.upgrade_apply", {
                "expected_revision": preview["revision"], "user_request": "Retry same migration",
            })["replayed"]

            pending = runtime._records()["cycles"]["awaiting-review"]["proposals"]["legacy-edit"]
            original_plan = runtime._load_plan("awaiting-review", "legacy-edit", pending)
            overlay = runtime._execution_plan(original_plan)
            if existing_rules:
                assert overlay["instructions_revision"] == _revision(rules)
            else:
                assert "instructions_revision" not in overlay

            # An already-started exact write remains recoverable, but still
            # requires its original explicit knowledge approval.
            with pytest.raises(PCWError, match="confirmation"):
                app.execute("workspace.proposal_apply", {
                    "cycle_id": "recovering", "proposal_id": "legacy-create",
                    "expected_revision": revisions["legacy-create"],
                })
            assert app.execute("workspace.proposal_apply", {
                "cycle_id": "recovering", "proposal_id": "legacy-create",
                "expected_revision": revisions["legacy-create"], "confirmation": "Resume original exact review",
            })["status"] == "applied"
            edit = {"cycle_id": "awaiting-review", "proposal_id": "legacy-edit",
                    "expected_revision": revisions["legacy-edit"], "confirmation": "Original knowledge approval"}
            if existing_rules:
                assert app.execute("workspace.proposal_apply", edit)["status"] == "applied"
                assert runtime.read(material_id="topic.md")["content"] == changed["text"]
            else:
                with pytest.raises(PCWError, match="Instructions changed.*prepare"):
                    app.execute("workspace.proposal_apply", edit)
                assert runtime.read(material_id="topic.md")["content"] == snapshot["text"]
                assert runtime._records()["cycles"]["awaiting-review"]["proposals"]["legacy-edit"] == pending
            assert all(path.read_bytes() == raw for path, raw in originals.items())
    finally:
        runtime.close()
        foreign.close()
