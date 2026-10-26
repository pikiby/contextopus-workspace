from __future__ import annotations

import json
from importlib import metadata
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "persistent-context-workspace"
SKILL = PLUGIN / "skills" / "pcw"
FIXTURE = ROOT / "tests" / "fixtures" / "valid-memory"
LAUNCHER = PLUGIN / "scripts" / "launch.py"
SHELL_LAUNCHER = PLUGIN / "scripts" / "launch.sh"


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise AssertionError(f"missing frontmatter: {path}")
    result = {}
    for line in match[1].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"invalid frontmatter: {path}")
        try:
            result[key] = json.loads(value.strip())
        except json.JSONDecodeError:
            result[key] = value.strip()
    return result


def server_tools() -> set[str]:
    return {"pcw_query", "pcw_execute"}


class PluginContractTests(unittest.TestCase):
    def test_package_versions_and_components_agree(self):
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
        portable = json.loads((PLUGIN / "plugin.json").read_text())
        package = tomllib.loads((PLUGIN / "pyproject.toml").read_text())
        self.assertEqual(PLUGIN.name, manifest["name"])
        self.assertEqual(manifest["name"], package["project"]["name"])
        self.assertEqual(manifest["version"], package["project"]["version"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual(manifest["name"], portable["name"])
        self.assertEqual(manifest["version"], portable["version"])
        self.assertEqual(manifest["interface"], portable["extensions"]["com.openai"]["interface"])
        self.assertEqual(manifest["skills"], portable["extensions"]["com.openai"]["skills"])
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("mcpServers", portable["extensions"]["com.openai"])
        self.assertFalse((PLUGIN / ".mcp.json").exists())
        current = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual("contextopus-workspace", current["project"]["name"])
        self.assertTrue((PLUGIN / "pcw_runtime" / "__main__.py").is_file())
        self.assertTrue(LAUNCHER.is_file())
        self.assertTrue(SHELL_LAUNCHER.is_file())

    def test_explicit_skills_are_packaged(self):
        expected = {PLUGIN / "skills" / name / "SKILL.md"
                    for name in ("pcw", "pcw-edit-instructions", "pcw-create", "pcw-search", "pcw-upgrade", "pcw-audit", "pcw-rework", "pcw-analyze")}
        self.assertEqual(expected, set((PLUGIN / "skills").glob("*/SKILL.md")))
        for entrypoint in expected:
            ui = (entrypoint.parent / "agents" / "openai.yaml").read_text()
            implicit = "true" if entrypoint.parent.name in {"pcw", "pcw-search", "pcw-analyze"} else "false"
            self.assertIn("allow_implicit_invocation: " + implicit, ui)
            self.assertEqual(entrypoint.parent.name, frontmatter(entrypoint)["name"])
        metadata = frontmatter(SKILL / "SKILL.md")
        self.assertEqual("pcw", metadata["name"])
        self.assertTrue(metadata["description"].strip())
        ui = (SKILL / "agents" / "openai.yaml").read_text()
        self.assertIn("allow_implicit_invocation: true", ui)
        self.assertIn("$pcw", ui)
        prompts = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["interface"]["defaultPrompt"]
        self.assertTrue(all("$pcw" in prompt and "$pcw-run" not in prompt for prompt in prompts))

    def test_all_skill_references_resolve_inside_plugin(self):
        reached = {SKILL / "SKILL.md"}
        pending = list(reached)
        while pending:
            document = pending.pop()
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
                if "://" in target:
                    continue
                resolved = (document.parent / target.split("#", 1)[0]).resolve()
                self.assertTrue(resolved.is_relative_to(PLUGIN.resolve()), target)
                self.assertTrue(resolved.is_file(), target)
                if resolved not in reached:
                    reached.add(resolved)
                    pending.append(resolved)
        # Compatibility references independently route to canonical procedures.
        for reference in (SKILL / "references").glob("*.md"):
            self.assertIn("guide(procedure=", reference.read_text())


    def test_documented_tools_exist_in_runtime(self):
        documented = set()
        for path in (PLUGIN / "skills").rglob("*.md"):
            # The package path pcw_runtime/... is not a tool reference.
            documented.update(re.findall(r"\bpcw_[a-z_]+\b(?!/)", path.read_text()))
        self.assertEqual(set(), documented - server_tools())
        procedures = [Path(__import__("contextopus.startup", fromlist=["Application"]).__file__).parent / "resources/procedures",
                      ROOT / "package/workspace/resources/procedures"]
        for path in (PLUGIN / "skills").rglob("*.md"):
            text = path.read_text()
            for name in re.findall(r'guide\(procedure="([^"]+)"\)', text):
                self.assertTrue(any((directory / (name + ".md")).is_file() for directory in procedures), name)
        self.assertIn("guide()", (SKILL / "SKILL.md").read_text())

    def test_packaged_docs_have_no_legacy_entrypoints_or_scaffolds(self):
        paths = [ROOT / "README.md", *(PLUGIN / "skills").rglob("*.md")]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("$pcw-run", text, str(path))
            self.assertNotIn("[TODO:", text, str(path))
            self.assertIsNone(re.search(r"[\u0400-\u04FF]", text), str(path))


legacy_posix_launcher = unittest.skipUnless(
    os.name == "posix" and Path("/bin/sh").is_file(),
    "Legacy 0.14 POSIX shell launcher; Contextopus HTTP transport is tested on every platform",
)


class LauncherTests(unittest.TestCase):
    @legacy_posix_launcher
    def test_missing_interpreter_fails_without_creating_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "not-installed" / "bin" / "python"
            environment = {**os.environ, "PCW_PYTHON": str(missing)}
            result = subprocess.run(
                ["/bin/sh", str(SHELL_LAUNCHER)], env=environment,
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn(str(missing), result.stderr)
            self.assertEqual("", result.stdout)
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_portable_mcp_declares_a_host_resolved_script_argument(self):
        document = json.loads((PLUGIN / "mcp.json").read_text())
        self.assertEqual("https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", document["$schema"])
        config = document["mcpServers"]["pcw"]
        self.assertEqual("stdio", config["type"])
        self.assertEqual("sh", config["command"])
        self.assertEqual(["${PLUGIN_ROOT}/scripts/launch.sh"], config["args"])
        self.assertNotIn("env", config)

    @legacy_posix_launcher
    def test_missing_dependencies_fail_with_installation_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated = Path(directory) / "isolated"
            venv.EnvBuilder(with_pip=False).create(isolated)
            environment = {**os.environ, "PCW_PYTHON": str(isolated / "bin" / "python")}
            environment["PCW_DEPENDENCIES"] = str(Path(directory) / "packages")
            result = subprocess.run(
                ["/bin/sh", str(SHELL_LAUNCHER)], env=environment,
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(3, result.returncode)
            self.assertIn("mcp", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertFalse((Path(directory) / "packages").exists())

    @legacy_posix_launcher
    def test_launcher_stdio_initializes_and_lists_tools(self):
        # The retained source plugin now contains compatibility imports rather
        # than a second implementation. A copied shim needs the standalone
        # distribution in the selected interpreter, as installed by uv sync.
        try:
            installed = metadata.version("contextopus-core")
        except metadata.PackageNotFoundError:
            self.fail("Install the standalone project in the test interpreter first (uv sync --locked --extra test); copied compatibility shims require contextopus-core")
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        requirement = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"][0]
        self.assertIn(Version(installed), SpecifierSet(requirement.removeprefix("contextopus-core")))
        check = subprocess.run(
            ["/bin/sh", str(SHELL_LAUNCHER), "--check"],
            env={**os.environ, "PCW_PYTHON": sys.executable},
            text=True, capture_output=True, timeout=30,
        )
        if check.returncode == 3:
            self.skipTest("Selected runtime dependencies are unavailable; real stdio integration was not run")
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)
        imports = subprocess.run(
            ["/bin/sh", str(SHELL_LAUNCHER), "--check-imports"],
            env={**os.environ, "PCW_PYTHON": sys.executable},
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(0, imports.returncode, imports.stdout + imports.stderr)
        for explicit in (False, True):
            with self.subTest(explicit_python=explicit), tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as cache:
                environment = {
                    **os.environ,
                    "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
                    "PCW_DEPENDENCIES": str(Path(cache) / "empty-private-dependencies"),
                    "HF_HOME": str(Path(cache) / "empty-huggingface-cache"),
                    "XDG_CACHE_HOME": str(Path(cache) / "empty-xdg-cache"),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
                environment.pop("PLUGIN_ROOT", None)
                environment.pop("CLAUDE_PLUGIN_ROOT", None)
                if explicit:
                    environment["PCW_PYTHON"] = sys.executable
                else:
                    environment.pop("PCW_PYTHON", None)
                active_plugin = Path(cache) / "installed plugin with spaces"
                shutil.copytree(PLUGIN, active_plugin, ignore=shutil.ignore_patterns("__pycache__", ".venv", "*.egg-info", "build"))
                # The independent Workspace package is explicitly supplied to
                # this copied legacy-launcher fixture, alongside Core's runtime.
                shutil.copytree(ROOT / 'package', active_plugin / 'module-package',
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                entry = active_plugin / 'scripts/launch.py'
                copied = entry.read_text()
                copied = copied.replace('    try:\n        from pcw_runtime import server',
                    '    sys.path.insert(0, str(plugin_root / "module-package"))\n    try:\n        from pcw_runtime import server', 1)
                entry.write_text(copied)
                # The launcher deliberately ignores PYTHONPATH (-E). Inject the
                # test provider through the temporary launcher copy instead; keep
                # its readiness checks and real stdio/MCP entrypoint intact.
                shutil.copyfile(ROOT / "tests/fixtures/offline_stdio_provider.py",
                                active_plugin / "scripts/offline_stdio_provider.py")
                entry = active_plugin / "scripts/launch.py"
                entry_text = entry.read_text()
                original_run = "    run_mcp()\n"
                self.assertEqual(1, entry_text.count(original_run))
                entry.write_text(entry_text.replace(original_run,
                    '    import runpy\n'
                    '    runpy.run_path(str(Path(__file__).with_name("offline_stdio_provider.py")))\n'
                    + original_run))
                # The descriptor requests add many log lines. An unread stderr
                # pipe can fill and block the server while stdout is awaited.
                error_log = tempfile.TemporaryFile(mode="w+")
                process = subprocess.Popen(
                    ["/bin/sh", str(active_plugin / "scripts" / "launch.sh")], env=environment, cwd=directory,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log,
                    text=True, bufsize=1,
                )

                def send(message):
                    process.stdin.write(json.dumps(message) + "\n")
                    process.stdin.flush()

                def receive(request_id):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
                        self.assertTrue(ready, "stdio response timed out")
                        line = process.stdout.readline()
                        if not line:
                            error_log.flush()
                            error_log.seek(0)
                            self.fail("stdio closed before returning a response; stderr: " + error_log.read()[-4000:])
                        message = json.loads(line)
                        if message.get("id") == request_id:
                            self.assertNotIn("error", message)
                            return message["result"]
                    self.fail("stdio response timed out")

                try:
                    send({
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "pcw-contract-test", "version": "1"},
                        },
                    })
                    initialized = receive(1)
                    self.assertIn("tools", initialized["capabilities"])
                    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                    tools = receive(2)["tools"]
                    self.assertEqual(server_tools(), {tool["name"] for tool in tools})
                    schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
                    self.assertEqual(["operation"], schemas["pcw_query"]["required"])
                    self.assertTrue({"operation", "work_root", "workspace"} <= set(schemas["pcw_execute"]["required"]))
                    self.assertEqual([], list(Path(directory).iterdir()))
                    request_id = 2
                    cycle_id = None

                    def raw_call(tool, arguments):
                        nonlocal request_id
                        request_id += 1
                        send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
                            "name": tool, "arguments": arguments}})
                        response = receive(request_id)
                        self.assertFalse(response.get("isError"), response)
                        return json.loads(response["content"][0]["text"])

                    catalog = raw_call("pcw_query", {"operation": "describe"})["operations"]
                    from workspace.operations import _DESCRIPTIONS
                    self.assertEqual(set(_DESCRIPTIONS), set().union(*map(set, catalog.values())))
                    self.assertIn('settings_read', catalog['pcw_query'])
                    self.assertTrue({'settings_preview', 'settings_apply'} <= set(catalog['pcw_execute']))
                    operations = {name: raw_call("pcw_query", {
                        "operation": "describe", "arguments": {"operation": name}})["arguments_schema"]
                        for names in catalog.values() for name in names}
                    self.assertEqual([], list(Path(directory).iterdir()))
                    self.assertIn("expected_revision", operations["proposal_apply"]["required"])
                    self.assertIn("confirmation", operations["proposal_apply"]["properties"])
                    self.assertNotIn("confirmation", operations["proposal_apply"]["required"])
                    self.assertIn("confirmation", operations["finish"]["properties"])
                    self.assertIn("expected_revision", operations["finish"]["properties"])
                    self.assertIn("no_changes_reason", operations["proposal_prepare"]["properties"])
                    self.assertEqual({"request_id", "purpose", "restart_cycle_id", "invocation", "task_id", "user_request"},
                                     set(operations["startup"]["properties"]))
                    self.assertIn("user_request", operations["instructions_prepare"]["required"])
                    self.assertNotIn("confirmation", operations["instructions_apply"]["required"])
                    for name in ("search", "read", "read_many", "list", "instructions_read", "project_read"):
                        self.assertNotIn("cycle_id", operations[name].get("required", []))
                    for name in ("proposal_prepare", "proposal_apply", "tmp_write", "instructions_apply"):
                        self.assertIn("cycle_id", operations[name]["required"])
                    self.assertIn("chunking", operations["switch_embeddings"]["properties"])
                    self.assertTrue({"since", "until", "workspaces", "instruction_revision", "cursor"}
                                    <= operations["analytics_report"]["properties"].keys())
                    self.assertIn("history_index", catalog["pcw_execute"])
                    self.assertIn("history_lineage", catalog["pcw_query"])

                    def call(name, **arguments):
                        tool = "pcw_query" if name in catalog["pcw_query"] else "pcw_execute"
                        if cycle_id and "cycle_id" in operations[name]["properties"]:
                            arguments.setdefault("cycle_id", cycle_id)
                        result = raw_call(tool, {"operation": name, "work_root": directory, "workspace": "protocol",
                                                 "arguments": arguments})
                        self.assertEqual("recorded", result.pop("analytics")["status"])
                        return result

                    created = call("workspace_create", request_id="protocol-create", user_request="Create the protocol fixture",
                        # Protocol/approval coverage is strictly offline; the real
                        # model/tokenizer is replaced by the explicit test provider.
                        instructions_content=(ROOT / "package/workspace/default_instructions.md").read_text(),
                        settings={'allow_implicit_updates': False, 'knowledge_approval_required': False,
                                  'instructions_approval_required': True, 'cleanup_approval_required': True,
                                  'git_history_enabled': True,
                                  'material_max_tokens': None, 'create_required_sources': False},
                        project_description='Purpose and boundaries of this isolated stdio protocol fixture.',
                        project_content="# Protocol fixture\nA temporary test project.\n")
                    self.assertEqual("created", created["status"], created)
                    self.assertEqual([], call("search", query="empty", types=["knowledge"])["results"])
                    opened = call("startup", request_id="protocol-invocation")
                    cycle_id = opened["cycle_id"]
                    self.assertEqual(1, opened["materials"])
                    self.assertEqual("closed", call("finish")["status"])
                    opened = call("startup", request_id="protocol-materials", user_request="Save this fixture")
                    cycle_id = opened["cycle_id"]
                    pending = call("tmp_write", content="Protocol fixture content")
                    self.assertEqual("blocked", call("finish")["status"])
                    self.assertEqual(1, call("status")["materials"])
                    proposal = call("proposal_prepare", expected_task_revision=opened["task_revision"], changes=[{"key": "fixture", "content": "Protocol fixture content", "description": "Persisted fact used by the isolated stdio contract fixture."}])
                    self.assertEqual("Protocol fixture content", proposal["review_changes"][0]["body_changes"][0]["after"])
                    review = call("proposal_read", proposal_id=proposal["proposal_id"])
                    self.assertIn("Protocol fixture content", review["review"])
                    self.assertEqual(proposal["review_changes"], review["review_changes"])
                    applied = call("proposal_apply", proposal_id=proposal["proposal_id"],
                                   expected_revision=proposal["revision"], confirmation="Apply the displayed fixture")
                    item = applied["materials"][0]
                    self.assertRegex(item["id"], r"^[a-f0-9]{32}\.md$")
                    self.assertEqual("Protocol fixture content", call("read", material_id=item["id"])["content"])
                    second = call("proposal_prepare", expected_task_revision=opened["task_revision"], changes=[{"key": "related", "content": "Related fact", "description": "A related fact for testing links over stdio.", "links": [item["id"]]}])
                    other = call("proposal_apply", proposal_id=second["proposal_id"], expected_revision=second["revision"],
                                 confirmation="Apply the displayed related fact")["materials"][0]
                    self.assertEqual([item["id"]], call("read", material_id=other["id"])["links"])
                    observed = call("analytics_query", tool="read", limit=1)
                    replay = call("analytics_reference_read", event_id=observed["events"][0]["id"])
                    self.assertEqual("Protocol fixture content", replay["text"])
                    report = call("analytics_report", tool="read", max_events=1)
                    self.assertEqual(1, report["analyzed_events"])
                    self.assertTrue(report["partial"])
                    self.assertEqual(["protocol"], report["scope"]["workspaces"])
                    instructions = call("instructions_read")
                    self.assertEqual(opened["instructions"], instructions)
                    edit = call("instructions_prepare", content="# Project rules\nTest-specific instructions.\n",
                                expected_revision=instructions["revision"], user_request="Replace the fixture instructions")
                    self.assertEqual(instructions, call("instructions_read"))
                    self.assertEqual("applied", call("instructions_apply", proposal_id=edit["proposal_id"],
                                     expected_revision=edit["revision"], confirmation="Apply the shown rules")["status"])
                    self.assertEqual("# Project rules\nTest-specific instructions.\n", call("instructions_read")["content"])
                    self.assertEqual("Protocol fixture content", call("tmp_read", material_id=pending["id"])["content"])
                    shutil.rmtree(Path(directory) / ".pcw/.rag")
                    self.assertEqual("recovery_required", call("status")["status"])
                    self.assertEqual("recovered", call("recover_workspace")["status"])
                    self.assertEqual("Protocol fixture content", call("read", material_id=item["id"])["content"])
                    closed = call("finish", cancel=True, confirmation="Cancel the isolated fixture and delete its drafts",
                                  expected_revision=call("status")["cleanup_revision"])
                    self.assertEqual("cancelled", closed["status"])
                    self.assertFalse((Path(opened["workspace"]) / ".tmp" / cycle_id).exists())
                finally:
                    if process.stdin:
                        process.stdin.close()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    process.stdout.close()
                    error_log.close()


class MemoryFixtureTests(unittest.TestCase):
    def test_filename_ids_are_unique_and_links_are_filename_ids(self):
        paths = list((FIXTURE / "knowledge").glob("*.md"))
        records = {path.name: (path, frontmatter(path)) for path in paths}
        self.assertEqual(len(paths), len(records))
        for material_id, (path, metadata) in records.items():
            self.assertIsInstance(metadata["links"], list)
            self.assertEqual(len(metadata["links"]), len(set(metadata["links"])))
            self.assertNotIn(material_id, metadata["links"])
            self.assertTrue(set(metadata["links"]) <= records.keys())
            self.assertNotIn("id", metadata)
            self.assertEqual(material_id, path.name)

    def test_instructions_template_is_packaged_as_one_resource(self):
        package = tomllib.loads((PLUGIN / "pyproject.toml").read_text())
        self.assertIn("default_instructions.md", package["tool"]["setuptools"]["package-data"]["pcw_runtime"])
        default = PLUGIN / "pcw_runtime" / "default_instructions.md"
        self.assertTrue(default.read_text().strip())
        fixture = (FIXTURE / "INSTRUCTIONS.md").read_text()
        self.assertIn("instructions-contract.md", fixture)
        self.assertFalse((FIXTURE / "current.md").exists())


if __name__ == "__main__":
    unittest.main()
