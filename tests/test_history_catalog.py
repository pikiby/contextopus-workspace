"""Actual Git repositories exercise the rebuildable catalog without a RAG backend."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from workspace.history import HistoryMixin
from workspace.history_events import commit_message
from workspace.runtime import PCWError


@pytest.fixture
def history(tmp_path):
    from workspace.analytics import EventStore
    store = EventStore(tmp_path)
    store.storage_apply(store.storage_prepare()["revision"])
    class Runtime(HistoryMixin):
        def _require_format(self):
            pass
    r = Runtime()
    root = tmp_path / 'context'
    root.mkdir()
    r.engine = SimpleNamespace(root=root, work_root=tmp_path,
        _assert_safe=lambda p: None, _own_git=lambda: None,
        _knowledge_path=lambda p: root / 'knowledge' / p)
    r.control = SimpleNamespace(guard=nullcontext)
    r._history_git('init', '.')
    (root / 'knowledge').mkdir()
    return r


def commit(r, changes, transformations=()):
    event_changes = []
    for ident, text in changes.items():
        path = r.engine.root / 'knowledge' / (ident + '.md')
        relative = path.relative_to(r.engine.root).as_posix()
        before = {'path': relative, 'revision': hashlib.sha256(path.read_bytes()).hexdigest()} if path.exists() else None
        if text is None:
            path.unlink()
        else:
            path.write_bytes(text.encode('utf-8'))
        after = {'path': relative, 'revision': hashlib.sha256(path.read_bytes()).hexdigest()} if path.exists() else None
        event_changes.append(dict(id=ident, before=before, after=after))
    event = dict(version=1, operation_id='knowledge-test', kind='knowledge', changes=event_changes,
                 transformations=list(transformations))
    r._history_git('add', 'knowledge')
    r._history_git('commit', '-m', commit_message(event))
    oid = r._history_revision('HEAD')
    r._history_index_commit(oid)
    return oid


def test_index_cursor_and_exact_reference(history):
    r = history
    commit(r, {'a': '# Secret\nVersioned body.'})
    oid = commit(r, {'b': '# Another\nBody.'})
    digest = hashlib.sha256(b'# Secret\nVersioned body.').hexdigest()
    assert r._history_content_reference('knowledge/a.md', digest)['commit'] == oid
    assert r._history_content_reference('knowledge/a.md', '0' * 64)['status'] == 'unversioned'
    result = r.history_index(limit=1)
    assert len(result['indexed']) == 1 and result['next_cursor']
    assert r.history_index(cursor=result['next_cursor'], limit=1)['coverage']['reached_root']
    with pytest.raises(PCWError):
        r.history_index(cursor='{}')
    commit(r, {'c': 'New'})
    with pytest.raises(PCWError, match='advanced'):
        r.history_index(cursor=result['next_cursor'])
    with r._history_catalog() as db:
        assert 'Versioned body.' not in str([tuple(x) for x in db.execute('SELECT * FROM history_commits')])


def test_merge_retained_split_and_version_traversal(history):
    r = history
    original = commit(r, {'a': 'A', 'b': 'B'})
    merged = commit(r, {'a': None, 'b': None, 'c': 'C'},
                    [dict(kind='merge', sources=['a', 'b'], targets=['c'])])
    split = commit(r, {'c': 'C rewritten', 'd': 'D'},
                   [dict(kind='split', sources=['c'], targets=['c', 'd'])])
    graph = r.history_lineage('d')
    assert len([e for e in graph['edges'] if e['kind'] == 'merge']) == 2
    edge = next(e for e in graph['edges'] if e['kind'] == 'split')
    assert edge['source_commit'] == merged and edge['target_commit'] == split
    descendants = r.history_lineage('a', direction='descendants', start_commit=original)
    assert len([e for e in descendants['edges'] if e['kind'] == 'split']) == 2
    assert len(r.history_lineage('d', limit=1)['edges']) <= 1


def test_catalog_failure_is_separate_from_git_receipt(history):
    r = history
    oid = commit(r, {'a': 'Retry'})
    with patch.object(r, '_history_index_commit', side_effect=RuntimeError('offline')):
        assert r._history_catalog_receipt(dict(status='committed', commit=oid))['status'] == 'pending'
    assert r.history_index()['head'] == oid
    assert r._history_revision('HEAD') == oid


def test_external_commit_has_unknown_provenance(history):
    r = history
    commit(r, {'a': 'A'})
    (r.engine.root / 'knowledge/a.md').write_text('External')
    r._history_git('add', 'knowledge')
    r._history_git('commit', '-m', 'external')
    oid = r._history_revision('HEAD')
    r.history_index()
    with r._history_catalog() as db:
        import json
        meta = json.loads(db.execute('SELECT metadata FROM history_commits WHERE oid=?', (oid,)).fetchone()[0])
        assert meta['provenance'] == 'unknown'


def test_real_workspace_catalog_flush_and_retained_split(tmp_path):
    from pcw_runtime.cycles import CycleRuntime
    from test_runtime import Factory
    from test_workspace_v3 import create
    from test_history_lineage import save
    runtime = CycleRuntime(tmp_path, 'catalog', provider_factory=Factory())
    try:
        create(runtime)
        cycle = runtime.startup('catalog-test')['cycle_id']
        first, old = save(runtime, cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'a', 'content': '# Source\nOriginal body.'}])
        ident = first['created_ids']['a']
        source = runtime.read(material_id=ident)
        result, new = save(runtime, cycle, [
            {"description": "Verified behavior used to test historical replay and policy evidence", 'material_id': ident, 'expected_revision': source['revision'], 'content': '# Source\nRetained body.'},
            {"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'b', 'content': '# Target\nSplit body.'}],
            transformations=[dict(kind='split', sources=[ident], targets=[ident, '@b'])])
        graph = runtime.history_lineage(result['created_ids']['b'])
        edge = next(e for e in graph['edges'] if e['kind'] == 'split')
        assert (edge['source_commit'], edge['target_commit']) == (old, new)
        assert runtime.history_index(limit=1)['indexed'] == [new]
    finally:
        runtime.close()


def test_dirty_reviewed_source_does_not_claim_git_lineage(history):
    r = history
    commit(r, {'a': 'Git state'})
    # The reviewed source is dirty and has never existed in Git.
    (r.engine.root / 'knowledge/a.md').write_text('Unsaved source')
    oid = commit(r, {'a': None, 'b': 'Replacement'},
                 [dict(kind='replace', sources=['a'], targets=['b'])])
    assert not r.history_lineage('b')['edges']
    with r._history_catalog() as db:
        import json
        metadata = json.loads(db.execute('SELECT metadata FROM history_commits WHERE oid=?', (oid,)).fetchone()[0])
        assert metadata['source_mismatches'] == ['a']


def test_native_rename_preserves_declared_identity(history):
    r = history
    old = commit(r, {'a': 'Body'})
    path = r.engine.root / 'knowledge/a.md'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.rename(path.with_name('Renamed.md'))
    event = dict(version=1, operation_id='knowledge-rename', kind='knowledge',
                 changes=[dict(id='a', before=dict(path='knowledge/a.md', revision=digest),
                               after=dict(path='knowledge/Renamed.md', revision=digest))], transformations=[])
    r._history_git('add', 'knowledge')
    r._history_git('commit', '-m', commit_message(event))
    new = r._history_revision('HEAD')
    r.history_index()
    edge = r.history_lineage('a')['edges'][0]
    assert (edge['source_commit'], edge['target_commit']) == (old, new)


def test_unchanged_retained_source_is_a_new_declared_version(history):
    r = history
    body = '---\nid: a\n---\nA'
    old = commit(r, {'a': body})
    split = commit(r, {'b': 'B'}, [dict(kind='split', sources=['a'], targets=['a', 'b'])])
    new = commit(r, {'a': body + ' revised'})
    graph = r.history_lineage('a')
    assert any(e['kind'] == 'revision' and e['source_commit'] == split and e['target_commit'] == new for e in graph['edges'])
    assert any(e['kind'] == 'split' and e['source_commit'] == old for e in graph['edges'])


def test_lineage_dates_filter_before_limit_and_normalize_utc(history):
    import json
    r = history
    source = commit(r, {'a': 'A'})
    commits = [commit(r, {'b': str(i)}) for i in range(5)]
    chosen = sorted(commits)[-1]
    with r._history_catalog() as db:
        for oid in commits:
            metadata = json.loads(db.execute('SELECT metadata FROM history_commits WHERE context=? AND oid=?', (r._history_context, oid)).fetchone()[0])
            metadata['time'] = '2026-10-15T12:00:00+00:00' if oid == chosen else '2020-01-01T00:00:00+00:00'
            db.execute('UPDATE history_commits SET metadata=? WHERE context=? AND oid=?', (json.dumps(metadata), r._history_context, oid))
            db.execute('INSERT INTO history_edges VALUES (?,?,?,?,?,?,?,?)',
                       (r._history_context, oid, 10, 'replace', 'a', source, 'b', oid))
            # Another context must never enter the selected graph.
            db.execute('INSERT INTO history_edges VALUES (?,?,?,?,?,?,?,?)',
                       ('other-context', oid, 10, 'replace', 'a', source, 'b', oid))
    graph = r.history_lineage('a', direction='descendants', start_commit=source,
                              since='2026-10-15T08:00:00-03:00', until='2026-10-15T10:00:00-03:00', limit=1)
    assert [e['oid'] for e in graph['edges']] == [chosen]
    assert all(e['context'] == r._history_context for e in graph['edges'])
    for since, until in [('2026-01-01', '2025-01-01'), ('2026-01-01', '2026-01-01'),
                         ('2026-01-01T01:00:00+01:00', '2026-01-01T00:00:00Z')]:
        with pytest.raises(PCWError, match='precede'):
            r.history_lineage('a', since=since, until=until)


def test_exact_depth_leaf_and_last_version_are_not_truncated(history):
    r = history
    first = commit(r, {'a': 'A'})
    second = commit(r, {'a': 'A2'})
    third = commit(r, {'a': 'A3'})
    graph = r.history_lineage('a', max_depth=2, limit=2)
    assert graph['start_commit'] == third
    assert len(graph['edges']) == 2 and not graph['truncated']
    assert len({n['commit'] for n in graph['nodes']}) == 3
    assert r.history_lineage('a', max_depth=1)['truncated']
    assert not r.history_lineage('a', start_commit=first, max_depth=0)['truncated']
    assert r.history_lineage('a', start_commit=second, max_depth=0)['truncated']


def test_source_mismatch_does_not_insert_unsupported_before_version(history):
    r = history
    old = commit(r, {'a': 'Git'})
    with r._history_catalog() as db:
        db.execute('DELETE FROM history_versions')
    (r.engine.root / 'knowledge/a.md').write_text('Dirty')
    commit(r, {'a': 'New'})
    with r._history_catalog() as db:
        assert not db.execute('SELECT 1 FROM history_versions WHERE context=? AND oid=? AND material=?',
                              (r._history_context, old, 'a')).fetchone()
        indexes = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {'history_edges_source', 'history_edges_target', 'history_versions_material'} <= indexes


def test_declared_target_fingerprint_must_match_git_version(history):
    r = history
    commit(r, {'a': 'A'})
    path = r.engine.root / 'knowledge/a.md'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text('Actual new')
    event = dict(version=1, operation_id='knowledge-invalid-target', kind='knowledge',
                 changes=[dict(id='a', before=dict(path='knowledge/a.md', revision=digest),
                               after=dict(path='knowledge/a.md', revision='0' * 64))], transformations=[])
    r._history_git('add', 'knowledge')
    r._history_git('commit', '-m', commit_message(event))
    oid = r._history_revision('HEAD')
    r._history_index_commit(oid)
    with r._history_catalog() as db:
        assert not db.execute('SELECT 1 FROM history_versions WHERE context=? AND oid=?', (r._history_context, oid)).fetchone()
    assert not r.history_lineage('a')['edges']


def test_exact_fixture_bytes_preserve_lineage_with_git_autocrlf(history):
    r = history
    r._history_git('config', 'core.autocrlf', 'true')
    body = '---\nid: a\n---\nA'
    old = commit(r, {'a': body})
    assert (r.engine.root / 'knowledge/a.md').read_bytes() == body.encode('utf-8')
    assert r._history_git('show', old + ':knowledge/a.md').stdout == body
    split = commit(r, {'b': 'B'}, [dict(kind='split', sources=['a'], targets=['a', 'b'])])
    new = commit(r, {'a': body + ' revised'})
    graph = r.history_lineage('a')
    assert any(e['kind'] == 'revision' and e['source_commit'] == split and e['target_commit'] == new for e in graph['edges'])
    assert any(e['kind'] == 'split' and e['source_commit'] == old for e in graph['edges'])


def test_git_newline_normalization_is_not_claimed_as_exact_raw_version(history):
    import json
    r = history
    r._history_git('config', 'core.autocrlf', 'true')
    oid = commit(r, {'a': '---\r\nid: a\r\n---\r\nA'})
    with r._history_catalog() as db:
        metadata = json.loads(db.execute('SELECT metadata FROM history_commits WHERE context=? AND oid=?',
                                        (r._history_context, oid)).fetchone()[0])
    assert metadata['target_mismatches'] == ['a']
    raw_digest = hashlib.sha256((r.engine.root / 'knowledge/a.md').read_bytes()).hexdigest()
    assert r._history_content_reference('knowledge/a.md', raw_digest)['status'] == 'unversioned'


def test_automatic_history_preserves_crlf_bytes_despite_git_eol_filters(tmp_path):
    from pcw_runtime.cycles import CycleRuntime
    from test_runtime import Factory
    from test_workspace_v3 import create
    from test_history_lineage import save
    runtime = CycleRuntime(tmp_path, 'raw-history', provider_factory=Factory())
    try:
        create(runtime)
        cycle = runtime.startup('raw-history-test')['cycle_id']
        proposal, _ = save(runtime, cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'a', 'content': '# Body\nRaw snapshot body.'}])
        material = runtime.read(material_id=proposal['created_ids']['a'])
        path = runtime.engine.root / material['path']
        raw = path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        path.write_bytes(raw)
        runtime._history_git('config', 'core.autocrlf', 'true')
        (runtime.engine.root / '.gitattributes').write_bytes(b'*.md text eol=lf\n')
        receipt = runtime._history_record('knowledge-crlf-raw', force=True)
        assert receipt['status'] == 'committed'
        assert runtime._history_git('show', receipt['commit'] + ':' + material['path']).stdout.encode('utf-8') == raw
        reference = runtime._history_content_reference(material['path'], hashlib.sha256(raw).hexdigest())
        assert reference['status'] == 'versioned'
        assert reference['commit'] == receipt['commit']
        stored = json.loads((runtime._history_root() / 'knowledge-crlf-raw.json').read_text())
        assert 'files' not in stored  # Compaction also verified exact CRLF replay.
        assert path.read_bytes() == raw
    finally:
        runtime.close()
