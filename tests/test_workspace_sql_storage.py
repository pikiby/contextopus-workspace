"""Explicit, lossless native Workspace storage lifecycle in disposable roots."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from contextopus.core.module_storage import ModuleStorage, ModuleStorageError
from contextopus.core.module_storage.trusted_import import inventory
from contextopus.core.storage import StorageLayout
from workspace.analytics import EventStore
from workspace.runtime import PCWError


def install(root):
    store = EventStore(root)
    plan = store.storage_prepare()
    store.storage_apply(plan["revision"])
    return store


def old_database(root):
    """An actual pre-cutover SQLite, including implicit rowid cursor gaps."""
    source = root / "core/analytics/events.sqlite"
    source.parent.mkdir(parents=True)
    (root / "core/configuration").mkdir()
    (root / "core/configuration/layout.json").write_text('{"format":"contextopus","version":1}')
    schema = Path(__file__).parents[1] / "package/workspace/sql_migrations/001-schema.sql"
    with sqlite3.connect(source) as db:
        db.executescript(schema.read_text())
        db.execute("INSERT INTO blobs VALUES ('body','\"original\"',0)")
        db.execute("INSERT INTO events(id,workspace,tool,started_at,state,params) VALUES ('original-id','context','read','2026-01-01','success','body')")
        db.execute("INSERT INTO versions(rowid,event_id,path,relation,after_blob) VALUES (7,'original-id','knowledge/a.md','read','body')")
        db.execute("INSERT INTO history_commits VALUES ('context','commit-id','{}')")
        db.execute("INSERT INTO history_versions VALUES ('context','commit-id','a.md','knowledge/a.md','git-blob')")
        db.execute("INSERT INTO history_edges VALUES ('context','commit-id',0,'edit','a.md','parent','a.md','commit-id')")
        db.execute("INSERT INTO legacy_snapshot_references VALUES ('context','versions',7,'after','{}')")
    return source


def test_read_does_not_install_or_change_schema(tmp_path):
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    assert plan["schema"]["needs_migration"]
    assert not store.path.exists() and not (tmp_path / "core").exists()
    with pytest.raises(PCWError, match="explicit"):
        store.begin("context", "read", {})
    assert not store.path.exists()
    store.storage_apply(plan["revision"])
    assert not store.storage_prepare()["needs_migration"]
    with store.connect() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM sqlite_master ORDER BY name")]
    store.begin("context", "read", {})
    with store.connect() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM sqlite_master ORDER BY name")] == before
    assert store.path == tmp_path / "core/storage/modules/workspace.sqlite3"
    assert not (tmp_path / "core/analytics/operations.sqlite3").exists()


def test_legacy_cutover_preserves_every_row_reference_and_cursor(tmp_path):
    source = old_database(tmp_path)
    with closing(sqlite3.connect(source)) as reader:
        reader.row_factory = sqlite3.Row
        original = inventory(reader)
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    assert not store.path.exists()
    with pytest.raises(PCWError, match="explicit"):
        store.read("original-id")
    result = store.storage_apply(plan["revision"])
    assert Path(result["import"]["backup"]).is_file()
    assert json.loads(Path(result["import"]["receipt"]).read_text())["status"] == "complete"
    with store.connect() as db:
        current = inventory(db)
        current["tables"].pop("_contextopus_module_migrations")
        current["tables"].pop("workspace_edit_reservations")
        assert db.execute("SELECT count(*) FROM workspace_edit_reservations").fetchone()[0] == 0
        assert current["tables"] == original["tables"]
        assert db.execute("SELECT rowid FROM versions").fetchone()[0] == 7
    assert store.read("original-id")["params"]["value"] == "original"
    with sqlite3.connect(source) as writer:
        with pytest.raises(sqlite3.IntegrityError, match="migrated"):
            writer.execute("INSERT INTO blobs VALUES ('wrong','text',0)")
    store.begin("context", "new-write", {})
    # Retry after new target writes must not restore the old snapshot over them.
    store.storage_apply(plan["revision"])
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


@pytest.mark.parametrize("after_publication", [False, True])
def test_failed_publication_or_receipt_retries_losslessly(tmp_path, monkeypatch, after_publication):
    import contextopus.core.module_storage.trusted_import as migration
    old_database(tmp_path)
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    if after_publication:
        original = migration._write_json
        def fail(path, state):
            if state.get("status") == "complete":
                raise OSError("receipt publication interrupted")
            return original(path, state)
        monkeypatch.setattr(migration, "_write_json", fail)
    else:
        monkeypatch.setattr(migration, "_publish", lambda *_: (_ for _ in ()).throw(OSError("target publication interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        store.storage_apply(plan["revision"])
    monkeypatch.undo()
    assert store.storage_apply(plan["revision"])["status"] == "complete"
    assert store.read("original-id")["id"] == "original-id"


def test_changed_source_and_conflicting_target_fail_before_import(tmp_path):
    source = old_database(tmp_path)
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO blobs VALUES ('new','new',0)")
    with pytest.raises(PCWError, match="changed"):
        store.storage_apply(plan["revision"])
    assert not store.path.exists()
    store.path.parent.mkdir(parents=True)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TABLE unrelated(id)")
    with pytest.raises(ModuleStorageError, match="refusing overwrite"):
        store.storage_prepare()


def test_native_transaction_rolls_back_and_independent_module_does_not_wait(tmp_path):
    store = install(tmp_path)
    with pytest.raises(RuntimeError):
        with store.connect() as db:
            db.execute("INSERT INTO blobs VALUES ('aborted','text',0)")
            raise RuntimeError("rollback")
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM blobs WHERE id='aborted'").fetchone()
    other = ModuleStorage(StorageLayout(tmp_path)).for_module("other").native()
    done = threading.Event()
    errors = []
    def write_other():
        try:
            with other.connect(create=True) as db:
                db.execute("CREATE TABLE payload(id)")
                db.execute("INSERT INTO payload VALUES (1)")
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    with store.connect() as db:
        db.execute("INSERT INTO blobs VALUES ('held','text',0)")
        worker = threading.Thread(target=write_other)
        worker.start()
        assert done.wait(2), "Workspace writer retained the root-wide lock"
    worker.join()
    assert not errors


def test_historical_optional_columns_upgrade_only_during_explicit_apply(tmp_path):
    source = old_database(tmp_path)
    with sqlite3.connect(source) as db:
        for name in ("instructions_revision", "instructions_ref", "runtime_version", "procedures_revision", "operation_id", "journal_format"):
            db.execute("ALTER TABLE events DROP COLUMN " + name)
        for name in ("scope", "instruction_basis"):
            db.execute("ALTER TABLE evaluations DROP COLUMN " + name)
    store = install(tmp_path)
    with store.connect() as db:
        assert db.execute("SELECT id,journal_format FROM events").fetchone()[0:2] == ("original-id", 1)
        assert {"scope", "instruction_basis"} <= {row[1] for row in db.execute("PRAGMA table_info(evaluations)")}


def test_failed_schema_migration_rolls_back_and_retries_without_reimport(tmp_path, monkeypatch):
    old_database(tmp_path)
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    original = store._upgrade_columns
    def fail(db, version):
        db.execute("INSERT INTO blobs VALUES ('temporary','text',0)")
        raise RuntimeError("schema interrupted")
    monkeypatch.setattr(store, "_upgrade_columns", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        store.storage_apply(plan["revision"])
    with pytest.raises(PCWError, match="explicit"):
        store.storage_ready()
    monkeypatch.setattr(store, "_upgrade_columns", original)
    store.storage_apply(plan["revision"])
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM blobs WHERE id='temporary'").fetchone()
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_reservation_registry_moves_with_exact_backup_and_retry(tmp_path, monkeypatch):
    from workspace import material_ids
    source = tmp_path / ".pcw/.rag/material-ids.json"
    source.parent.mkdir(parents=True)
    raw = b'{"existing.md": {"workspace": "context"}}\n'
    source.write_bytes(raw)
    store = EventStore(tmp_path)
    plan = store.storage_prepare()
    original = material_ids._write_json
    monkeypatch.setattr(material_ids, "_write_json", lambda *_: (_ for _ in ()).throw(OSError("receipt interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        store.storage_apply(plan["revision"])
    assert source.read_bytes() == raw
    monkeypatch.setattr(material_ids, "_write_json", original)
    store.storage_apply(plan["revision"])
    assert not source.exists()
    assert store.layout.reservations.read_bytes() == raw
    assert store.storage_ready()["ready"]
    store.storage_apply(plan["revision"])
    assert store.layout.legacy  # module code/data never changes legacy context routing
    backups = list(store.layout.reservations.parent.glob('*.backup.json'))
    assert len(backups) == 1 and backups[0].read_bytes() == raw


def test_import_provenance_and_reservations_survive_root_move(tmp_path):
    root = tmp_path / "before"
    root.mkdir()
    old_database(root)
    source_ids = root / "core/rag/material-ids.json"
    source_ids.parent.mkdir(parents=True)
    source_ids.write_text('{"kept.md":{"workspace":"context"}}')
    store = install(root)
    original_revision = store.storage_prepare()["revision"]
    destination = tmp_path / "after"
    shutil.copytree(root, destination)
    moved = EventStore(destination)
    assert moved.storage_ready()["ready"]
    assert moved.storage_prepare()["revision"] == original_revision
    moved.storage_apply(original_revision)
    assert moved.read("original-id")["id"] == "original-id"


def test_registry_context_operation_allows_other_module_and_relocation_drains_it(tmp_path):
    from contextopus.core.operations import OperationRegistry, OperationSpec
    from contextopus.core.module_storage.coordination import quiesce_module_writes
    from workspace.control import control_store
    store = install(tmp_path)
    layout = StorageLayout(tmp_path)
    context = control_store(tmp_path, "context")
    held, proceed, relocating, drained = (threading.Event() for _ in range(4))
    registry = OperationRegistry()
    def operation(request):
        with context.guard():
            held.set()
            assert proceed.wait(3)
            # Relocation already holds root admission at this point. The live
            # context scope must still be able to finish its own SQL write.
            with store.connect() as db:
                db.execute("INSERT INTO blobs VALUES ('nested','text',0)")
        return {"completed": True}
    registry.register(OperationSpec("workspace.context_test", "execute", {"type": "object"},
                                    operation, guard_root=False))
    def relocation():
        with layout.guard():
            relocating.set()
            with quiesce_module_writes(layout):
                drained.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        operation_future = pool.submit(registry.dispatch, "execute", "workspace.context_test", {}, SimpleNamespace(root=tmp_path))
        assert held.wait(2)
        other = ModuleStorage(layout).for_module("other").native()
        with other.connect(create=True) as db:
            db.execute("CREATE TABLE payload(id)")
        relocation_future = pool.submit(relocation)
        try:
            assert relocating.wait(2)
            assert not drained.wait(.05)
        finally:
            proceed.set()
        assert operation_future.result(timeout=3)["completed"]
        relocation_future.result(timeout=3)
    assert drained.is_set()


def test_resource_admission_does_not_grant_other_module_or_root_bypass(tmp_path):
    store = install(tmp_path)
    held, release = threading.Event(), threading.Event()
    def root_holder():
        with StorageLayout(tmp_path).guard():
            held.set()
            assert release.wait(2)
    with store.storage.guard("context:test"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            holder = pool.submit(root_holder)
            try:
                assert held.wait(1)
                other = ModuleStorage(StorageLayout(tmp_path)).for_module("other").native()
                with pytest.raises(ModuleStorageError, match="root admission"):
                    with other.connect(create=True, deadline=time.monotonic() + .05):
                        pytest.fail("Cross-module admission bypassed root")
                with pytest.raises(ModuleStorageError, match="another"):
                    with store.storage.guard("different-resource"):
                        pytest.fail("Nested different resource could deadlock relocation")
            finally:
                release.set()
            holder.result(timeout=2)
