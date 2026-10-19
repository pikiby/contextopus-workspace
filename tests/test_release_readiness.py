"""Release transition fixtures use actual previously tagged skill bytes."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile
import shutil

from workspace_support import install_workspace

import pytest

from contextopus.integrations import IntegrationConflict, integrate, plan

ROOT = Path(__file__).resolve().parents[1]
RELEASED = json.loads((ROOT / 'tests/fixtures/released-short-skills.json').read_text())
CASES = [(version, name, text) for version, skills in RELEASED.items() for name, text in skills.items()]


def test_canonical_release_versions_and_documentation_agree():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    base = json.loads((ROOT / "package/workspace/resources/base_context/manifest.json").read_text(encoding="utf-8"))
    workspace = json.loads((ROOT / 'module.json').read_text(encoding='utf-8'))
    assert base["version"] == workspace['version']
    installed = (ROOT / "package/workspace/resources/base_context/installed-version.md").read_text(encoding="utf-8")
    assert f"separately installed Workspace module {workspace['version']}." in installed
    assert workspace["version"] == version


@pytest.mark.parametrize('version,name,text', CASES, ids=[f'{version}-{name}' for version, name, _ in CASES])
@pytest.mark.parametrize('crlf', [False, True])
def test_previous_released_skills_upgrade_without_manifest_and_preserve_edits(tmp_path, version, name, text, crlf):
    root = tmp_path / "core"
    install_workspace(root)
    target = tmp_path / '.agents/skills' / name / 'SKILL.md'
    target.parent.mkdir(parents=True)
    previous = text.replace('\n', '\r\n') if crlf else text
    target.write_bytes(previous.encode())
    assert not (tmp_path / '.contextopus/codex-integration.json').exists()
    integrate(plan('codex', tmp_path, root=root))
    current_name = 'w-context' if name == 'w-find' else name
    current_target = tmp_path / '.agents/skills' / current_name / 'SKILL.md'
    assert current_target.read_bytes() == ((Path(__import__('contextopus.startup', fromlist=['Application']).__file__).parent / 'resources/skills' if current_name == 'm' else ROOT / 'package/workspace/skills') / current_name / 'SKILL.md').read_bytes()
    if current_name != name:
        assert not target.exists()
    edited = previous + '\nUser-authored local constraint.\n'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(edited.encode())
    if current_name == name:
        with pytest.raises(IntegrationConflict, match='collision'):
            plan('codex', tmp_path, root=root)
    else:
        preview = plan('codex', tmp_path, root=root)
        assert str(target) in [item['path'] for item in preview['preserved']]
        integrate(preview)
    assert target.read_bytes() == edited.encode()






