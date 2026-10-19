#!/usr/bin/env python3
"""Internal Python entry; use launch.sh to select and validate the interpreter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def main() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    # Load the checker by its bundled path, not a project or installed wheel.
    specification = importlib.util.spec_from_file_location(
        "pcw_runtime_readiness", plugin_root / "scripts" / "check_runtime.py"
    )
    checker = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(checker)
    arguments = sys.argv[1:]
    flags = {"--check", "--check-local", "--check-imports", "--check-local-imports"}
    if arguments and arguments[0] in flags:
        if len(arguments) != 1:
            print("PCW readiness checks accept one check flag only.", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(checker.main(arguments, plugin_root=plugin_root))

    report, status = checker.readiness(plugin_root, local=False)
    if status:
        print(json.dumps(report, indent=2), file=sys.stderr)
        print(
            "PCW dependencies are unavailable in the selected Python environment. "
            "Run launch.sh --check and follow README.md for explicit installation; "
            "startup never installs dependencies.",
            file=sys.stderr,
        )
        raise SystemExit(status)

    try:
        from pcw_runtime import server  # noqa: F401 - diagnose real dependency imports here
        from pcw_runtime.__main__ import main as run_mcp
    except Exception:
        print(
            "PCW runtime import failed after the metadata check. "
            "Run launch.sh --check-imports for the selected environment; "
            "the original traceback follows.",
            file=sys.stderr,
        )
        raise
    # Keep the server and reusable embedding providers in this process.
    run_mcp()


if __name__ == "__main__":
    main()
