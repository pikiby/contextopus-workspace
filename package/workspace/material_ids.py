"""Explicit transfer of Workspace reservations out of the shared RAG state."""

import hashlib
import json
from pathlib import Path
import os
import uuid

from contextopus.core.module_storage import ModuleStorage
from contextopus.core.module_storage.trusted_import import _write_json

from .runtime import PCWError


def _paths(layout):
    source = layout.workspace_rag / "material-ids.json"
    target = layout.reservations
    receipt = target.parent / ".material-ids-migration.json"
    safe = ModuleStorage(layout).for_module("workspace")
    for path in (source, target, receipt):
        safe._safe_path(path)
    return source, target, receipt


def _validate(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or any(not isinstance(key, str) or not isinstance(value, dict)
                                         for key, value in data.items()):
        raise PCWError("Invalid Workspace material reservations")
    return data


def _publish_bytes(path, raw):
    from contextopus.core.module_storage.legacy import _new_file, _sync
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    _new_file(temporary)
    try:
        temporary.write_bytes(raw)
        _sync(temporary)
        os.link(temporary, path)
        _sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(layout):
    source, target, receipt = _paths(layout)
    if target.exists():
        _validate(target.read_bytes())
    source_name = source.relative_to(layout.root).as_posix()
    target_name = target.relative_to(layout.root).as_posix()
    if receipt.exists():
        state = json.loads(receipt.read_text(encoding="utf-8"))
        if (state.get("source") != source_name or state.get("target") != target_name
                or state.get("format") != 1):
            raise PCWError("Invalid Workspace material reservation migration receipt")
        backup = target.parent / (".material-ids-" + state["sha256"] + ".backup.json")
        if (backup.is_symlink() or not backup.is_file()
                or hashlib.sha256(backup.read_bytes()).hexdigest() != state["sha256"]):
            raise PCWError("Workspace material reservation backup changed")
        if not target.is_file():
            raise PCWError("Migrated Workspace material reservations disappeared")
        migrated = _validate(target.read_bytes())
        if not set(_validate(backup.read_bytes())) <= set(migrated):
            raise PCWError("Migrated Workspace material reservations lost IDs")
        if source.exists() and hashlib.sha256(source.read_bytes()).hexdigest() != state["sha256"]:
            raise PCWError("Legacy Workspace material reservations changed after migration")
        return {**state, "needs_migration": source.exists()}
    if not source.exists():
        return {"needs_migration": False, "sha256": None}
    raw = source.read_bytes()
    _validate(raw)
    if target.exists() and target.read_bytes() != raw:
        raise PCWError("Conflicting Workspace material reservations; refusing overwrite")
    return {"format": 1, "source": source_name, "target": target_name,
            "sha256": hashlib.sha256(raw).hexdigest(), "needs_migration": True}


def apply(layout, expected_sha256):
    with layout.guard():
        plan = prepare(layout)
        if plan["sha256"] != expected_sha256:
            raise PCWError("Workspace material reservations changed; prepare again")
        if not plan["needs_migration"]:
            return plan
        source, target, receipt = _paths(layout)
        raw = source.read_bytes()
        state = {key: plan[key] for key in ("format", "source", "target", "sha256")}
        backup = target.parent / (".material-ids-" + expected_sha256 + ".backup.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        if backup.is_symlink():
            raise PCWError("Workspace reservation backup cannot be a symlink")
        if backup.exists() and backup.read_bytes() != raw:
            raise PCWError("Conflicting Workspace material reservation backup")
        if not backup.exists():
            # JSON is validated before mutation; storing the exact original
            # bytes preserves the source SHA and makes recovery unambiguous.
            _publish_bytes(backup, raw)
        if not target.exists():
            _publish_bytes(target, raw)
        _write_json(receipt, state)
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
            raise PCWError("Legacy Workspace reservations changed during migration")
        source.unlink()
        from contextopus.core.storage import sync_directory
        sync_directory(source.parent)
        return {**state, "needs_migration": False}
