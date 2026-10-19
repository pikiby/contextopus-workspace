"""Boundaries of the native Hermes verifier; native roundtrip is an explicit E2E check."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "hermes_verifier", Path(__file__).resolve().parents[1] / "scripts/verify_hermes_connection.py")
verifier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verifier)


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8765/mcp", "https://[::1]:8765/mcp"])
def test_loopback_endpoint_allowed(endpoint):
    assert verifier.validate_endpoint(endpoint) == endpoint


@pytest.mark.parametrize("endpoint", [
    "https://example.com:443/mcp", "http://user:secret@127.0.0.1:8765/mcp",
    "http://127.0.0.1:8765/mcp?token=secret", "file:///tmp/mcp", "http://localhost/mcp",
])
def test_external_or_credentialed_endpoint_refused(endpoint):
    with pytest.raises(ValueError):
        verifier.validate_endpoint(endpoint)


def test_child_does_not_inherit_credentials_or_loader_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted")
    home = tmp_path / "hermes-check"
    env = verifier.child_environment(home)
    assert not {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "PYTHONPATH", "HTTPS_PROXY"} & env.keys()
    assert env["HERMES_HOME"] == str(home)


def test_child_keeps_windows_home_resolution_without_inheriting_other_settings(tmp_path, monkeypatch):
    values = {"USERPROFILE": str(tmp_path / "user"), "LOCALAPPDATA": str(tmp_path / "user/AppData/Local"),
              "HOMEDRIVE": "C:", "HOMEPATH": r"\Users\Hermes"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CUSTOM_PROVIDER_SECRET", "private")
    env = verifier.child_environment(tmp_path / "profile")
    assert {name: env[name] for name in values} == values
    assert "CUSTOM_PROVIDER_SECRET" not in env


def test_isolated_child_uses_utf8_for_unicode_report_and_safe_environment(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.touch()
    args = SimpleNamespace(hermes_source=tmp_path, hermes_home=tmp_path,
                           hermes_executable=python, hermes_python=python,
                           expected_endpoint="http://127.0.0.1:8765/mcp",
                           expected_commit=verifier.AUDITED_COMMIT, timeout=10)
    unicode_value = "Контекст → 日本語"

    def run_probe(command, **kwargs):
        # Run a small real isolated Python child with the verifier's launch and
        # decoding settings; native Hermes/model/network access is unnecessary.
        assert "-I" in command and command[command.index("-X") + 1] == "utf8"
        assert kwargs["encoding"] == "utf-8"
        assert not {"PYTHONPATH", "OPENAI_API_KEY", "HTTPS_PROXY"} & kwargs["env"].keys()
        script = "import json; print(json.dumps({'verified': True, 'path': " + repr(unicode_value) + "}, ensure_ascii=False))"
        return subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", script], **kwargs)

    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted")
    # Patching this module's shared subprocess object would also patch the real
    # subprocess call above, so replace only its module reference.
    monkeypatch.setattr(verifier, "subprocess", SimpleNamespace(run=run_probe))
    assert verifier.verify(args)["path"] == unicode_value


def test_main_writes_utf8_stdout_and_stderr_under_ansi_stdio(tmp_path):
    unicode_value = "Контекст → 日本語"
    # Exercise the actual entrypoint with ANSI pipes. A missing source directory
    # stops before Hermes imports, subprocess launch, or network access.
    runner = ("import runpy, sys; namespace = runpy.run_path(sys.argv[1]); "
              "code = namespace['main'](sys.argv[2:]); sys.stderr.write(" + repr(unicode_value) + "); "
              "raise SystemExit(code)")
    env = verifier.child_environment(tmp_path)
    env["PYTHONIOENCODING"] = "cp1252"
    child = subprocess.run([sys.executable, "-X", "utf8=0", "-c", runner,
                            str(Path(verifier.__file__).resolve()),
                            "--hermes-executable", str(tmp_path / "hermes"),
                            "--hermes-python", str(tmp_path / "python"),
                            "--hermes-source", str(tmp_path / unicode_value),
                            "--hermes-home", str(tmp_path),
                            "--expected-endpoint", "http://127.0.0.1:8765/mcp"],
                           env=env, capture_output=True, check=False, timeout=10)
    assert child.returncode == 1
    report = json.loads(child.stdout.decode("utf-8"))
    assert report["verified"] is False and unicode_value in report["error"]
    assert child.stderr.decode("utf-8") == unicode_value


def test_relative_python_resolves_before_subprocess_changes_working_directory(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    python = tmp_path / "runtime/python"
    python.parent.mkdir()
    python.touch()
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(hermes_source=tmp_path, hermes_home=home,
                           hermes_executable=python, hermes_python=Path("runtime/python"),
                           expected_endpoint="http://127.0.0.1:8765/mcp",
                           expected_commit=verifier.AUDITED_COMMIT, timeout=10)

    def run_probe(command, **kwargs):
        assert command[0] == str(python.resolve())
        assert Path(command[0]).is_absolute() and Path(command[0]).is_file()
        assert kwargs["cwd"] == home
        return SimpleNamespace(stdout='{"verified": true}', returncode=0)

    monkeypatch.setattr(verifier, "subprocess", SimpleNamespace(run=run_probe))
    assert verifier.verify(args)["verified"] is True


def test_native_error_cannot_be_mistaken_for_workspace_guide():
    with pytest.raises(ValueError):
        verifier.decode_tool_result('{"error":"failed"}')
    with pytest.raises(ValueError):
        verifier.require_workspace_guide({"content": "Unrelated Workspace help"})
    content = "Workspace: workspace.startup and workspace.search"
    assert verifier.decode_tool_result(json.dumps({"result": json.dumps({"content": content})})) == {"content": content}
    assert len(verifier.require_workspace_guide({"name": "workspace", "content": content})) == 64


def test_tool_inventory_requires_all_four_exact_server_tools():
    names = [f"mcp__contextopus__{name}" for name in verifier.TOOLS]
    assert verifier.select_tools(names)["guide"] == "mcp__contextopus__guide"
    assert verifier.select_tools(names + ["mcp__contextopus__list_resources"])["read"] == "mcp__contextopus__read"
    with pytest.raises(ValueError):
        verifier.select_tools(names[:-1])
    with pytest.raises(ValueError):
        verifier.select_tools(names + ["mcp__other__guide"])


@pytest.mark.parametrize("output,code", [('{"verified": true}', 1), ('[]', 0), ('not JSON', 0)])
def test_subprocess_failure_or_invalid_protocol_never_becomes_success(tmp_path, monkeypatch, output, code):
    executable = tmp_path / "hermes"
    executable.touch()
    args = SimpleNamespace(hermes_source=tmp_path, hermes_home=tmp_path,
                           hermes_executable=executable, hermes_python=executable,
                           expected_endpoint="http://127.0.0.1:8765/mcp",
                           expected_commit=verifier.AUDITED_COMMIT, timeout=10)
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=output, returncode=code))
    with pytest.raises(RuntimeError):
        verifier.verify(args)
