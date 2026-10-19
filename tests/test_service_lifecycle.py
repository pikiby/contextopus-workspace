import errno
import json
import plistlib
from pathlib import Path
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET

import pytest

from contextopus.integrations import autostart, service
from contextopus.integrations.native import plan, integrate, IntegrationConflict, _digest
from contextopus.interfaces.tui.backend import Backend
from contextopus.interfaces.tui.render import action_names










@pytest.mark.parametrize("managed_content", [b"Old managed search", b"Old managed\r\nsearch\r\n"])
def test_upgrade_removes_only_managed_retired_skills_and_rejects_stale_plan(tmp_path, managed_content):
    from workspace_support import install_workspace
    memory = tmp_path / 'memory'
    install_workspace(memory)
    root = tmp_path / ".agents/skills"
    old = root / "ws/SKILL.md"
    old.parent.mkdir(parents=True)
    old.write_bytes(managed_content)
    modified = root / "wr/SKILL.md"
    modified.parent.mkdir()
    modified.write_text("My custom rework")
    metadata = tmp_path / ".contextopus/codex-integration.json"
    metadata.parent.mkdir()
    metadata.write_text(json.dumps({"skills": {old.relative_to(tmp_path).as_posix(): _digest(old.read_bytes())}}))
    preview = plan("codex", tmp_path, root=memory)
    assert preview["removals"] == [{"path": str(old), "expected": _digest(old.read_bytes())}]
    assert preview["preserved"][0]["path"] == str(modified)
    old.write_text("Changed since preview")
    with pytest.raises(IntegrationConflict):
        integrate(preview)
    assert not (tmp_path / ".codex/config.toml").exists()
    old.write_bytes(managed_content)
    result = integrate(preview)
    assert not old.exists() and modified.read_text() == "My custom rework"
    assert set(path.name for path in root.iterdir()) == {"w", "w-search", "w-create", "w-settings", "w-context", "w-analyze", "m", "wr"}
    assert result["removed"] == [str(old)]
