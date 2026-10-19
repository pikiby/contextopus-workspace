"""Native Hermes home/profile integration through the shared Contextopus MCP."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from contextopus.integrations import IntegrationConflict, integrate, plan
from workspace_support import install_workspace


@pytest.fixture
def workspace_root(tmp_path):
    root = tmp_path / "core"
    install_workspace(root)
    return root


@pytest.fixture(autouse=True)
def isolated_hermes_environment(monkeypatch):
    from contextopus.integrations import hermes

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_DATA_DIR_SUFFIX", raising=False)
    monkeypatch.setattr(hermes, "sys", SimpleNamespace(platform="linux"))


def test_native_home_preserves_yaml_and_custom_skills_and_is_idempotent(tmp_path, workspace_root):
    home = tmp_path / ".hermes"
    home.mkdir()
    config = home / "config.yaml"
    original = '# keep my settings\nmodel: "custom-model" # model comment\nmcp_servers:\n  other:\n    url: "https://other/mcp" # keep URL\n    headers:\n      Authorization: "Bearer private-value"\nagent:\n  max_turns: 17\n'
    config.write_text(original)
    custom = home / "skills/custom/SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("my skill")

    preview = plan("hermes", tmp_path, scope="user", root=workspace_root)
    assert config.read_text() == original  # Preview has no writes.
    assert not (home / "skills/w").exists()
    integrate(preview)
    actual = config.read_text()
    assert actual.replace('  contextopus:\n    url: http://127.0.0.1:8765/mcp\n    tools:\n      resources: false\n      prompts: false\n', "") == original
    assert yaml.safe_load(actual)["mcp_servers"]["contextopus"] == {
        "url": "http://127.0.0.1:8765/mcp", "tools": {"resources": False, "prompts": False},
    }
    assert custom.read_text() == "my skill"
    assert {path.parent.name for path in (home / "skills").glob("*/SKILL.md")} == {
        "custom", "m", "w", "w-analyze", "w-context", "w-create", "w-search", "w-settings",
    }
    assert not integrate(plan("hermes", tmp_path, scope="user", root=workspace_root))["written"]












def test_managed_skill_upgrade_and_modified_collision(tmp_path, workspace_root):
    integrate(plan("hermes", tmp_path, scope="user", root=workspace_root))
    home = tmp_path / ".hermes"
    target = home / "skills/w/SKILL.md"
    current = target.read_bytes()
    target.write_text("older managed skill")
    receipt = home / ".contextopus/hermes-integration.json"
    managed = json.loads(receipt.read_text())
    managed["skills"][".hermes/skills/w/SKILL.md"] = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(managed))
    integrate(plan("hermes", tmp_path, scope="user", root=workspace_root))
    assert target.read_bytes() == current
    target.write_text("user modified installed skill")
    with pytest.raises(IntegrationConflict, match="collision"):
        plan("hermes", tmp_path, scope="user", root=workspace_root)
    assert target.read_text() == "user modified installed skill"


def test_custom_canonical_skill_collision_has_no_config_write(tmp_path, workspace_root):
    target = tmp_path / ".hermes/skills/w/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("custom w skill")
    with pytest.raises(IntegrationConflict, match="collision"):
        plan("hermes", tmp_path, scope="user", root=workspace_root)
    assert not (tmp_path / ".hermes/config.yaml").exists()
    assert target.read_text() == "custom w skill"


@pytest.mark.parametrize("changed", ["config.yaml", "skills/w/SKILL.md", ".contextopus/hermes-integration.json"])
def test_stale_preview_checks_all_targets_before_writing(tmp_path, changed, workspace_root):
    preview = plan("hermes", tmp_path, scope="user", root=workspace_root)
    target = tmp_path / ".hermes" / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed after preview")
    with pytest.raises(IntegrationConflict, match="changed after preview"):
        integrate(preview)
    assert target.read_text() == "changed after preview"
    assert not (tmp_path / ".hermes/skills/w-create/SKILL.md").exists()


def test_environment_home_and_explicit_profile_keep_separate_receipts(tmp_path, monkeypatch, workspace_root):
    environment_home = tmp_path / "hermes-data"
    profile_home = environment_home / "profiles/research"
    monkeypatch.setenv("HERMES_HOME", str(environment_home))
    integrate(plan("hermes", tmp_path / "user", scope="user", root=workspace_root))
    integrate(plan("hermes", tmp_path / "user", scope="user", root=workspace_root, hermes_home=profile_home))
    for home in (environment_home, profile_home):
        assert (home / "config.yaml").is_file()
        assert (home / "skills/w/SKILL.md").is_file()
        assert (home / ".contextopus/hermes-integration.json").is_file()
    assert not integrate(plan("hermes", tmp_path / "user", scope="user", root=workspace_root))["written"]
    assert not integrate(plan("hermes", tmp_path / "user", scope="user", root=workspace_root, hermes_home=profile_home))["written"]








