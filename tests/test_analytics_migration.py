"""Lossless bounded legacy snapshot migration against native Git and SQLite."""
import hashlib
import json

import pytest

from workspace.analytics import EventStore
from workspace.analytics_migration import AnalyticsMigrationMixin
from test_history_catalog import history, commit


@pytest.fixture
def migration(history):
    class Runtime(AnalyticsMigrationMixin, type(history)):
        pass
    r = Runtime()
    r.__dict__.update(history.__dict__)
    r.engine.workspace = 'context'
    return r, EventStore(r.engine.work_root)


def legacy(r, store, text, retained=None, workspace='context', observed=False, shared=False):
    with store.connect() as db:
        blob = store._blob(db, text if retained is None else retained)
        event = 'event-' + str(db.execute('SELECT count(*) FROM events').fetchone()[0])
        db.execute('INSERT INTO events(id,workspace,tool,started_at,state,params) VALUES (?,?,?,?,?,?)',
                   (event, workspace, 'read', '2026-01-01', 'success', blob if shared else None))
        digest = hashlib.sha256(text.encode()).hexdigest()
        db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?)',
                   (event, 'knowledge/a.md', 'read', None, digest, None, blob))
        if observed:
            db.execute('INSERT OR REPLACE INTO observed VALUES (?,?,?,?)', (workspace, 'knowledge/a.md', digest, blob))
    return event, blob


def test_dry_run_and_atomic_conversion_preserve_api(migration):
    r, store = migration
    oid = commit(r, {'a': 'Original'})
    event, blob = legacy(r, store, 'Original')
    report = r.analytics_migrate()
    assert len(report['conversions']) == 1 and report['status'] == 'dry_run'
    assert store.blob_read(blob)['value'] == 'Original'
    report = r.analytics_migrate(apply=True)
    assert report['deleted_blobs'] == 1
    detail = store.read(event)
    assert detail['versions'][0]['after_blob'] is None
    assert detail['references'][0]['commit'] == oid
    assert detail['references'][0]['role'] == 'legacy_after'
    assert not r.analytics_migrate(apply=True)['conversions']


def test_unknown_unmatched_shared_and_other_context_are_preserved(migration):
    r, store = migration
    commit(r, {'a': 'Match'})
    _, shared = legacy(r, store, 'Match', shared=True)
    _, unknown = legacy(r, store, 'Absent')
    _, other = legacy(r, store, 'Match', workspace='other')
    report = r.analytics_migrate(apply=True)
    assert report['preserved_bodies'] == 1 and report['deleted_blobs'] == 0
    assert store.blob_read(shared)['value'] == 'Match'
    assert store.blob_read(unknown)['value'] == 'Absent'
    with store.connect() as db:
        assert db.execute('SELECT after_blob FROM versions WHERE event_id IN (SELECT id FROM events WHERE workspace=?)', ('other',)).fetchone()[0] == other


def test_redacted_git_content_requires_raw_digest_and_redacted_match(migration):
    r, store = migration
    text = 'Authorization: Bearer abcdefghijklmnop'
    commit(r, {'a': text})
    event, _ = legacy(r, store, text)
    wrong_event, wrong_blob = legacy(r, store, text, retained='Different')
    report = r.analytics_migrate(apply=True)
    assert len(report['conversions']) == 1 and report['preserved_bodies'] == 1
    assert store.read(event)['references'][0]['content_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert store.read(wrong_event)['versions'][0]['after_blob'] == wrong_blob


def test_keyset_bounds_cover_observed_and_gc_only_after_last_reference(migration):
    r, store = migration
    commit(r, {'a': 'Match'})
    _, blob = legacy(r, store, 'Match', observed=True)
    first = r.analytics_migrate(limit=1, apply=True)
    assert first['next_cursor'] and first['deleted_blobs'] == 0
    second = r.analytics_migrate(limit=1, cursor=first['next_cursor'], apply=True)
    assert second['examined_rows'] == 1 and second['deleted_blobs'] == 1
    assert second['conversions'][0]['side'] == 'observed'
    assert second['next_cursor'] is None
    with pytest.raises(Exception):
        store.blob_read(blob)


def test_bounded_historical_lookup_and_cursor_scope(migration):
    r, store = migration
    old = commit(r, {'a': 'Old'})
    commit(r, {'a': 'New'})
    legacy(r, store, 'Old')
    report = r.analytics_migrate(git_limit=1)
    assert not report['conversions'] or report['coverage']['git_candidates_examined'] <= 1
    report = r.analytics_migrate(git_limit=2)
    assert report['conversions'][0]['reference']['commit'] == old
    for _ in range(2):
        legacy(r, store, 'New')
    first = r.analytics_migrate(limit=1)
    token = json.loads(first['next_cursor'])
    token['context'] = '/other'
    with pytest.raises(Exception, match='cursor'):
        r.analytics_migrate(cursor=json.dumps(token))


def test_actual_workspace_migration_preserves_request_body(tmp_path):
    from pcw_runtime.cycles import CycleRuntime
    from test_runtime import Factory
    from test_workspace_v3 import create
    from test_history_lineage import save
    class Runtime(CycleRuntime):
        pass
    runtime = Runtime(tmp_path, 'context', provider_factory=Factory())
    try:
        create(runtime)
        cycle = runtime.startup('migration-test')['cycle_id']
        proposal, oid = save(runtime, cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'a', 'path': 'a.md', 'content': '# Body\nExact original.'}])
        text = runtime.history_read(commit=oid, material_id=proposal['created_ids']['a'])['materials'][0]['text']
        store = EventStore(tmp_path)
        event, blob = legacy(runtime, store, text, shared=True)
        report = runtime.analytics_migrate(apply=True)
        assert len(report['conversions']) == 1
        assert store.read(event)['references'][0]['commit'] == oid
        assert store.blob_read(blob)['value'] == text
    finally:
        runtime.close()
