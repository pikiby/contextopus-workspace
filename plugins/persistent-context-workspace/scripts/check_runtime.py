#!/usr/bin/env python3
"""Read-only readiness checks using only the Python standard library.

Ordinary checks inspect distribution metadata and top-level module locations.
Import checks are explicit and never construct an embedding model or workspace.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import sysconfig
import traceback


BASE = (
    ("lancedb", "lancedb", ">=0.20,<1", (0, 20), (1,)),
    ("mcp", "mcp", ">=1.10,<2", (1, 10), (2,)),
    ("PyYAML", "yaml", ">=6,<7", (6,), (7,)),
    ("pydantic", "pydantic", ">=2.11,<3", (2, 11), (3,)),
    ("pyarrow", "pyarrow", "installed", None, None),
)
LOCAL = (
    ("sentence-transformers", "sentence_transformers", ">=3,<6", (3,), (6,)),
    ("torch", "torch", "installed", None, None),
    ("transformers", "transformers", "installed", None, None),
    ("tokenizers", "tokenizers", "installed", None, None),
    ("huggingface-hub", "huggingface_hub", "installed", None, None),
    ("safetensors", "safetensors", "installed", None, None),
)


def dependency_target(environment: dict[str, str]) -> Path:
    if "PCW_DEPENDENCIES" in environment:
        value = environment["PCW_DEPENDENCIES"]
        if not value or not Path(value).is_absolute():
            raise ValueError("PCW_DEPENDENCIES must be a nonempty absolute directory path")
        target = Path(value)
    else:
        data_root = Path(environment.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        if not data_root.is_absolute():
            raise ValueError("XDG_DATA_HOME must be an absolute directory path")
        tag = str(sys.implementation.cache_tag) + "-" + sysconfig.get_platform()
        target = data_root / "persistent-context-workspace" / "packages" / tag
    if target.exists() and not target.is_dir():
        raise ValueError("PCW dependency installation target is not a directory: " + str(target))
    return target


def _supported_version(value, minimum, maximum):
    if minimum is None:
        return True
    # Direct requirements have stable numeric bounds. Do not claim that an
    # unrecognized or prerelease version meets the supported stable range.
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:\.post\d+)?(?:\+[a-zA-Z0-9.]+)?", value)
    if match is None:
        return False
    release = tuple(int(part) for part in match.group(1).split("."))
    width = max(len(release), len(minimum), len(maximum))
    padded = lambda item: item + (0,) * (width - len(item))
    return padded(minimum) <= padded(release) < padded(maximum)


def _dependency(requirement):
    distribution, module, required, minimum, maximum = requirement
    result = {"distribution": distribution, "module": module, "required": required, "version": None}
    try:
        result["version"] = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        result["status"] = "missing_distribution"
        return result
    if not _supported_version(result["version"], minimum, maximum):
        result["status"] = "incompatible_version"
    elif importlib.util.find_spec(module) is None:
        result["status"] = "missing_module"
    else:
        result["status"] = "available"
    return result


def readiness(plugin_root: Path, *, local: bool = False, imports: bool = False):
    report = {
        "status": "ready",
        "check": ("local" if local else "base") + ("_imports" if imports else ""),
        "python": {"executable": sys.executable, "version": sys.version.split()[0], "minimum": "3.11"},
        "plugin_root": str(plugin_root),
        "dependency_install_target": None,
        "externally_managed": (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").is_file(),
        # Measured before the private directory is added: this reports whether
        # the selected interpreter can run `-E -P -m pip` directly.
        "pip_available": importlib.util.find_spec("pip") is not None,
        "dependencies": {"base": [], "local": []},
        "imports_checked": False,
        "model_checked": False,
    }
    if sys.version_info < (3, 11):
        report.update(status="unsupported_python", error="PCW requires Python >=3.11")
        return report, 2
    try:
        target = dependency_target(dict(os.environ))
    except ValueError as error:
        report.update(status="invalid_configuration", error=str(error))
        return report, 2
    report["dependency_install_target"] = str(target)
    # launch.sh excludes unsafe import paths. Keep the active bundle ahead of any wheel
    # from an explicitly installed private target or the interpreter site.
    roots = [str(plugin_root)] + ([str(target)] if target.is_dir() else [])
    sys.path[:] = roots + [entry for entry in sys.path if entry not in roots]
    report["dependencies"]["base"] = [_dependency(item) for item in BASE]
    if local:
        report["dependencies"]["local"] = [_dependency(item) for item in LOCAL]
    dependencies = report["dependencies"]["base"] + report["dependencies"]["local"]
    if any(item["status"].startswith("missing_") for item in dependencies):
        report["status"] = "missing_dependencies"
        return report, 3
    if any(item["status"] == "incompatible_version" for item in dependencies):
        report["status"] = "incompatible_dependencies"
        return report, 3
    if imports:
        try:
            for item in dependencies:
                importlib.import_module(item["module"])
            importlib.import_module("pcw_runtime.server")
        except Exception as error:
            report.update(status="import_error", error=str(error))
            traceback.print_exc()
            return report, 4
        report["imports_checked"] = True
    return report, 0


def main(arguments=None, *, plugin_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Inspect base metadata without importing runtime dependencies")
    modes.add_argument("--check-local", action="store_true", help="Also inspect local embedding dependency metadata")
    modes.add_argument("--check-imports", action="store_true", help="Explicitly import base dependencies and the bundled server")
    modes.add_argument("--check-local-imports", action="store_true", help="Explicitly import base and local dependencies and the bundled server")
    args = parser.parse_args(arguments)
    report, status = readiness(
        plugin_root or Path(__file__).resolve().parents[1],
        local=args.check_local or args.check_local_imports,
        imports=args.check_imports or args.check_local_imports,
    )
    print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
