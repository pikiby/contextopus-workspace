"""Compatibility alias to the Workspace module implementation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "package"))
from workspace import workspaces as _implementation
sys.modules[__name__] = _implementation
