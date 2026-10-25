"""UTF-8 source bytes and writable snapshot durability are OS-independent."""

import os
from workspace.text_io import read_utf8


def test_literal_utf8_reader_keeps_crlf_cr_and_unicode(tmp_path):
    text = "# Цель 🙂\r\nBody\rStandalone\n"
    path = tmp_path / "rules.md"
    path.write_bytes(text.encode("utf-8"))
    assert read_utf8(path) == text
    assert path.read_bytes() == text.encode("utf-8")


def test_snapshot_sync_uses_original_writable_descriptor(tmp_path, monkeypatch):
    from test_shared_v4 import context
    from test_runtime import Factory
    from workspace.workspaces import file_hash

    runtime = context(tmp_path, "alpha", Factory())
    source = runtime.engine.root / "original"
    source.mkdir()
    body = source / "readonly.md"
    body.write_bytes("literal Цель\r\n".encode("utf-8"))
    body.chmod(0o444)
    destination = runtime.control.root / "backups/test-copy/workspace"
    sync = os.fsync
    observed = []

    def writable_sync(fd):
        # Zero-byte write does not change data and fails on read-only fds.
        os.write(fd, b"")
        observed.append(fd)
        return sync(fd)

    monkeypatch.setattr(os, "fsync", writable_sync)
    try:
        runtime._migration_copy(source, destination, {"readonly.md": file_hash(body)})
        assert observed
        assert (destination / "readonly.md").read_bytes() == body.read_bytes()
    finally:
        body.chmod(0o644)
        target = destination / "readonly.md"
        if target.exists():
            target.chmod(0o644)
        runtime.close()


def test_unicode_root_upgrade_receipt_replays_after_reopen(tmp_path):
    from test_legacy_adoption import sparse
    from test_runtime import Factory
    from workspace.cycles import CycleRuntime

    root = tmp_path / "Русский путь"
    root.mkdir()
    sparse(root)
    rules = (
        "---\nallow_implicit_updates: false\nknowledge_approval_required: true\ninstructions_approval_required: true\n"
        "git_history_enabled: true\nmaterial_max_tokens: null\n"
        "create_required_sources: false\n---\n# Правила\n"
        "Сохранять исходные данные без изменений.\n"
    )
    runtime = CycleRuntime(root, "old", provider_factory=Factory())
    try:
        plan = runtime.upgrade_plan(instructions_content=rules)
        revision = plan["revision"]
        result = runtime.upgrade_apply(
            revision,
            automatic=True,
            instructions_content=rules,
            instructions_confirmation="Подтверждаю полный текст правил",
        )
        assert result["status"] == "complete"
        receipt = runtime._migration_receipt_path()
        assert "Правила" in receipt.read_bytes().decode("utf-8")
    finally:
        runtime.close()
    reopened = CycleRuntime(root, "old", provider_factory=Factory())
    try:
        assert reopened.upgrade_apply(revision, automatic=True)["replayed"] is True
        from workspace.policies import extract_settings
        assert reopened.instructions_read()["content"] == extract_settings(rules)[1]
        assert reopened.upgrade_plan()["from_version"] == 6
    finally:
        reopened.close()


def test_windows_migration_transforms_match_posix_manifest_keys(tmp_path, monkeypatch):
    from pathlib import PureWindowsPath
    from test_legacy_adoption import sparse
    from test_runtime import Factory
    from workspace.cycles import CycleRuntime
    from workspace import migration

    sparse(tmp_path)
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    try:
        monkeypatch.setattr(migration, "Path", PureWindowsPath)
        transforms, _ = runtime._v2_transforms(0)
        assert transforms
        assert all("\\" not in item["path"] for item in transforms.values())
        assert all(
            item["path"] == "Context.md" or item["path"].startswith("knowledge/") for item in transforms.values()
        )
    finally:
        runtime.close()
