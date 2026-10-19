"""Retry bodies survive failures and disappear only after verified Git capture."""
import json
from unittest.mock import patch

from test_history_lineage import history, save


def pending_apply(r, c, content):
    proposal = r.proposal_prepare(c, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'new', 'content': content}])
    result = r.proposal_apply(c, proposal['proposal_id'], proposal['revision'])
    return proposal, result


def test_compact_receipt_and_noop_remove_all_snapshot_bodies(history):
    r, c = history
    proposal, oid = save(r, c, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'a', 'content': '# A\nUnique permanent body.'}])
    path = r._history_root() / (proposal['proposal_id'] + '.json')
    compact = json.loads(path.read_text())
    assert set(compact) == {'status', 'commit', 'operation_id', 'revision'}
    assert 'Unique permanent body.' not in path.read_text()
    assert json.loads((r._history_root() / 'queue.json').read_text()) == []
    assert r._history_flush(proposal['proposal_id'])['commit'] == oid
    no_op = r._history_record('knowledge-noop', force=True)
    assert no_op['status'] == 'unchanged' and no_op['commit'] == oid
    assert set(json.loads((r._history_root() / 'knowledge-noop.json').read_text())) == set(compact)
    assert json.loads((r._history_root() / 'queue.json').read_text()) == []
    (r._history_root() / 'queue.json').unlink()
    assert r._history_flush(proposal['proposal_id'])['commit'] == oid


def test_two_pending_snapshots_keep_order_and_external_files(history):
    r, c = history
    git = r._history_git
    def unavailable(*args, **kwargs):
        if 'commit-tree' in args:
            raise OSError('offline')
        return git(*args, **kwargs)
    with patch.object(r, '_history_git', side_effect=unavailable):
        first, one = pending_apply(r, c, '# One\nFirst pending body.')
        second, two = pending_apply(r, c, '# Two\nSecond pending body.')
    assert one['history']['status'] == two['history']['status'] == 'pending'
    queue_path = r._history_root() / 'queue.json'
    assert json.loads(queue_path.read_text()) == [first['proposal_id'], second['proposal_id']]
    snapshots = [json.loads((r._history_root() / (p['proposal_id'] + '.json')).read_text()) for p in (first, second)]
    assert all('files' in s and 'event' in s for s in snapshots)
    target = r.engine.root / next(path for path in snapshots[1]['files'] if path.startswith('knowledge/'))
    target.write_text(target.read_text().replace('First pending body.', 'External edit preserved'))
    r.history_retry(limit=1)
    assert json.loads(queue_path.read_text()) == [second['proposal_id']]
    r.history_retry(limit=1)
    assert json.loads(queue_path.read_text()) == []
    assert 'External edit preserved' in target.read_text()
    for proposal, snapshot in zip((first, second), snapshots):
        receipt = json.loads((r._history_root() / (proposal['proposal_id'] + '.json')).read_text())
        assert 'files' not in receipt and 'event' not in receipt
        for path, text in snapshot['files'].items():
            assert r._history_git('show', receipt['commit'] + ':' + path).stdout == text


def test_legacy_completed_snapshot_is_verified_before_compaction(history):
    r, c = history
    proposal, oid = save(r, c, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'a', 'content': '# Body\nLegacy.'}])
    from workspace.runtime import _revision
    from workspace.workspaces import encode
    files = r._permanent_files()
    path = r._history_root() / (proposal['proposal_id'] + '.json')
    legacy = dict(status='committed', operation_id=proposal['proposal_id'], commit=oid,
                  files=files, revision=_revision(encode(files)))
    r.control.write(path, encode(legacy))
    r.control.write(r._history_root() / 'queue.json', encode([proposal['proposal_id']]))
    assert r.history_retry()['commit'] == oid
    assert 'files' not in json.loads(path.read_text())
    assert r._history_flush(proposal['proposal_id'])['commit'] == oid


def test_lost_ack_with_advanced_head_does_not_duplicate_commit(history):
    r, c = history
    git = r._history_git
    def lose_ack(*args, **kwargs):
        result = git(*args, **kwargs)
        if args[:2] == ('update-ref', 'HEAD'):
            raise OSError('lost acknowledgement')
        return result
    with patch.object(r, '_history_git', side_effect=lose_ack):
        proposal, result = pending_apply(r, c, '# Ack\nSaved before lost acknowledgement.')
    assert result['history']['status'] == 'pending'
    saved = r._history_revision('HEAD')
    tree = git('rev-parse', saved + '^{tree}').stdout.strip()
    advanced = git('commit-tree', tree, '-p', saved, text='External later metadata\n').stdout.strip()
    git('update-ref', 'HEAD', advanced, saved)
    r.history_retry()
    assert r._history_revision('HEAD') == advanced
    receipt = json.loads((r._history_root() / (proposal['proposal_id'] + '.json')).read_text())
    assert receipt['commit'] == saved and receipt['status'] == 'committed'
    assert 'files' not in receipt


def test_compaction_verification_failure_keeps_body_and_queue(history):
    import pytest
    from workspace.runtime import PCWError, _revision
    from workspace.workspaces import encode
    r, c = history
    proposal, oid = save(r, c, [{"description": "Verified behavior used to test historical replay and policy evidence", 'key': 'a', 'content': '# A\nActual Git body.'}])
    files = r._permanent_files()
    relative = next(path for path in files if path.startswith('knowledge/'))
    files[relative] += '\nUnverified retry bytes.'
    path = r._history_root() / (proposal['proposal_id'] + '.json')
    r.control.write(path, encode(dict(status='committed', operation_id=proposal['proposal_id'],
                                     commit=oid, files=files, revision=_revision(encode(files)))))
    queue = r._history_root() / 'queue.json'
    r.control.write(queue, encode([proposal['proposal_id']]))
    with pytest.raises(PCWError, match='content differs'):
        r.history_retry()
    assert 'Unverified retry bytes.' in path.read_text()
    assert json.loads(queue.read_text()) == [proposal['proposal_id']]


def test_catalog_failure_does_not_retain_verified_retry_body(history):
    r, c = history
    with patch.object(r, '_history_index_commit', side_effect=RuntimeError('catalog offline')):
        proposal, result = pending_apply(r, c, '# A\nGit owns this body.')
    receipt = result['history']
    assert receipt['status'] == 'committed' and receipt['catalog']['status'] == 'pending'
    stored = json.loads((r._history_root() / (proposal['proposal_id'] + '.json')).read_text())
    assert 'files' not in stored and 'event' not in stored
    assert json.loads((r._history_root() / 'queue.json').read_text()) == []
    assert r.history_index()['head'] == receipt['commit']
