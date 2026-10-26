"""Real SQL/files with independent cycles, restarts and competing processes."""
import json
import multiprocessing
from pathlib import Path

import pytest

from workspace.cycles import CycleRuntime
from workspace.runtime import PCWError, _revision
from workspace.edit_reservations import EditReservations
from test_runtime import Factory
from test_workspace_v3 import create


@pytest.fixture
def work(tmp_path):
    runtime = CycleRuntime(tmp_path, 'project', provider_factory=Factory())
    create(runtime, knowledge_approval_required=False, cleanup_approval_required=False)
    opened = runtime.startup('seed')
    cycle = opened['cycle_id']
    proposal = runtime.proposal_prepare(cycle, [
        {'key': key, 'content': '# ' + key, 'description': 'Verified ' + key + ' information.'}
        for key in ('Alpha', 'Beta', 'Gamma')], expected_task_revision=opened['task_revision'])
    runtime.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'])
    cards = {key: runtime.read(material_id=ident) for key, ident in proposal['created_ids'].items()}
    a, b = (runtime.startup(key)['cycle_id'] for key in ('worker-a', 'worker-b'))
    yield runtime, cards, a, b
    runtime.close()


def change(card, **fields):
    return {'material_id': card['id'], 'expected_revision': card['revision'], **fields}


def save(runtime, cycle, proposal):
    return runtime.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'])


def test_owner_reason_and_reads_are_available_but_competing_changes_fail(work):
    r, cards, a, b = work
    card = cards['Alpha']
    reserved = r.reserve(a, [card['id']], 'Update Windows installation commands')
    assert reserved['reservations'][0]['cycle_id'] == a
    assert r.read(material_id=card['id'])['content'] == card['content']
    assert r.search(query='Alpha')['results']
    for fields in ({'delete': True}, {'path': 'Renamed.md'}, {'links': [cards['Beta']['id']]}):
        with pytest.raises(PCWError, match='Update Windows installation commands'):
            r.proposal_prepare(b, [change(card, **fields)], expected_task_revision=r.status(b)['task_revision'])
    other = r.proposal_prepare(b, [change(cards['Beta'], status='verified')], expected_task_revision=r.status(b)['task_revision'])
    assert save(r, b, other)['status'] == 'applied'
    own = r.proposal_prepare(a, [change(card, status='verified')], expected_task_revision=r.status(a)['task_revision'])
    assert save(r, a, own)['status'] == 'applied'
    assert not r.reservations()['reservations']


def test_batch_is_all_or_nothing_and_release_checks_owner(work):
    r, cards, a, b = work
    held = r.reserve(a, [cards['Beta']['id']], 'Check Beta')['reservations'][0]
    with pytest.raises(PCWError, match='reserved'):
        r.reserve(b, [cards['Alpha']['id'], cards['Beta']['id']], 'Update both')
    assert r.reservations(cycle_id=b)['total'] == 0
    with pytest.raises(PCWError, match='another cycle'):
        r.reservation_release(b, held['reservation_id'])
    r.reservation_release(a, held['reservation_id'])
    assert r.reserve(b, [cards['Alpha']['id'], cards['Beta']['id']], 'Update both')['status'] == 'reserved'


def test_expiry_fences_old_proposal_even_if_same_cycle_reacquires(work, monkeypatch):
    r, cards, a, b = work
    clock = [1000.0]
    monkeypatch.setattr('workspace.edit_reservations.time.time', lambda: clock[0])
    held = r.reserve(a, [cards['Alpha']['id']], 'Check Alpha', ttl_seconds=10)['reservations'][0]
    p = r.proposal_prepare(a, [change(cards['Alpha'], status='verified')], expected_task_revision=r.status(a)['task_revision'])
    clock[0] += 11
    with pytest.raises(PCWError, match='expired'):
        r.reservation_renew(a, held['reservation_id'])
    assert r.reservations()['total'] == 0
    r.reserve(a, [cards['Alpha']['id']], 'Restart Alpha')
    with pytest.raises(PCWError, match='expired, released or replaced'):
        save(r, a, p)
    revised = r.proposal_prepare(a, [change(r.read(material_id=cards['Alpha']['id']), status='verified')],
                                 proposal_id=p['proposal_id'], expected_task_revision=r.status(a)['task_revision'])
    assert save(r, a, revised)['status'] == 'applied'


def test_expiry_allows_other_cycle_without_stale_release(work, monkeypatch):
    r, cards, a, b = work
    clock = [1000.0]
    monkeypatch.setattr('workspace.edit_reservations.time.time', lambda: clock[0])
    old = r.reserve(a, [cards['Alpha']['id']], 'A', ttl_seconds=5)['reservations'][0]
    clock[0] += 6
    new = r.reserve(b, [cards['Alpha']['id']], 'B')['reservations'][0]
    r.reservation_release(a, old['reservation_id'])
    assert r.reservations()['reservations'][0]['reservation_id'] == new['reservation_id']


def test_renew_does_not_invalidate_prepared_review(work, monkeypatch):
    r, cards, a, b = work
    clock = [1000.0]
    monkeypatch.setattr('workspace.edit_reservations.time.time', lambda: clock[0])
    held = r.reserve(a, [cards['Alpha']['id']], 'A', ttl_seconds=10)['reservations'][0]
    p = r.proposal_prepare(a, [change(cards['Alpha'], status='verified')], expected_task_revision=r.status(a)['task_revision'])
    clock[0] += 5
    r.reservation_renew(a, held['reservation_id'], ttl_seconds=20)
    clock[0] += 10
    assert save(r, a, p)['status'] == 'applied'


def test_automatic_link_changes_join_the_same_atomic_reservation(work):
    r, cards, a, b = work
    p = r.proposal_prepare(a, [change(cards['Beta'], links=[cards['Alpha']['id']])], expected_task_revision=r.status(a)['task_revision'])
    save(r, a, p)
    held = r.reserve(b, [cards['Beta']['id']], 'Maintain the referring card')['reservations'][0]
    with pytest.raises(PCWError, match='Maintain the referring card'):
        r.proposal_prepare(a, [change(cards['Alpha'], path='Renamed.md')], expected_task_revision=r.status(a)['task_revision'])
    assert r.reservations(cycle_id=a)['total'] == 0
    r.reservation_release(b, held['reservation_id'])
    p = r.proposal_prepare(a, [change(cards['Alpha'], path='Renamed.md')], expected_task_revision=r.status(a)['task_revision'])
    assert {x['material_id'] for x in p['reservations']} == {cards['Alpha']['id'], cards['Beta']['id']}
    assert save(r, a, p)['status'] == 'applied'


def test_external_edit_still_triggers_revision_conflict(work):
    r, cards, a, b = work
    card = cards['Alpha']
    p = r.proposal_prepare(a, [change(card, status='verified')], expected_task_revision=r.status(a)['task_revision'])
    path = r.engine.root / card['path']
    path.write_bytes(path.read_bytes() + b'\nExternal editor change.\n')
    with pytest.raises(PCWError, match='conflict'):
        save(r, a, p)
    assert 'External editor change.' in r.read(material_id=card['id'])['content']


def test_internal_update_delete_rename_and_migration_respect_reservation(work):
    r, cards, a, b = work
    card = cards['Alpha']
    r.reserve(a, [card['id']], 'Active editing')
    path = r.engine.root / card['path']
    before = path.read_bytes()
    for action in (lambda: r.engine.update(card['id'], 'Overwrite'),
                   lambda: r.engine.rename(card['id'], 'Other.md'),
                   lambda: r.engine.links(card['id'], add=[cards['Beta']['id']]),
                   lambda: r.upgrade_apply('irrelevant', 'Check migration')):
        with pytest.raises(PCWError, match='reserved'):
            action()
    with pytest.raises(PCWError, match='reserved'):
        r.engine.delete(card['id'])
    assert path.read_bytes() == before


def test_discard_and_finish_release_only_their_own_claims(work):
    r, cards, a, b = work
    p = r.proposal_prepare(a, [change(cards['Alpha'], status='verified')], expected_task_revision=r.status(a)['task_revision'])
    r.reserve(b, [cards['Beta']['id']], 'B')
    r.proposal_discard(a, p['proposal_id'], p['revision'])
    assert r.reservations(cycle_id=a)['total'] == 0
    r.reserve(a, [cards['Gamma']['id']], 'A')
    assert r.finish(a)['status'] == 'closed'
    assert r.reservations(cycle_id=a)['total'] == 0
    assert r.reservations(cycle_id=b)['total'] == 1


def test_restart_preserves_ownership_and_does_not_hold_sql_transaction(work):
    r, cards, a, b = work
    r.reserve(a, [cards['Alpha']['id']], 'Persist across process restarts')
    with r.control.storage.connect() as db:
        db.execute('CREATE TABLE unrelated_probe (id INTEGER)')
        db.execute('INSERT INTO unrelated_probe VALUES (1)')
    other = CycleRuntime(r.engine.work_root, 'project', provider_factory=Factory())
    try:
        assert other.reservations()['total'] == 1
        with pytest.raises(PCWError, match='Persist across process restarts'):
            other.reserve(b, [cards['Alpha']['id']], 'B')
    finally:
        other.close()


def _compete(root, cycle, card, start, results):
    runtime = CycleRuntime(Path(root), 'project', provider_factory=Factory())
    try:
        start.wait(20)
        try:
            runtime.reserve(cycle, [card], 'Concurrent process')
            results.put('reserved')
        except PCWError as exc:
            results.put('blocked' if 'Materials reserved' in str(exc) else str(exc))
    finally:
        runtime.close()


def test_two_processes_cannot_both_reserve(work):
    r, cards, a, b = work
    ctx = multiprocessing.get_context('spawn')
    start, results = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_compete, args=(str(r.engine.work_root), cycle,
                 cards['Alpha']['id'], start, results)) for cycle in (a, b)]
    try:
        for process in processes:
            process.start()
        start.set()
        assert sorted(results.get(timeout=40) for _ in processes) == ['blocked', 'reserved']
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        results.close()
        results.join_thread()


def test_sql_v1_upgrade_preserves_existing_rows_and_requires_explicit_apply(tmp_path):
    import shutil
    from workspace.analytics import EventStore
    store = EventStore(tmp_path)
    old_package = tmp_path / 'old-package'
    old_migrations = old_package / 'sql_migrations'
    old_migrations.mkdir(parents=True)
    first = sorted((store.package / 'sql_migrations').glob('*.sql'))[0]
    shutil.copyfile(first, old_migrations / first.name)
    contract = {'engine': 'sqlite', 'schema_version': 1, 'migrations': 'sql_migrations',
                'compatible_versions': {'min': 1, 'max': 1}}
    prepared = store.storage.prepare_schema(contract, old_package)
    store.storage.apply_schema(contract, old_package, prepared['revision'])
    with store.storage.connect() as db:
        db.execute("INSERT INTO blobs VALUES ('retained','Existing evidence',0)")
    with pytest.raises(PCWError, match='explicit module installation/update'):
        store.storage_ready()
    plan = store.storage_prepare()
    assert plan['schema']['current_version'] == 1
    assert plan['schema']['target_version'] == 2
    store.storage_apply(plan['revision'])
    assert store.storage_ready()['schema_version'] == 2
    with store.storage.connect(write=False) as db:
        assert db.execute("SELECT text FROM blobs WHERE id='retained'").fetchone()[0] == 'Existing evidence'
        assert db.execute('SELECT count(*) FROM workspace_edit_reservations').fetchone()[0] == 0


def test_mcp_operation_registration_and_conflict_response(work):
    from types import SimpleNamespace
    from contextopus.core.operations import OperationRegistry
    from workspace.operations import register_operations
    from workspace.server import close_runtimes
    r, cards, a, b = work
    registry = OperationRegistry()
    register_operations(registry, provider_factory=Factory())
    context = SimpleNamespace(root=r.engine.work_root, workspace='project')
    try:
        result = registry.dispatch('execute', 'workspace.reserve',
            {'cycle_id': a, 'material_ids': [cards['Alpha']['id']], 'reason': 'MCP operation check'}, context)
        assert result['reservations'][0]['reason'] == 'MCP operation check'
        with pytest.raises(PCWError, match='MCP operation check'):
            registry.dispatch('execute', 'workspace.reserve',
                {'cycle_id': b, 'material_ids': [cards['Alpha']['id']], 'reason': 'Competing request'}, context)
        result = registry.dispatch('query', 'workspace.reservations', {}, context)
        assert result['total'] == 1
    finally:
        close_runtimes()


@pytest.mark.parametrize('reason,ttl', [('',900), ('two\nlines',900), ('x'*241,900), ('Edit',0), ('Edit',3601), ('Edit',True)])
def test_invalid_reservation_request_never_acquires(work, reason, ttl):
    r, cards, a, b = work
    with pytest.raises(PCWError):
        r.reserve(a, [cards['Alpha']['id']], reason, ttl_seconds=ttl)
    assert r.reservations()['total'] == 0


def test_revising_proposal_releases_dropped_cards_but_keeps_other_reviews(work):
    r, cards, a, b = work
    first = r.proposal_prepare(a, [change(cards['Alpha'], status='verified'), change(cards['Beta'], status='verified')], expected_task_revision=r.status(a)['task_revision'])
    second = r.proposal_prepare(a, [change(cards['Beta'], status='checked')], expected_task_revision=r.status(a)['task_revision'])
    r.proposal_prepare(a, [change(cards['Gamma'], status='verified')], proposal_id=first['proposal_id'], expected_task_revision=r.status(a)['task_revision'])
    assert {row['material_id'] for row in r.reservations()['reservations']} == {cards['Beta']['id'], cards['Gamma']['id']}
    assert r.reserve(b, [cards['Alpha']['id']], 'Independent Alpha edit')['status'] == 'reserved'
    r.proposal_discard(a, second['proposal_id'], second['revision'])
    assert {row['material_id'] for row in r.reservations(cycle_id=a)['reservations']} == {cards['Gamma']['id']}
