"""Bootstrap/readiness tests: stdlib fixtures, no installed ML stack required."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
import venv


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "persistent-context-workspace"
SCRIPTS = PLUGIN / "scripts"


legacy_posix_launcher = unittest.skipUnless(
    os.name == "posix" and Path("/bin/sh").is_file(),
    "Legacy 0.14 POSIX shell launcher fixture; standalone Core/MCP coverage runs separately",
)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pcw startup ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.packages = self.root / "private packages"
        self.project = self.root / "source project"
        self.project.mkdir()
        self.plugin = self.root / "cached plugin with spaces"
        (self.plugin / "scripts").mkdir(parents=True)
        for filename in ("launch.sh", "launch.py", "check_runtime.py"):
            shutil.copyfile(SCRIPTS / filename, self.plugin / "scripts" / filename)
        runtime = self.plugin / "pcw_runtime"
        runtime.mkdir()
        (runtime / "__init__.py").write_text("")
        shutil.copyfile(PLUGIN / "pcw_runtime" / "__main__.py", runtime / "__main__.py")
        (runtime / "server.py").write_text(
            "import json, os, sys\n"
            "class Server:\n"
            "    def run(self, transport):\n"
            "        for line in sys.stdin:\n"
            "            print(json.dumps({'origin': __file__, 'pid': os.getpid(), 'input': line.strip(), 'transport': transport}), flush=True)\n"
            "mcp = Server()\n"
        )
        # -S isolates fixtures from whichever packages the test runner uses.
        self.python = self.root / "selected python"
        self.python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -S "$@"\n')
        self.python.chmod(0o755)
        self.environment = {
            **os.environ,
            "PCW_PYTHON": str(self.python),
            "PCW_DEPENDENCIES": str(self.packages),
            "XDG_DATA_HOME": str(self.root / "data"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        self.environment.pop("PLUGIN_ROOT", None)
        self.environment.pop("CLAUDE_PLUGIN_ROOT", None)

    def launch(self, *arguments, environment=None, input=None):
        environment = environment if environment is not None else self.environment
        command = ["/bin/sh", str(self.plugin / "scripts" / "launch.sh"), *arguments]
        return subprocess.run(command, env=environment, cwd=self.project, input=input,
                              text=True, capture_output=True, timeout=15)

    def distribution(self, name, module, version, *, body="raise RuntimeError('UNEXPECTED_DEPENDENCY_IMPORT')\n"):
        self.packages.mkdir(exist_ok=True)
        metadata = self.packages / (name.replace("-", "_") + "-" + version + ".dist-info")
        metadata.mkdir(exist_ok=True)
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n")
        package = self.packages / module
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text(body)

    def base(self, *, mcp_version="1.10.0", body="raise RuntimeError('UNEXPECTED_DEPENDENCY_IMPORT')\n"):
        for name, module, version in (
            ("lancedb", "lancedb", "0.20.0"),
            ("mcp", "mcp", mcp_version),
            ("PyYAML", "yaml", "6.0"),
            ("pydantic", "pydantic", "2.11.0"),
            ("pyarrow", "pyarrow", "19.0.1"),
        ):
            self.distribution(name, module, version, body=body)

    def local(self, *, body="raise RuntimeError('UNEXPECTED_LOCAL_IMPORT')\n"):
        for name, module, version in (
            ("sentence-transformers", "sentence_transformers", "5.0.0"),
            ("torch", "torch", "2.0.0"),
            ("transformers", "transformers", "4.51.0"),
            ("tokenizers", "tokenizers", "0.20.0"),
            ("huggingface-hub", "huggingface_hub", "0.35.3"),
            ("safetensors", "safetensors", "0.5.0"),
        ):
            self.distribution(name, module, version, body=body)

    @legacy_posix_launcher
    def test_no_python_is_diagnosed_by_launcher(self):
        environment = {**self.environment, "PATH": str(self.root / "absent-bin")}
        environment.pop("PCW_PYTHON")
        result = self.launch("--check", environment=environment)
        self.assertEqual(2, result.returncode)
        self.assertIn("No python3", result.stderr)
        self.assertEqual("", result.stdout)
        self.assertFalse(self.packages.exists())

    @legacy_posix_launcher
    def test_explicit_python_needs_no_python3_or_plugin_root_environment(self):
        environment = {**self.environment, "PATH": str(self.root / "absent-bin")}
        result = self.launch("--check", environment=environment)
        self.assertEqual(3, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(str(self.plugin), report["plugin_root"])
        self.assertEqual("missing_dependencies", report["status"])

    @legacy_posix_launcher
    def test_path_python_is_used_without_looking_for_a_venv(self):
        binary_directory = self.root / "bin"
        binary_directory.mkdir()
        (binary_directory / "python3").symlink_to(self.python)
        environment = {**self.environment, "PATH": str(binary_directory)}
        environment.pop("PCW_PYTHON")
        environment.pop("PCW_DEPENDENCIES")
        result = self.launch("--check", environment=environment)
        self.assertEqual(3, result.returncode, result.stderr)
        target = json.loads(result.stdout)["dependency_install_target"]
        self.assertTrue(target.startswith(str(self.root / "data" / "persistent-context-workspace" / "packages")))
        self.assertNotIn("venv", target)
        self.assertFalse((self.root / "data").exists())

    @legacy_posix_launcher
    def test_explicit_invalid_python_is_not_ignored(self):
        non_executable = self.root / "not executable"
        non_executable.write_text("not Python")
        cases = ("", "python3", "python3 -I", str(self.root / "missing"), str(non_executable), "/bin/true")
        for value in cases:
            with self.subTest(value=value):
                result = self.launch("--check", environment={**self.environment, "PCW_PYTHON": value})
                self.assertEqual(2, result.returncode)
                self.assertIn("PCW", result.stderr)
                self.assertEqual("", result.stdout)

    @legacy_posix_launcher
    def test_old_python_is_rejected_before_loading_plugin_code(self):
        old_python = self.root / "old python"
        program = "import sys; sys.version_info = (3, 10); sys.version = '3.10.0 (fixture)'; exec(sys.argv[-1])"
        old_python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -S -c " + shlex.quote(program) + ' "$@"\n')
        old_python.chmod(0o755)
        result = self.launch("--check", environment={**self.environment, "PCW_PYTHON": str(old_python)})
        self.assertEqual(2, result.returncode)
        self.assertIn(">=3.11", result.stderr)
        self.assertIn("3.10.0", result.stderr)
        self.assertEqual("", result.stdout)

    @legacy_posix_launcher
    def test_base_check_is_stdlib_only_and_read_only(self):
        self.base()
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        result = self.launch("--check")
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("ready", report["status"])
        self.assertEqual([], report["dependencies"]["local"])
        self.assertFalse(report["imports_checked"])
        self.assertFalse(report["model_checked"])
        self.assertEqual(str(self.packages), report["dependency_install_target"])
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))

    @legacy_posix_launcher
    def test_local_missing_dependencies_do_not_block_base_readiness(self):
        self.base()
        self.assertEqual(0, self.launch("--check").returncode)
        result = self.launch("--check-local")
        self.assertEqual(3, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(all(item["status"] == "available" for item in report["dependencies"]["base"]))
        self.assertTrue(all(item["status"] == "missing_distribution" for item in report["dependencies"]["local"]))

    @unittest.skipIf(sys.prefix != sys.base_prefix, "The selected test interpreter intentionally disables its user site")
    @legacy_posix_launcher
    def test_normal_user_site_packages_are_reused_and_explicit_restriction_is_respected(self):
        self.base()
        userbase = self.root / "existing user installation"
        user_site = Path(sysconfig.get_path("purelib", scheme="posix_user", vars={"userbase": str(userbase)}))
        user_site.parent.mkdir(parents=True)
        shutil.move(str(self.packages), str(user_site))
        environment = {**self.environment, "PCW_PYTHON": sys.executable, "PYTHONUSERBASE": str(userbase)}
        environment.pop("PYTHONNOUSERSITE", None)
        result = self.launch("--check", environment=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("0.20.0", report["dependencies"]["base"][0]["version"])
        result = self.launch("--check", environment={**environment, "PYTHONNOUSERSITE": "1"})
        report = json.loads(result.stdout)
        # The deliberately broken fixture modules prove that user site is not
        # imported by readiness; -s must also remove their distribution metadata.
        visible_versions = subprocess.check_output(
            [sys.executable, "-E", "-P", "-B", "-s", "-c",
             "import importlib.metadata as m; print(m.version('lancedb') if list(m.distributions(name='lancedb')) else 'missing')"],
            env=environment, text=True,
        ).strip()
        self.assertEqual(None if visible_versions == "missing" else visible_versions,
                         report["dependencies"]["base"][0]["version"])

    @legacy_posix_launcher
    def test_local_metadata_does_not_import_or_download(self):
        self.base()
        self.local()
        result = self.launch("--check-local")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(json.loads(result.stdout)["model_checked"])
        self.assertEqual([], list(self.root.rglob("__pycache__")))
        self.assertEqual([], list(self.project.iterdir()))

    @legacy_posix_launcher
    def test_selected_existing_venv_keeps_its_user_site_disabled(self):
        self.base()
        userbase = self.root / "existing user installation"
        user_site = Path(sysconfig.get_path("purelib", scheme="posix_user", vars={"userbase": str(userbase)}))
        user_site.parent.mkdir(parents=True)
        shutil.move(str(self.packages), str(user_site))
        # This is a disposable test fixture; the launcher never creates a venv.
        isolated = self.root / "existing selected environment"
        venv.EnvBuilder(with_pip=False).create(isolated)
        environment = {**self.environment, "PCW_PYTHON": str(isolated / "bin" / "python"), "PYTHONUSERBASE": str(userbase)}
        environment.pop("PYTHONNOUSERSITE", None)
        result = self.launch("--check", environment=environment)
        self.assertEqual(3, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("missing_distribution", report["dependencies"]["base"][0]["status"])

    @legacy_posix_launcher
    def test_incompatible_direct_version_and_missing_module_are_reported(self):
        self.base(mcp_version="1.9.9")
        result = self.launch("--check")
        self.assertEqual(3, result.returncode)
        report = json.loads(result.stdout)
        self.assertEqual("incompatible_dependencies", report["status"])
        self.assertEqual("incompatible_version", next(item for item in report["dependencies"]["base"] if item["distribution"] == "mcp")["status"])
        (self.packages / "lancedb" / "__init__.py").unlink()
        (self.packages / "lancedb").rmdir()
        result = self.launch("--check")
        report = json.loads(result.stdout)
        self.assertEqual("missing_module", report["dependencies"]["base"][0]["status"])

    @legacy_posix_launcher
    def test_explicit_import_failure_preserves_original_traceback(self):
        self.base()
        result = self.launch("--check-imports")
        self.assertEqual(4, result.returncode)
        self.assertEqual("import_error", json.loads(result.stdout)["status"])
        self.assertIn("Traceback (most recent call last)", result.stderr)
        self.assertIn("UNEXPECTED_DEPENDENCY_IMPORT", result.stderr)

    @legacy_posix_launcher
    def test_explicit_local_imports_are_separate_from_base_imports(self):
        self.base(body="")
        self.local()
        self.assertEqual(0, self.launch("--check-imports").returncode)
        result = self.launch("--check-local-imports")
        self.assertEqual(4, result.returncode)
        self.assertIn("UNEXPECTED_LOCAL_IMPORT", result.stderr)
        self.assertFalse(json.loads(result.stdout)["model_checked"])

    @legacy_posix_launcher
    def test_active_cached_plugin_wins_and_server_stays_in_one_process(self):
        self.base(body="")
        for directory in (self.packages, self.project):
            stale = directory / "pcw_runtime"
            stale.mkdir()
            (stale / "__init__.py").write_text("raise RuntimeError('OLD_OR_PROJECT_RUNTIME')\n")
        (self.project / "json.py").write_text("raise RuntimeError('PROJECT_STDLIB_SHADOW')\n")
        (self.project / "sitecustomize.py").write_text("raise RuntimeError('PROJECT_SITECUSTOMIZE')\n")
        environment = {**self.environment, "PYTHONPATH": str(self.project)}
        result = self.launch(environment=environment, input="first\nsecond\n")
        self.assertEqual(0, result.returncode, result.stderr)
        outputs = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(["first", "second"], [item["input"] for item in outputs])
        self.assertEqual(outputs[0]["pid"], outputs[1]["pid"])
        self.assertTrue(all(item["origin"] == str(self.plugin / "pcw_runtime" / "server.py") for item in outputs))
        self.assertTrue(all(item["transport"] == "stdio" for item in outputs))

    @legacy_posix_launcher
    def test_normal_startup_has_no_readiness_output_on_stdout(self):
        result = self.launch()
        self.assertEqual(3, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn('"status": "missing_dependencies"', result.stderr)
        self.assertIn("startup never installs", result.stderr)

    @legacy_posix_launcher
    def test_invalid_dependency_directory_is_actionable(self):
        for value in ("", "relative/packages", str(self.python)):
            with self.subTest(value=value):
                result = self.launch("--check", environment={**self.environment, "PCW_DEPENDENCIES": value})
                self.assertEqual(2, result.returncode)
                self.assertEqual("invalid_configuration", json.loads(result.stdout)["status"])

    def test_version_bounds_handle_padding_and_stable_local_versions(self):
        spec = importlib.util.spec_from_file_location("pcw_check_tests", SCRIPTS / "check_runtime.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertTrue(checker._supported_version("6", (6,), (7,)))
        self.assertTrue(checker._supported_version("6.0.0+cpu", (6,), (7,)))
        self.assertFalse(checker._supported_version("6.0rc1", (6,), (7,)))
        self.assertFalse(checker._supported_version("7.0.0", (6,), (7,)))


if __name__ == "__main__":
    unittest.main()
