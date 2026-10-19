from workspace_support import install_workspace, workspace_module
"""Core accepts documents without Workspace authoring or YAML."""

import copy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from contextopus.core.storage import StorageLayout
from contextopus.core.resources import ProcessResources
from contextopus.core.rag import DocumentKey, IndexDocument, RagIndex
from contextopus.core.embeddings import default_config
from contextopus.core.chunking import default_chunking
from contextopus.core.operations import OperationRegistry, OperationSpec
from contextopus.core import relocation
from contextopus.startup import Application
from test_runtime import Factory
from test_shared_v4 import context, save


def test_generic_and_workspace_share_table_and_embeddings(tmp_path):
    factory = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    workspace = context(tmp_path, "alpha", factory)
    one = save(workspace, "# Workspace\nsearchable text")
    layout = StorageLayout(tmp_path)
    with ProcessResources(factory) as resources:
        index = RagIndex(layout, resources, default_config(), default_chunking())
        document = IndexDocument(
            DocumentKey("second", "notes", "plain-1"),
            "rev1",
            "searchable plain text",
            "second://1",
            {"object_type": "note"},
        )
        index.index(document)
        assert index.table.name == workspace.engine.active["table"]
        assert index.db.uri == workspace.engine.db.uri
        found = index.search("searchable", module="second", distance=2)
        assert found[0]["key"] == ["second", "notes", "plain-1"]
        assert (
            workspace.read(material_id=one["id"])["content"]
            == "# Workspace\nsearchable text"
        )
        assert {
            r["workspace"]
            for r in workspace.search(query="searchable", N=20, d_max=2)["results"]
        } == {"alpha"}
    workspace.close()


def test_application_registry_external_operation_and_no_model_status(tmp_path):
    with Application(tmp_path, discover=False) as app:
        app.registry.register(
            OperationSpec(
                "third.query",
                "query",
                {"type": "object", "additionalProperties": False},
                lambda ctx: {"root": str(ctx.root)},
            )
        )
        assert app.query("third.query")["root"] == str(tmp_path)
        assert "third.query" in app.query("describe")["operations"]["query"]
        assert not app.resources.providers._entries
        assert app.query("core.doctor")["model_loaded"] is False
        assert app.journal.summary()["requests"] >= 2
        with pytest.raises(ValueError):
            app.execute("third.query")


def test_relocation_binary_pending_proposal_layout_alias_and_redirect(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    path = source / ".pcw/alpha/tmp/cycle/proposals/pending.json"
    path.parent.mkdir(parents=True)
    body = b'{"revision":"unchanged", "path":".pcw/original"}\x00\xff'
    path.write_bytes(body)
    backup = source / ".pcw/.control/alpha/backups/id/workspace"
    backup.mkdir(parents=True)
    (backup / "topic.md").write_text("snapshot")
    proposal = relocation.plan(source, destination)
    result = relocation.apply(proposal)
    assert result["status"] == "complete"
    assert (
        destination
        / "modules/workspace/workspace_contexts/alpha/tmp/cycle/proposals/pending.json"
    ).read_bytes() == body
    assert (
        source
        / ".contextopus-retained"
        / proposal["revision"]
        / ".pcw/alpha/tmp/cycle/proposals/pending.json"
    ).read_bytes() == body
    assert (source / ".pcw").is_file()
    with pytest.raises((NotADirectoryError, FileExistsError)):
        (source / ".pcw/.control/alpha").mkdir(parents=True, exist_ok=True)
    assert StorageLayout.resolve(source).root == destination
    assert (
        relocation.checked_alias(StorageLayout(destination), backup)
        == destination / "modules/workspace/control/alpha/backups/id/workspace"
    )
    assert relocation.apply(proposal)["status"] == "complete"
    target = (
        destination
        / "modules/workspace/workspace_contexts/alpha/tmp/cycle/proposals/pending.json"
    )
    target.write_text("changed")
    with pytest.raises(ValueError, match="Destination verification"):
        relocation.apply(proposal)


def test_relocation_failed_copy_retry_and_rollback_no_clobber(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    path = source / ".pcw/alpha/knowledge/topic.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"text")
    proposal = relocation.plan(source, destination)
    with patch.object(relocation.shutil, "copy2", side_effect=OSError("injected")):
        with pytest.raises(OSError):
            relocation.apply(proposal)
    assert (source / ".contextopus-freeze.json").exists()
    assert relocation.apply(proposal)["status"] == "complete"
    with pytest.raises(ValueError):
        relocation.rollback(proposal)


def test_relocation_tamper_refused_and_unpublished_rollback(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    path = source / ".pcw/alpha/topic.md"
    path.parent.mkdir(parents=True)
    path.write_text("text")
    proposal = relocation.plan(source, destination)
    tampered = copy.deepcopy(proposal)
    tampered["files"][0]["path"] = "../outside"
    with pytest.raises(ValueError):
        relocation.apply(tampered)
    with patch.object(relocation.shutil, "copy2", side_effect=OSError("injected")):
        with pytest.raises(OSError):
            relocation.apply(proposal)
    assert relocation.rollback(proposal)["status"] == "rolled_back"
    assert path.read_text() == "text"
    assert not (source / ".contextopus-freeze.json").exists()


def test_relocation_sqlite_wal_consistent_backup(tmp_path):
    import sqlite3

    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    path = source / ".pcw/.analytics/events.sqlite"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(x)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()
    proposal = relocation.plan(source, destination)
    db.close()
    # Closing checkpoints the source; re-preview the exact final snapshot.
    proposal = relocation.plan(source, destination)
    relocation.apply(proposal)
    with sqlite3.connect(destination / "core/analytics/events.sqlite") as target:
        assert target.execute("SELECT x FROM t").fetchone() == (42,)
    assert (destination / ".backups/relocations" / proposal["snapshot_id"] / ".pcw/.analytics/events.sqlite").is_file()


def test_generic_namespace_collision_prefilter_atomic_failure_and_profile_refusal(
    tmp_path,
):
    factory = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    workspace = context(tmp_path, "alpha", factory)
    one = save(workspace, "# Workspace\nshared answer")
    with ProcessResources(factory) as resources:
        index = RagIndex(
            StorageLayout(tmp_path), resources, default_config(), default_chunking()
        )
        key = DocumentKey("second", "alpha", one["id"])
        index.index(
            IndexDocument(
                key,
                "r1",
                "shared answer",
                "other://1",
                {"archived": True, "object_type": "memo"},
            )
        )
        index.index(
            IndexDocument(
                DocumentKey("second", "alpha", "visible"),
                "r2",
                "shared answer",
                "other://2",
                {"object_type": "memo"},
            )
        )
        assert (
            index.search("shared", module="second", object_type="memo", limit=1)[0][
                "key"
            ][2]
            == "visible"
        )
        workspace.engine.reconcile()
        workspace.engine.delete(one["id"])
        found = index.search(
            "shared", module="second", include_archived=True, distance=2
        )
        assert {row["key"][2] for row in found} == {one["id"], "visible"}
        with patch.object(index, "rows", side_effect=RuntimeError("failed embedding")):
            with pytest.raises(RuntimeError):
                index.index(IndexDocument(key, "r3", "new", "other://1"))
        assert {
            row["revision"]
            for row in index.search(
                "shared", module="second", include_archived=True, distance=2
            )
        } == {"r1", "r2"}
        with pytest.raises(ValueError, match="source adapters"):
            workspace.engine.switch_embeddings(
                default_config(),
                {
                    "N": 20,
                    "d_max": 1,
                    "min_jump": 0.02,
                    "multiplier": 3,
                    "metric": "cosine",
                },
            )
    workspace.close()


def test_generic_read_missing_root_does_not_create_storage(tmp_path):
    root = tmp_path / "missing"
    with ProcessResources(Factory()) as resources:
        index = RagIndex(StorageLayout(root), resources)
        assert index.search("anything") == []
        assert not root.exists()
        assert not resources.providers._entries


def test_new_open_layout_preserves_real_pending_proposal_after_relocation(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    original = context(source, "alpha", Factory())
    cycle = original.startup("work")["cycle_id"]
    original.tmp_write(cycle, content="a result")
    prepared = original.proposal_prepare(
        cycle, [{"key": "new", "content": "# Pending\nanswer", "links": [],
                 "description": "Records the pending answer whose approved revision must survive root relocation."}]
    )
    revision = prepared["revision"]
    proposal_id = prepared["proposal_id"]
    before = original.proposal_read(cycle, proposal_id)
    original.close()
    relocation.apply(relocation.plan(source, destination))
    from workspace.cycles import CycleRuntime

    moved = CycleRuntime(source, "alpha", provider_factory=Factory())
    assert (
        moved.engine.root == destination / "modules/workspace/workspace_contexts/alpha"
    )
    assert moved.proposal_read(cycle, proposal_id)["revision"] == revision
    assert moved.proposal_apply(cycle, proposal_id, revision)["status"] == "applied"
    assert moved.engine.state == destination / "core/rag"
    assert moved.tmp_list(cycle)["materials"]
    moved.close()


def test_application_relocation_observer_polling_preserved_and_source_change_stale(
    tmp_path,
):
    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    path = source / ".pcw/alpha/knowledge/topic.md"
    path.parent.mkdir(parents=True)
    path.write_text("authoritative")
    with Application(source, discover=False) as app:
        proposal = app.query(
            "core.relocation_plan",
            {"destination": str(destination), "include_manifest": True},
        )
        app.query("core.doctor")
        app.query("core.settings")
        result = app.execute("core.relocation_apply", {"plan": proposal})
        assert result["status"] == "complete"
        assert app.journal.path == destination / "core/analytics/operations.sqlite3"
        import sqlite3

        with sqlite3.connect(app.journal.path) as db:
            rows = dict(db.execute("SELECT operation,outcome FROM operations"))
        assert rows["core.relocation_apply"] == "ok"
        assert rows["core.relocation_plan"] == "ok"
        assert rows["core.doctor"] == "ok"
        assert relocation.apply(proposal)["status"] == "complete"
    other = tmp_path / "other"
    other.mkdir()
    target = tmp_path / "target"
    material = other / ".pcw/alpha/knowledge/topic.md"
    material.parent.mkdir(parents=True)
    material.write_text("v1")
    with Application(other, discover=False) as app:
        stale = app.query("core.relocation_plan", {"destination": str(target)})
        material.write_text("v2")
        with pytest.raises(ValueError, match="Source changed"):
            app.execute("core.relocation_apply", {"plan": stale})
        assert not (other / ".contextopus-freeze.json").exists()


def test_legacy_busy_lock_prevents_freeze(tmp_path):
    from contextopus.core import locking

    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    lock = source / ".pcw/.control/alpha/lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    proposal = relocation.plan(source, destination)
    with lock.open("a+b") as stream:
        locking.flock(stream.fileno(), locking.LOCK_EX)
        with pytest.raises(OSError):
            relocation.apply(proposal)
        locking.flock(stream.fileno(), locking.LOCK_UN)
    assert (source / ".pcw").is_dir()
    assert not (source / ".contextopus-freeze.json").exists()
    relocation.apply(proposal)
