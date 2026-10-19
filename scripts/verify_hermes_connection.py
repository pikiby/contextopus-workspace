"""Verify the installed Hermes MCP client without a model or knowledge writes.

Run with an explicit native home containing the Contextopus integration. The
Hermes-owned interpreter and source are explicit because Hermes has no public
machine-readable CLI command that performs this check. Example::

    python scripts/verify_hermes_connection.py \
      --hermes-executable /path/to/hermes-agent/.hermes/bin/hermes \
      --hermes-python /path/to/Hermes-owned/python \
      --hermes-source /path/to/hermes-agent --hermes-home /tmp/hermes-profile \
      --expected-endpoint http://127.0.0.1:8765/mcp

The child uses Hermes's native YAML loader, MCP registration, tool dispatch and
skill resolver. Reload exercises native MCP shutdown/re-registration, not the
interactive confirmation or plugin rediscovery in /reload-mcp. Only guide() is
called; read/write/finish are checked in the live tool inventory. Native cache
and runtime-lease writes may occur in the selected Hermes home/runtime.
"""

import argparse
from contextlib import redirect_stdout
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


AUDITED_COMMIT = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"
SKILLS = ("w", "w-search", "w-create", "w-settings", "w-context", "w-analyze", "m")
TOOLS = ("guide", "read", "write", "finish")
UTILITIES = ("list_resources", "read_resource", "list_prompts", "get_prompt")
LIMITS = [
    "No model or inference API was called.",
    "Only guide() was called; read, write and finish were discovered but not executed.",
    "Reload covers native MCP shutdown/registration, not CLI confirmation or plugin rediscovery.",
    "Skill resolution disables preprocessing and refuses declared executable dependencies.",
    "This does not verify a model follows the Workspace procedure.",
]


class ProbeFailure(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def validate_endpoint(value):
    """Keep this non-model verifier confined to the named local HTTP endpoint."""
    parts = urlsplit(value)
    try:
        local = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname).is_loopback
        port = parts.port
    except (ValueError, TypeError):
        local, port = False, None
    if (parts.scheme not in {"http", "https"} or not local or port is None
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment):
        raise ValueError("Expected an explicit loopback HTTP(S) endpoint without credentials, query or fragment")
    return value


def child_environment(home):
    """Do not inherit model credentials, loader injection or proxy configuration."""
    names = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR",
             "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA")
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update(HERMES_HOME=str(home), HERMES_PROFILE="default", PYTHONDONTWRITEBYTECODE="1")
    return env


def decode_tool_result(result):
    """Unwrap the real native handler envelope without accepting an error as data."""
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or result.get("error") or result.get("isError"):
        raise ValueError("Native Hermes tool dispatch returned an error")
    value = result.get("structuredContent", result.get("result", result))
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or value.get("error") or value.get("isError"):
        raise ValueError("Contextopus guide did not return an object")
    return value


def require_workspace_guide(value):
    content = value.get("content")
    if (value.get("name") != "workspace" or not isinstance(content, str)
            or "workspace.startup" not in content or "workspace.search" not in content):
        raise ProbeFailure("guide() did not return the ordinary Workspace procedure", {"guide_result": value})
    return hashlib.sha256(content.encode()).hexdigest()


def select_tools(names):
    expected = {name: f"mcp__contextopus__{name}" for name in TOOLS}
    allowed = set(expected.values()) | {f"mcp__contextopus__{name}" for name in UTILITIES}
    if not set(expected.values()).issubset(names) or not set(names).issubset(allowed):
        raise ValueError(f"Native Hermes Contextopus inventory differs from four core tools plus native utilities: {sorted(names)}")
    return expected


def _native_probe(args):
    source = args.hermes_source.resolve()
    home = args.hermes_home.resolve()
    if source not in args.hermes_executable.resolve().parents:
        raise ValueError("Choose the install-local Hermes executable under --hermes-source")
    sys.path.insert(0, str(source))
    from pm.environments import activate_dependencies, owning_home_root
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    # Borrow the installation's committed dependencies without asking the CLI
    # startup repair path to install another environment or rebuild products.
    dependency_home = owning_home_root(source) or home
    token = set_hermes_home_override(dependency_home)
    try:
        activate_dependencies(source)
    finally:
        reset_hermes_home_override(token)
    from hermes_cli._launchers import _launcher_python

    bound_python = _launcher_python(args.hermes_executable)
    if bound_python is None or bound_python.resolve() != Path(sys.executable).resolve():
        raise ValueError("Explicit Python does not match the installed Hermes executable's interpreter")
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, encoding="utf-8", timeout=10,
    ).strip()
    if commit != args.expected_commit:
        raise ValueError("Installed Hermes commit does not match --expected-commit")
    changed = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True, encoding="utf-8", timeout=10,
    ).strip()
    if changed:
        raise ValueError("Installed Hermes has tracked source changes; verify a clean pinned installation")
    from hermes_constants import get_hermes_home
    from hermes_cli.config import get_config_path, load_config
    from hermes_cli import __version__
    from tools import mcp_tool, skills_tool
    from tools.mcp_tool_common import mcp_server_enabled
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.registry import registry

    if get_hermes_home().resolve() != home:
        raise ValueError("Native Hermes resolved an unexpected home")
    if Path(mcp_tool.__file__).resolve().parent.parent != source:
        raise ValueError("MCP client did not import from the explicit Hermes source")

    def server_config():
        config = load_config()
        servers = config.get("mcp_servers")
        entry = servers.get("contextopus") if isinstance(servers, dict) else None
        if not isinstance(entry, dict) or not mcp_server_enabled(entry):
            raise ValueError("Native Hermes config has no enabled contextopus server")
        if entry.get("url") != args.expected_endpoint or entry.get("command"):
            raise ValueError("Native Hermes Contextopus endpoint differs from --expected-endpoint")
        # No remote authentication, elicitation, or server-initiated model calls.
        if entry.get("headers") or entry.get("oauth") or entry.get("identity"):
            raise ValueError("This verifier only supports unauthenticated local Contextopus")
        if entry.get("lazy"):
            raise ValueError("This verifier requires live discovery (lazy must be false or absent)")
        return {"contextopus": dict(entry, sampling={"enabled": False})}

    skills = []
    roots, _ = skills_tool._skill_search_dirs()
    for name in SKILLS:
        error, skill_dir, skill_md = skills_tool._locate_skill(name, None, roots)
        expected = home / "skills" / name
        if error or skill_dir is None or skill_dir.resolve() != expected.resolve():
            raise ValueError(f"Native Hermes did not resolve the profile-owned skill {name}")
        content = skills_tool._read_skill_text(skill_md)
        frontmatter = skills_tool._safe_frontmatter(content=content)
        if (frontmatter.get("deps") or frontmatter.get("required_credential_files")
                or skills_tool._get_required_environment_variables(frontmatter)):
            raise ValueError(f"Skill {name} requires dependency/credential setup; verification refuses activation")
        viewed = json.loads(skills_tool.skill_view(name, preprocess=False))
        if not viewed.get("success") or viewed.get("name") != name or viewed.get("content") != content:
            raise ValueError(f"Native Hermes skill_view failed for {name}")
        skills.append({"name": name, "path": str(skill_md), "sha256": hashlib.sha256(content.encode()).hexdigest()})

    passes = []
    previous = None
    try:
        for phase in ("connect", "reload"):
            if phase == "reload":
                shutdown_mcp_servers()
            register_mcp_servers(server_config())
            names = registry.get_tool_names_for_toolset("mcp-contextopus")
            selected = select_tools(names)
            with mcp_tool._lock:
                connections = [server for server in mcp_tool._servers.values() if server.name == "contextopus"]
            if len(connections) != 1 or (previous is not None and connections[0] is previous):
                raise ValueError("Native MCP reload did not create one fresh Contextopus connection")
            previous = connections[0]
            value = decode_tool_result(registry.dispatch(selected["guide"], {}))
            digest = require_workspace_guide(value)
            passes.append({"phase": phase, "tools": sorted(names), "canonical_tools": selected,
                           "generated_utility_tools": sorted(set(names) - set(selected.values())), "guide_arguments": {},
                           "guide_result": value, "workspace_procedure_sha256": digest})
    finally:
        shutdown_mcp_servers()
    return {"verified": True, "client": "Hermes", "hermes_version": __version__,
            "hermes_commit": commit, "hermes_source_dirty": False,
            "hermes_executable": str(args.hermes_executable),
            "hermes_python": sys.executable, "hermes_source": str(source), "hermes_home": str(home),
            "dependency_home": str(dependency_home),
            "config_path": str(get_config_path()), "endpoint": args.expected_endpoint,
            "skills": skills, "passes": passes, "limits": LIMITS}


def verify(args):
    validate_endpoint(args.expected_endpoint)
    for path in (args.hermes_source, args.hermes_home):
        if not path.is_dir():
            raise ValueError(f"Required existing directory is missing: {path}")
    for path in (args.hermes_executable, args.hermes_python):
        if not path.is_file():
            raise ValueError(f"Required installed executable is missing: {path}")
    command = [str(args.hermes_python.resolve()), "-I", "-X", "utf8", "-B", str(Path(__file__).resolve()), "--native-probe",
               "--hermes-executable", str(args.hermes_executable.resolve()),
               "--hermes-python", str(args.hermes_python.resolve()),
               "--hermes-source", str(args.hermes_source.resolve()),
               "--hermes-home", str(args.hermes_home.resolve()),
               "--expected-endpoint", args.expected_endpoint, "--expected-commit", args.expected_commit]
    child = subprocess.run(command, cwd=args.hermes_home, env=child_environment(args.hermes_home.resolve()),
                           capture_output=True, text=True, encoding="utf-8", timeout=args.timeout, check=False)
    try:
        report = json.loads(child.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Native Hermes verifier did not produce a JSON report") from exc
    if not isinstance(report, dict):
        raise RuntimeError("Native Hermes verifier did not produce a JSON object")
    if child.returncode and report.get("verified"):
        raise RuntimeError("Native Hermes verifier failed after reporting success")
    return report


def main(argv=None):
    # Pipes and redirected reports use UTF-8 even under a Windows ANSI locale.
    # -X utf8 also configures the isolated child before importing Hermes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-executable", type=Path, required=True)
    parser.add_argument("--hermes-python", type=Path, required=True)
    parser.add_argument("--hermes-source", type=Path, required=True)
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--expected-endpoint", required=True)
    parser.add_argument("--expected-commit", default=AUDITED_COMMIT)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--native-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.native_probe:
            # Native discovery logs never contaminate the machine-readable report.
            with redirect_stdout(sys.stderr):
                report = _native_probe(args)
        else:
            report = verify(args)
    except (Exception, KeyboardInterrupt) as exc:
        # Exception strings from HTTP clients can include config/header values.
        report = {"verified": False, "client": "Hermes", "error_type": type(exc).__name__,
                  "error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Native verification failed",
                  "limits": LIMITS}
        if isinstance(exc, ModuleNotFoundError):
            report["missing_module"] = exc.name
        if isinstance(exc, ProbeFailure):
            report["evidence"] = exc.evidence
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
