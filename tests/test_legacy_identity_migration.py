"""Collision migration uses scoped source identities and immutable approvals."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from workspace.cycles import CycleRuntime, _json
from workspace.runtime import PCWError, _parse, _revision
from test_runtime import Factory
from test_shared_v4 import context, save

RULES = '# Human rules\nPreserve verified facts and request approval.\n'


def note(root, name, body, links=(), metadata=None):
    path = root / 'knowledge' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'links': list(links), **(metadata or {})}
    text = '---\n' + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + '---\n' + body
    path.write_bytes(text.encode('utf-8'))
    return {'path': 'knowledge/' + name, 'text': text, 'fingerprint': _revision(text)}


def legacy(tmp_path, name, *, links=(), version=0):
    root = tmp_path / '.pcw' / name
    (root / 'tmp').mkdir(parents=True)
    (root / 'INSTRUCTIONS.md').write_bytes(RULES.encode())
    note(root, 'index.md', '# ' + name + ' index\nOriginal.', links)
    note(root, 'workfile.md', '# ' + name + ' work\nOngoing.', ['index.md'])
    runtime = CycleRuntime(tmp_path, name, provider_factory=Factory())
    if version:
        with runtime.control.guard():
            runtime.control.write(runtime._format_path(), _json({'format_version': version}))
    return runtime


def mapping(plan, workspace, path='knowledge/index.md'):
    return next(i['new_id'] for i in plan['identity_map'] if i['workspace'] == workspace and i['path'] == path)


@pytest.mark.parametrize('order', [('alpha', 'beta', 'gamma'), ('gamma', 'alpha', 'beta')])
@pytest.mark.parametrize('version', [0, 1])
def test_three_contexts_scoped_map_local_links_and_order_independence(tmp_path, order, version):
    runtimes = {n: legacy(tmp_path, n, version=version) for n in order}
    try:
        note(runtimes['alpha'].engine.root, 'unique.md', '# Unique\nUnchanged identity.')
        plans = {n: r.upgrade_plan() for n, r in runtimes.items()}
        assert all(p['ready'] for p in plans.values()), plans
        expected = plans['alpha']['identity_map']
        assert len(expected) == 6
        assert all(p['identity_map'] == expected for p in plans.values())
        for n in order:
            r = runtimes[n]
            fresh = r.upgrade_plan()
            assert fresh['identity_map'] == expected
            ident = mapping(fresh, n)
            result = r.upgrade_apply(fresh['revision'], 'Migrate the reviewed identity map')
            assert result['status'] == 'complete'
            assert r.read(material_id='index.md')['id'] == ident
            work = r.read(material_id='workfile.md')
            assert r.engine.resolve(work['id']).links == [ident]
            assert r.read(material_id='index.md', from_id='workfile.md')['id'] == ident
            assert r.read(material_id=ident)['content'] == '# ' + n + ' index\nOriginal.'
            assert r.upgrade_apply(fresh['revision'], 'Retry')['replayed']
        assert runtimes['alpha'].read(material_id='unique.md')['id'] == 'unique.md'
        assert all(r.upgrade_plan()['compatible'] for r in runtimes.values())
    finally:
        for r in runtimes.values(): r.close()


def test_modern_incoming_native_path_and_outgoing_id_survive(tmp_path):
    current = context(tmp_path, 'current')
    r = legacy(tmp_path, 'old')
    other = legacy(tmp_path, 'other')
    try:
        target = save(current, '# Modern\nKeep stable.')
        incoming = save(current, '# Incoming\nOriginal.')
        source_path = current.engine.root / incoming['path']
        metadata, body = _parse(source_path.read_text(encoding='utf-8'))
        metadata['links'] = ['[[.pcw/old/knowledge/index.md|Старый вход]]']
        source_path.write_bytes(('---\n' + yaml.safe_dump(metadata, allow_unicode=True) + '---\n' + body).encode())
        original = source_path.read_bytes()
        note(r.engine.root, 'index.md', '# old index\nOriginal.', [target['id']])
        p = r.upgrade_plan(); assert p['ready'], p
        ident = mapping(p, 'old')
        assert r.upgrade_apply(p['revision'], 'Migrate')['status'] == 'complete'
        assert r.read(material_id=ident)['path'] == 'knowledge/index.md'
        assert source_path.read_bytes() == original
        assert current.read(material_id=incoming['id'])['id'] == incoming['id']
        assert current.engine.resolve(incoming['id']).links == [ident]
        assert r.engine.resolve(ident).links == [target['id']]
        assert current.read(material_id=target['id'])['id'] == target['id']
    finally:
        current.close(); r.close(); other.close()


def test_foreign_catalog_change_invalidates_review_before_any_write(tmp_path):
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        p = a.upgrade_plan(); original = (a.engine.knowledge / 'index.md').read_bytes()
        note(b.engine.root, 'extra.md', '# New foreign\nChanged dependency.')
        with pytest.raises(PCWError, match='changed since migration review'):
            a.upgrade_apply(p['revision'], 'Migrate')
        assert (a.engine.knowledge / 'index.md').read_bytes() == original
        assert not a._migration_receipt_path().exists()
    finally: a.close(); b.close()


@pytest.mark.parametrize('links,reason', [(['workfile.md'], 'Ambiguous'), (['[[.pcw/beta/knowledge/index.md]]'], 'requires migration first')])
def test_foreign_legacy_links_refuse_without_silent_selection(tmp_path, links, reason):
    a, b, c = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta'), legacy(tmp_path, 'gamma')
    try:
        # No local workfile: an unqualified reference has two possible foreign owners.
        (a.engine.knowledge / 'workfile.md').unlink()
        note(a.engine.root, 'index.md', '# Alpha\nUnchanged.', links)
        before = (a.engine.knowledge / 'index.md').read_bytes()
        p = a.upgrade_plan()
        assert not p['ready'] and any(reason in x['reason'] for x in p['blockers']), p
        assert (a.engine.knowledge / 'index.md').read_bytes() == before
    finally: a.close(); b.close(); c.close()


@pytest.mark.parametrize("semantic_conflict", [False, True])
def test_pending_proposal_overlay_retry_and_old_approval_remain_bound(tmp_path, semantic_conflict):
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        cycle, proposal = 'cycle', 'pending'
        original = (a.engine.knowledge / 'index.md').read_text(encoding='utf-8')
        before = {'path': 'knowledge/index.md', 'text': original, 'fingerprint': _revision(original), 'link_ids': []}
        after = {**before, 'text': original.replace('Original.', 'Reviewed later.')}
        after['fingerprint'] = _revision(after['text'])
        new_snapshot = note(a.engine.root, 'workfile.md', '# Pending new\nReviewed new fact.', ['index.md'])
        (a.engine.knowledge / 'workfile.md').unlink()
        plan = {'cycle_id': cycle, 'proposal_id': proposal, 'created_ids': {'new': 'workfile.md'}, 'task_id': 'index.md',
                'instructions_revision': _revision(RULES), 'knowledge_approval_required': True,
                'entries': {'index.md': {'before': before, 'after': after},
                            'workfile.md': {'before': None, 'after': new_snapshot}}}
        raw = _json(plan)
        path = a._plan_path(cycle, proposal)
        a.engine._write(path, raw); a.engine._write(path.with_suffix('.md'), a._review(plan))
        records = {'starts': {}, 'cycles': {cycle: {'state': 'active', 'task_id': 'index.md', 'proposals': {proposal: {'revision': _revision(raw), 'state': 'prepared'}}}}}
        with a.control.guard():
            a._persist(records)
        registry_before = a.record_path.read_bytes()
        p = a.upgrade_plan(); assert p['ready'], p
        ident = mapping(p, 'alpha')
        write = a.engine._write
        failed = False
        def interrupt(path, text):
            nonlocal failed
            write(path, text)
            if path.parent == a.engine.knowledge and not failed:
                failed = True
                raise OSError('lost acknowledgement')
        with patch.object(a.engine, '_write', side_effect=interrupt), pytest.raises(OSError):
            a.upgrade_apply(p['revision'], 'Migrate')
        assert a.upgrade_plan()['resume_revision'] == p['revision']
        assert a.upgrade_apply(p['revision'], 'Retry')['status'] == 'complete'
        assert path.read_text() == raw and a.record_path.read_bytes() == registry_before
        assert a.status(cycle)['task_id'] == ident
        overlay = a._execution_plan(plan)
        new_id = mapping(p, 'alpha', 'knowledge/workfile.md')
        assert set(overlay['entries']) == {ident, new_id} and overlay['created_ids']['new'] == new_id and overlay['task_id'] == ident
        if semantic_conflict:
            material = a.read(material_id=ident)
            current = a.engine.root / material['path']
            current.write_bytes(current.read_bytes() + b'Unapproved intervening content.')
            with pytest.raises(PCWError, match='changed|conflict'):
                a.proposal_apply(cycle, proposal, _revision(raw), confirmation='Original approval')
            assert path.read_text() == raw
            return
        assert a.proposal_apply(cycle, proposal, _revision(raw), confirmation='Approve the unchanged original review')['status'] == 'applied'
        assert 'Reviewed later.' in a.read(material_id='index.md')['content']
        assert a.read(material_id='workfile.md')['id'] == new_id
        assert a.engine.resolve(new_id).links == [ident]
        # Approval is still checked against the original immutable proposal bytes.
        changed = json.loads(raw)
        changed['entries']['index.md']['after']['text'] += 'Unapproved semantic change.'
        path.write_text(_json(changed), encoding='utf-8')
        with pytest.raises(PCWError, match='changed'):
            a.proposal_read(cycle, proposal)
    finally: a.close(); b.close()


def test_current_yaml_identity_is_preserved_and_never_shadowed_by_old_alias(tmp_path):
    current = context(tmp_path, 'current')
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        snap = note(current.engine.root, 'Stable modern.md', '# Modern stable\nExisting.', metadata={
            'id': 'index.md', 'type': 'knowledge', 'description': 'Existing global YAML identity.'})
        with a.control.guard():
            a._persist({'starts': {'old-task-start': 'task'}, 'cycles': {'task': {
                'state': 'active', 'task_id': 'index.md', 'proposals': {},
                'start_signature': _revision(_json(['work', None, 'explicit', 'index.md']))}}})
        original_registry = a.record_path.read_bytes()
        p = a.upgrade_plan(); assert p['ready'], p
        ident = mapping(p, 'alpha')
        assert a.upgrade_apply(p['revision'], 'Migrate')['status'] == 'complete'
        assert a.read(material_id=ident)['id'] == ident
        assert a.status('task')['task_id'] == ident
        resumed = a.startup('old-task-start', task_id='index.md')
        assert resumed['replayed'] and resumed['cycle_id'] == 'task' and resumed['task_id'] == ident
        assert a.record_path.read_bytes() == original_registry
        with pytest.raises(PCWError, match='conflicts with a current YAML ID'):
            a.read(material_id='index.md')
        assert current.read(material_id='index.md')['content'] == '# Modern stable\nExisting.'
        assert (current.engine.root / snap['path']).read_text(encoding='utf-8') == snap['text']
    finally: current.close(); a.close(); b.close()


def test_foreign_pending_creations_are_reserved_before_either_file_exists(tmp_path):
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    proposals = {}
    try:
        for r in (a, b):
            snapshot = note(r.engine.root, 'workfile.md', '# New ' + r.engine.workspace + '\nReviewed.', ['index.md'])
            (r.engine.knowledge / 'workfile.md').unlink()
            plan = {'cycle_id': 'cycle', 'proposal_id': 'new', 'created_ids': {'new': 'workfile.md'},
                    'instructions_revision': _revision(RULES), 'knowledge_approval_required': True,
                    'entries': {'workfile.md': {'before': None, 'after': snapshot}}}
            raw = _json(plan)
            p = r._plan_path('cycle', 'new')
            r.engine._write(p, raw); r.engine._write(p.with_suffix('.md'), r._review(plan))
            with r.control.guard():
                r._persist({'starts': {}, 'cycles': {'cycle': {'state': 'active', 'proposals': {'new': {'revision': _revision(raw), 'state': 'prepared'}}}}})
            proposals[r.engine.workspace] = _revision(raw)
        initial = a.upgrade_plan()
        assert initial['ready'] and initial['identity_map'] == b.upgrade_plan()['identity_map']
        for r in (a, b):
            p = r.upgrade_plan(); assert p['ready'], p
            assert p['identity_map'] == initial['identity_map']
            assert r.upgrade_apply(p['revision'], 'Migrate reviewed reservations')['status'] == 'complete'
        from types import SimpleNamespace
        reserved_id = mapping(initial, 'alpha', 'knowledge/workfile.md')
        with a.engine._guard(), patch('workspace.shared.uuid.uuid4', side_effect=[
            SimpleNamespace(hex=reserved_id.removesuffix('.md')), SimpleNamespace(hex='f' * 32)]):
            assert a.engine._allocate_material_id() == 'f' * 32 + '.md'
        for r in (a, b):
            assert r.proposal_apply('cycle', 'new', proposals[r.engine.workspace], confirmation='Approve reviewed new fact')['status'] == 'applied'
            assert r.read(material_id='workfile.md')['id'] == mapping(initial, r.engine.workspace, 'knowledge/workfile.md')
    finally: a.close(); b.close()


def test_identity_map_survives_supported_root_relocation(tmp_path):
    from contextopus.core import relocation
    source, destination = tmp_path / 'source', tmp_path / 'moved'
    source.mkdir()
    a, b = legacy(source, 'alpha'), legacy(source, 'beta')
    expected = a.upgrade_plan()['identity_map']
    a.close(); b.close()
    relocation.apply(relocation.plan(source, destination))
    moved = CycleRuntime(source, 'alpha', provider_factory=Factory())
    try:
        plan = moved.upgrade_plan()
        assert plan['ready'] and plan['identity_map'] == expected
        assert moved.engine.work_root == destination
    finally: moved.close()


def test_changed_retained_map_blocks_alias_and_reviewed_foreign_migration(tmp_path):
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        p = a.upgrade_plan()
        assert a.upgrade_apply(p['revision'], 'Migrate')['status'] == 'complete'
        preview = b.upgrade_plan(); assert preview['ready'], preview
        original = (b.engine.knowledge / 'index.md').read_bytes()
        path = a._migration_receipt_path()
        receipt = json.loads(path.read_text())
        receipt['identity_map'][0]['new_id'] = 'unreviewed-target'
        path.write_text(_json(receipt), encoding='utf-8')
        with pytest.raises(PCWError, match='Invalid retained deterministic'):
            a.read(material_id='index.md')
        with pytest.raises(PCWError, match='changed since migration review'):
            b.upgrade_apply(preview['revision'], 'Migrate old preview')
        assert (b.engine.knowledge / 'index.md').read_bytes() == original
        assert not b._migration_receipt_path().exists()
    finally: a.close(); b.close()


@pytest.mark.parametrize('simulate_case_alias', [False, True])
def test_case_only_filename_preserves_graph_backup_and_retry(tmp_path, monkeypatch, simulate_case_alias):
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        original = note(a.engine.root, 'unique.md', '# Unique\nUnchanged identity.', ['index.md'])
        note(a.engine.root, 'workfile.md', '# Work\nUses unique.', ['unique.md'])
        source, destination = a.engine.knowledge / 'unique.md', a.engine.knowledge / 'Unique.md'
        native_alias = destination.is_file() and destination.samefile(source)
        is_file, samefile = Path.is_file, Path.samefile
        if simulate_case_alias and not native_alias:
            def alias_file(path):
                return is_file(source) if str(path) == str(destination) else is_file(path)
            def alias_identity(path, other):
                if str(path) == str(destination) and str(other) == str(source):
                    return True
                return samefile(path, other)
            monkeypatch.setattr(Path, 'is_file', alias_file)
            monkeypatch.setattr(Path, 'samefile', alias_identity)
        removed = []
        unlink = Path.unlink
        def record_unlink(path, *args, **kwargs):
            if str(path) in {str(source), str(destination)}:
                removed.append(str(path))
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', record_unlink)
        preview = a.upgrade_plan()
        assert preview['ready'], preview
        expected_path = 'knowledge/unique.md' if native_alias or simulate_case_alias else 'knowledge/Unique.md'
        transforms, _ = a._v2_transforms(0)
        assert transforms['knowledge/unique.md']['path'] == expected_path
        result = a.upgrade_apply(preview['revision'], 'Preserve the reviewed physical source')
        assert result['status'] == 'complete'
        material = a.read(material_id='unique.md')
        assert material['path'] == expected_path and material['content'] == '# Unique\nUnchanged identity.'
        assert a.engine.resolve('unique.md').links == [mapping(preview, 'alpha')]
        work = mapping(preview, 'alpha', 'knowledge/workfile.md')
        assert a.engine.resolve(work).links == ['unique.md']
        assert (Path(result['backup']) / 'knowledge/unique.md').read_bytes() == original['text'].encode('utf-8')
        assert a.upgrade_apply(preview['revision'], 'Retry the exact migration')['replayed']
        if native_alias or simulate_case_alias:
            assert not removed
            assert 'unique.md' in {path.name for path in a.engine.knowledge.iterdir()}
    finally: a.close(); b.close()


def test_case_only_destination_keeps_distinct_directory_entries(tmp_path):
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        note(a.engine.root, 'unique.md', '# Unique\nSource body.')
        source, destination = a.engine.knowledge / 'unique.md', a.engine.knowledge / 'Unique.md'
        distinct = not destination.exists()
        if distinct:
            note(a.engine.root, 'Unique.md', '# Destination\nDistinct body.')
            assert not source.samefile(destination)
        else:
            assert source.samefile(destination)
        preview = a.upgrade_plan()
        assert preview['ready'], preview
        transforms, _ = a._v2_transforms(0)
        if distinct:
            assert transforms['knowledge/unique.md']['path'] != 'knowledge/Unique.md'
            assert len({item['id'] for item in transforms.values()}) == len(transforms)
        else:
            assert transforms['knowledge/unique.md']['path'] == 'knowledge/unique.md'
        assert a.upgrade_apply(preview['revision'], 'Preserve distinct physical records')['status'] == 'complete'
        assert a.read(material_id='unique.md')['content'] == '# Unique\nSource body.'
        if distinct:
            assert a.read(material_id='Unique.md')['content'] == '# Destination\nDistinct body.'
    finally: a.close(); b.close()


@pytest.mark.parametrize('pending,bad_expected', [(False, False), (True, False), (True, True)])
def test_exact_foreign_wikilink_is_not_realiased_to_local_legacy_id(tmp_path, pending, bad_expected):
    current = context(tmp_path, 'current')
    a, b = legacy(tmp_path, 'alpha'), legacy(tmp_path, 'beta')
    try:
        note(current.engine.root, 'Stable modern.md', '# Foreign canonical\nRetain this target.', metadata={
            'id': 'index.md', 'type': 'knowledge', 'description': 'A stable foreign canonical identity.'})
        native = '[[.pcw/current/knowledge/Stable modern.md|Точная внешняя ссылка]]'
        before = note(a.engine.root, 'workfile.md', '# Work\nUses the foreign target.', [native])
        raw = None
        if pending:
            before['link_ids'] = ['unreviewed-target.md'] if bad_expected else ['index.md']
            after = {**before, 'text': before['text'].replace('Uses the foreign target.', 'Reviewed foreign target.')}
            after['fingerprint'] = _revision(after['text'])
            plan = {'cycle_id': 'cycle', 'proposal_id': 'foreign', 'created_ids': {},
                    'instructions_revision': _revision(RULES), 'knowledge_approval_required': True,
                    'entries': {'workfile.md': {'before': before, 'after': after}},
                    'link_targets': {'index.md': {'id': 'index.md', 'workspace': 'current',
                                                'path': 'knowledge/Stable modern.md', 'title': 'Foreign canonical'}}}
            raw = _json(plan)
            path = a._plan_path('cycle', 'foreign')
            a.engine._write(path, raw); a.engine._write(path.with_suffix('.md'), a._review(plan))
            with a.control.guard():
                a._persist({'starts': {}, 'cycles': {'cycle': {'state': 'active', 'proposals': {
                    'foreign': {'revision': _revision(raw), 'state': 'prepared'}}}}})
        preview = a.upgrade_plan()
        if bad_expected:
            assert not preview['ready'] and any('reviewed target IDs' in item['reason'] for item in preview['blockers'])
            assert (a.engine.knowledge / 'workfile.md').read_text(encoding='utf-8') == before['text']
            assert path.read_text(encoding='utf-8') == raw
            return
        assert preview['ready'], preview
        local_index = mapping(preview, 'alpha')
        work = mapping(preview, 'alpha', 'knowledge/workfile.md')
        assert a.upgrade_apply(preview['revision'], 'Preserve the exact foreign target')['status'] == 'complete'
        assert a.engine.resolve(work).links == ['index.md']
        assert a.engine.resolve(work).links != [local_index]
        text = (a.engine.root / a.read(material_id=work)['path']).read_text(encoding='utf-8')
        assert _parse(text)[0]['links'] == [native]
        if pending:
            assert path.read_text(encoding='utf-8') == raw
            overlay = a._execution_plan(plan)
            assert overlay['entries'][work]['after']['link_ids'] == ['index.md']
            assert overlay['link_targets']['index.md']['workspace'] == 'current'
            assert overlay['link_targets']['index.md']['path'] == 'knowledge/Stable modern.md'
            assert a.proposal_apply('cycle', 'foreign', _revision(raw), confirmation='Approve this exact original review')['status'] == 'applied'
            assert a.engine.resolve(work).links == ['index.md']
            assert 'Reviewed foreign target.' in a.read(material_id=work)['content']
        assert current.read(material_id='index.md')['content'] == '# Foreign canonical\nRetain this target.'
    finally: current.close(); a.close(); b.close()
