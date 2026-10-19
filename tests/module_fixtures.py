"""Synthetic independent module used by Workspace lifecycle integration tests."""
import json

ADAPTER = '''
from pathlib import Path
import time

def save(arguments, core):
    if "ready" not in arguments:
        return core.storage.execute(
            'INSERT INTO "{{module}}::items" VALUES (?)', [arguments["value"]])
    with core.storage.transaction(timeout_seconds=5) as transaction:
        transaction.execute('INSERT INTO "{{module}}::items" VALUES (?)',
                            [arguments["value"]])
        Path(arguments["ready"]).touch()
        deadline = time.monotonic() + 4
        while not Path(arguments["release"]).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Test release signal did not arrive")
            time.sleep(.01)
    return {"committed": True}

def read(arguments, core):
    return core.storage.query('SELECT value FROM "{{module}}::items" ORDER BY value')
'''


def installable(root, module):
    folder = root / "modules" / module
    package = folder / "package"
    (package / "migrations").mkdir(parents=True)
    (package / "migrations/001-schema.sql").write_text(
        'CREATE TABLE "{{module}}::items" (value TEXT PRIMARY KEY);'
    )
    (package / "adapter.py").write_text(ADAPTER)
    manifest = {
        "id": module,
        "version": "1.0",
        "storage": {
            "engine": "sqlite", "schema_version": 1, "migrations": "migrations",
            "compatible_versions": {"min": 1, "max": 1},
        },
        "operations": [
            {"name": module + "." + name, "kind": kind,
             "handler": "adapter.py:" + name, "protocol": "contextopus-core-v1"}
            for name, kind in (("save", "execute"), ("read", "query"))
        ],
    }
    (folder / "module.json").write_text(json.dumps(manifest))

