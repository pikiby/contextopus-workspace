import json
from pathlib import Path
import pytest
from contextopus.integrations import IntegrationConflict, integrate, plan
from workspace_support import install_workspace




def test_collision_is_not_overwritten(tmp_path):
    root = tmp_path / "core"
    install_workspace(root)
    skill = tmp_path / ".agents/skills/contextopus-workspace/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("my unrelated skill")
    with pytest.raises(IntegrationConflict, match="collision"):
        plan("codex", tmp_path, root=root)
    assert skill.read_text() == "my unrelated skill"
    assert not (tmp_path / ".codex/config.toml").exists()






def test_canonical_resources_available_without_model(tmp_path):
    from contextopus.startup import Application

    with Application(tmp_path, discover=False) as app:
        assert app.query("procedure", {"name": "default"})["name"] == "modules"
        install_workspace(tmp_path, app)
        content = app.query("procedure", {"name": "workspace"})["content"]
        assert "allow_implicit_updates" in content
        assert app.query("procedure", {"name": "instructions"})["content"]
        assert not app.resources.providers._entries
        with pytest.raises(ValueError):
            app.query("procedure", {"name": "../outside"})


def test_owned_skill_upgrade_preserves_user_edits(tmp_path):
    import hashlib

    root = tmp_path / "core"
    install_workspace(root)
    preview = plan("codex", tmp_path, root=root)
    integrate(preview)
    skill = tmp_path / ".agents/skills/contextopus-workspace/SKILL.md"
    manifest = tmp_path / ".contextopus/codex-integration.json"
    saved = json.loads(manifest.read_text())
    # Simulate an older, unmodified installation recorded by the installer.
    skill.write_text("older generated version")
    saved["skills"][".agents/skills/contextopus-workspace/SKILL.md"] = hashlib.sha256(
        skill.read_bytes()
    ).hexdigest()
    manifest.write_text(json.dumps(saved))
    integrate(plan("codex", tmp_path, root=root))
    assert skill.read_text() != "older generated version"
    skill.write_text("user edited installed skill")
    with pytest.raises(IntegrationConflict, match="collision"):
        plan("codex", tmp_path, root=root)


def test_http_remote_facade_shared_owner(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    from contextopus.integrations.remote import RemoteApplication

    install_workspace(tmp_path / "data")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    code = "from contextopus.interfaces.mcp.server import run; import sys; run(sys.argv[1],port=int(sys.argv[2]))"
    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path / "data"), str(port)],
            stdout=log,
            stderr=log,
            env=env,
        )
        first = second = None
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path / "server.log").read_text())
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=0.2
                    ) as response:
                        health = json.loads(response.read())
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("HTTP MCP server did not start")
            assert isinstance(health["pid"], int) and health["pid"] > 0
            assert health["root"] == str((tmp_path / "data").resolve())
            # Windows venv python.exe redirects to a child interpreter PID.
            if os.name != "nt":
                assert health["pid"] == process.pid
            first = RemoteApplication(
                tmp_path / "data", endpoint=f"http://127.0.0.1:{port}/mcp"
            )
            second = RemoteApplication(
                tmp_path / "data", endpoint=f"http://127.0.0.1:{port}/mcp"
            )
            assert first.query("core.doctor")["model_loaded"] is False
            preview = first.services.preview_settings({"searchN": 7})
            second.services.apply_settings(preview["preview_revision"])
            assert first.services.settings()["values"]["searchN"] == 7
            assert (
                second.query("procedure", {"name": "workspace"})["name"] == "workspace"
            )
        finally:
            if first:
                first.close()
            if second:
                second.close()
            process.terminate()
            process.wait(timeout=10)






@pytest.mark.parametrize("crlf", [False, True])
@pytest.mark.parametrize("skill_name", ["pcw", "pcw-analyze", "pcw-audit", "pcw-create",
                                       "pcw-edit-instructions", "pcw-rework", "pcw-search", "pcw-upgrade"])
def test_known_legacy_skill_upgrade_and_modified_collision(tmp_path, crlf, skill_name):
    import hashlib
    from importlib.resources import files

    old = tmp_path / ".claude/skills" / skill_name / "SKILL.md"
    old.parent.mkdir(parents=True)
    # The fixture uses the exact shipped compatibility redirect, not a fuzzy
    # content match; historical shipped hashes remain accepted independently.
    source = (
        Path(__file__).resolve().parents[1]
        / "plugins/persistent-context-workspace/skills" / skill_name / "SKILL.md"
    )
    original = source.read_bytes().replace(b"\r\n", b"\n")
    old.write_bytes(original.replace(b"\n", b"\r\n") if crlf else original)
    integrate(
        plan("claude-code", tmp_path, scope="user", legacy_skills=old.parent.parent)
    )
    assert not old.exists()
    old.parent.mkdir(parents=True)
    modified = original + b"\nUser customization must remain.\n"
    old.write_bytes(modified)
    review = plan("claude-code", tmp_path, scope="user", legacy_skills=old.parent.parent)
    assert review["preserved"]
    integrate(review)
    assert old.read_bytes() == modified
    cache = tmp_path / ".codex/plugins/cache/old/old/0.14.0/skills"
    with pytest.raises(IntegrationConflict, match="caches"):
        plan("codex", tmp_path, legacy_skills=cache)










