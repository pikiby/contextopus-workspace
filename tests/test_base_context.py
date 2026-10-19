"""Base docs use ordinary records and recover exact managed updates."""
import copy
import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest
from workspace.base_context import BaseContext, packaged_bundle
from workspace.cycles import CycleRuntime
from workspace.runtime import PCWError
from workspace.text_io import read_utf8
from test_runtime import Factory
from workspace.analytics import EventStore


def prepare_module_storage(root):
    # Direct domain fixtures perform the same explicit schema step as module install.
    store = EventStore(root)
    plan = store.storage_prepare()
    store.storage_apply(plan['revision'])


@pytest.fixture
def base(tmp_path):
    (tmp_path / 'modules/workspace/workspace_contexts').mkdir(parents=True)
    prepare_module_storage(tmp_path)
    factory = Factory()
    bundle = packaged_bundle()
    bundle['documents'] = [
        {'key': 'guide', 'type': 'guide', 'description': 'Explains the verified product workflow and where its requirements apply.', 'content': '# Product guide\n\nVerified product documentation.\n'},
        {'key': 'template', 'type': 'template', 'description': 'Provides the fields to review when authoring product instructions.', 'content': '# Product template\n\nPurpose: ...\n'}]
    return BaseContext(tmp_path, runtime_factory=lambda root, context:
        CycleRuntime(root, context, provider_factory=factory), bundle=bundle)


def install(service):
    result = service.install(service.plan()['plan_revision'])
    assert result['status'] == 'installed', result
    return result


def test_packaged_scenario_upgrade_keeps_existing_ids_and_resolves_navigation(tmp_path):
    """Exercise the real bundle graph, including links to newly shipped topics."""
    (tmp_path / 'modules/workspace/workspace_contexts').mkdir(parents=True)
    prepare_module_storage(tmp_path)
    bundle = packaged_bundle()
    original_keys = {'installed-version', 'workspace-guide', 'module-guide', 'algorithm-guide',
                     'module-template', 'algorithm-template', 'rules-template'}
    previous = copy.deepcopy(bundle)
    previous['documents'] = [item for item in previous['documents'] if item['key'] in original_keys]
    assert {item['key'] for item in previous['documents']} == original_keys
    for item in previous['documents']:
        item['links'] = [key for key in item.get('links', []) if key in original_keys]
        if item['key'] == 'workspace-guide':
            item['content'] = '# Earlier Workspace guide\n\nPreviously shipped documentation.\n'
    factory = Factory()
    service = BaseContext(tmp_path, runtime_factory=lambda root, context:
        CycleRuntime(root, context, provider_factory=factory), bundle=previous)
    install(service)
    before = copy.deepcopy(service._read()['managed']['documents'])

    # Resource changes are reviewable even within the same unreleased version.
    service.bundle = bundle
    preview = service.plan()
    assert not preview['create'] and preview['status'] == 'ready' and not preview['conflicts']
    assert {'contextopus-guide', 'retrieval-guide', 'evidence-guide', 'integrations-guide'} <= {
        item['key'] for item in preview['changes']}
    assert service.install(preview['plan_revision'])['status'] == 'installed'
    managed = service._read()['managed']
    assert all(managed['documents'][key]['id'] == entry['id'] for key, entry in before.items())
    runtime = service.runtime_factory(service.layout.root, managed['context'])
    try:
        project_id = runtime.project_read()['id']
        for item in bundle['documents']:
            record = runtime.read(material_id=managed['documents'][item['key']]['id'])
            assert record['content'] == item['content'] and record['description'] == item['description']
            assert record['index_status'] == 'indexed'
            expected = {managed['documents'][key]['id'] for key in item.get('links', [])}
            assert expected | {project_id} <= set(record['links'])
    finally:
        runtime.close()
    assert service.plan()['status'] == 'current'


def settings(service, changes):
    runtime = service.runtime_factory(service.layout.root, service.status()['managed_context'])
    try:
        preview = runtime.settings_preview(changes, 'Update the documentation context policy for this test')
        runtime.settings_apply(preview['preview_revision'], 'Approve the exact shown documentation settings')
    finally:
        runtime.close()


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_unicode_neighbors_and_managed_update_ignore_implicit_locale(base, monkeypatch, newline):
    """Real UTF-8 records survive install/update/reopen with a cp1252 default."""
    def lines(text):
        return text.replace('\n', newline)

    rules = lines('# Правила Н 🙂\nСохранять текст и ссылки без изменений.\n')
    neighbor = base.runtime_factory(base.layout.root, 'neighbor')
    try:
        neighbor.workspace_create('neighbor-create', rules, '# Соседний проект Н', 'Create the independent context',
                                  settings={**base.bundle['settings'], 'cleanup_approval_required': True},
                                  project_description='Описывает независимые записи Н и их связи.')
        cycle = neighbor.startup('neighbor-records')['cycle_id']
        bodies = {'target': lines('# Цель Н 🙂\nТочные условия применения.\n'),
                  'source': lines('# Источник Н\nПроверить связанную цель.\n')}
        proposal = neighbor.proposal_prepare(cycle, [
            {'key': key, 'content': body, 'description': 'Содержит проверенные условия Н и границы применения.',
             'path': 'Знания/' + ('Цель Н.md' if key == 'target' else 'Источник Н.md'),
             'links': [] if key == 'target' else ['@target']}
            for key, body in bodies.items()])
        assert neighbor.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'])['status'] == 'applied'
        cleanup = neighbor.finish(cycle)
        assert cleanup['status'] == 'blocked' and cleanup['tmp_preserved']
        assert neighbor.finish(cycle, confirmation='Delete the reviewed temporary files',
                               expected_revision=cleanup['cleanup_revision'])['status'] == 'closed'
        originals = {key: neighbor.read(material_id=ident) for key, ident in proposal['created_ids'].items()}
        neighbor_bytes = {neighbor.engine.root / item['path']: (neighbor.engine.root / item['path']).read_bytes()
                          for item in originals.values()}
        neighbor_bytes[neighbor.engine.root / 'INSTRUCTIONS.md'] = (neighbor.engine.root / 'INSTRUCTIONS.md').read_bytes()
    finally:
        neighbor.close()

    base.bundle['instructions'] = rules
    base.bundle['documents'][0].update(content=lines('# Руководство Н 🙂\nПроверенный порядок действий.\n'),
                                     description='Объясняет проверенный порядок Н и условия применения.', links=['template'])
    base.bundle['documents'][1].update(content=lines('# Шаблон Н\nУказать проверяемые условия.\n'),
                                     description='Определяет поля Н для проверки документации.')
    # U+041D encodes a byte (0x9d) undefined in cp1252, so accidental locale
    # decoding cannot silently pass as ASCII or merely produce mojibake.
    with pytest.raises(UnicodeDecodeError):
        base.bundle['documents'][0]['content'].encode('utf-8').decode('cp1252')
    original_read_text = Path.read_text

    def locale_read_text(path, encoding=None, errors=None, **kwargs):
        return original_read_text(path, encoding=encoding or 'cp1252', errors=errors, **kwargs)

    monkeypatch.setattr(Path, 'read_text', locale_read_text)
    assert packaged_bundle()['documents']
    install(base)
    base.select('neighbor')  # Preserve a selected role independent of managed docs.
    initial = copy.deepcopy(base._read())
    initial_revision = base.plan()['plan_revision']
    reopened = BaseContext(base.layout.root, runtime_factory=base.runtime_factory, bundle=copy.deepcopy(base.bundle))
    assert reopened._read() == initial
    assert reopened.plan()['status'] == 'current'
    assert reopened.plan()['plan_revision'] == initial_revision
    base_root = reopened._context_path(initial['managed']['context'])
    initial_rules = (base_root / 'INSTRUCTIONS.md').read_bytes()
    base_settings = reopened.runtime_factory(base.layout.root, initial['managed']['context'])
    try:
        initial_settings = base_settings.settings_read()['values']
    finally:
        base_settings.close()
    reopened.bundle['version'] = 'next-utf8-version'
    reopened.bundle['documents'][0]['content'] += lines('\nНовые проверенные условия Н.\n')
    plan = reopened.plan()
    assert plan['status'] == 'ready' and plan['conflicts'] == []
    assert reopened.install(plan['plan_revision'])['status'] == 'installed'
    final = BaseContext(base.layout.root, runtime_factory=base.runtime_factory, bundle=reopened.bundle)
    state = final._read()
    assert final.status()['context'] == 'neighbor'
    assert state['managed']['version'] == 'next-utf8-version'
    assert final.plan()['status'] == 'current'
    assert (base_root / 'INSTRUCTIONS.md').read_bytes() == initial_rules == rules.encode('utf-8')
    runtime = final.runtime_factory(base.layout.root, state['managed']['context'])
    try:
        assert runtime.settings_read()['values'] == initial_settings
        for item in final.bundle['documents']:
            receipt = state['managed']['documents'][item['key']]
            assert receipt['id'] == initial['managed']['documents'][item['key']]['id']
            actual = runtime.read(material_id=receipt['id'])
            assert actual['content'] == item['content'] and actual['description'] == item['description']
            assert runtime.project_read()['id'] in actual['links']
            if item['key'] == 'guide':
                assert state['managed']['documents']['template']['id'] in actual['links']
            assert receipt['file_hash'] == hashlib.sha256((base_root / receipt['path']).read_bytes()).hexdigest()
    finally:
        runtime.close()
    neighbor = final.runtime_factory(base.layout.root, 'neighbor')
    try:
        for key, before in originals.items():
            actual = neighbor.read(material_id=before['id'])
            assert actual['content'] == bodies[key] and actual['revision'] == before['revision']
            assert actual['path'] == before['path'] and actual['links'] == before['links']
        assert originals['target']['id'] in originals['source']['links']
        assert all(path.read_bytes() == before for path, before in neighbor_bytes.items())
    finally:
        neighbor.close()
    # The conflict path must return the literal user's Unicode text too.
    guide = base_root / state['managed']['documents']['guide']['path']
    changed = guide.read_bytes() + lines('\nПользовательское уточнение Н.\n').encode('utf-8')
    guide.write_bytes(changed)
    conflict = final.plan()
    assert conflict['status'] == 'conflicts'
    assert conflict['conflicts'][0]['current'] == changed.decode('utf-8')
    assert final.install(conflict['plan_revision'])['status'] == 'conflicts'
    assert guide.read_bytes() == changed


def test_passive_preview_then_shared_index_and_history(base):
    base.bundle['settings']['cleanup_approval_required'] = True  # Explicit approval branch.
    assert base.status()['context'] is None
    assert base.status()['next_action'] == 'core.base_plan'
    preview = base.plan()
    assert preview['create'] and preview['material_max_tokens'] is None
    assert not base.path.exists()
    assert not base._context_path(preview['context']).exists()
    installed = install(base)
    assert installed['next_action'] == 'finish'
    assert installed['cleanup']['status'] == 'blocked'
    assert installed['cleanup']['tmp_preserved']
    state = base._read()
    runtime = base.runtime_factory(base.layout.root, state['context'])
    try:
        cleanup = installed['cleanup']
        assert runtime._tmp(cleanup['cycle_id']).is_dir()
        assert all((runtime.engine.root / path).is_file() for path in cleanup['temporary_files'])
        cycles_before = copy.deepcopy(runtime._records())
        retry = base.install(installed['plan_revision'])
        assert retry['status'] == 'installed' and retry['replayed']
        assert retry['cleanup'] == {key: cleanup[key] for key in retry['cleanup']}
        assert runtime._records() == cycles_before
        assert runtime.record_types()['types'] == [
            {'type': 'guide', 'count': 1}, {'type': 'project', 'count': 1}, {'type': 'template', 'count': 1}]
        for receipt in state['managed']['documents'].values():
            assert runtime.read(material_id=receipt['id'])['index_status'] == 'indexed'
        assert (runtime.engine.root / '.git').is_dir()
        assert base.plan()['status'] == 'current'
        assert base.install(base.plan()['plan_revision'])['status'] == 'current'
        assert runtime.finish(cleanup['cycle_id'],
                              confirmation='Delete the reviewed installer temporary files',
                              expected_revision=cleanup['cleanup_revision'])['status'] == 'closed'
        assert base.plan()['status'] == 'current'
        assert 'cleanup' not in base.install(installed['plan_revision'])
    finally:
        runtime.close()


@pytest.mark.parametrize('deleted', [False, True])
def test_preserve_modified_or_deleted_docs(base, deleted):
    install(base)
    state = base._read()
    receipt = state['managed']['documents']['guide']
    path = base._context_path(state['managed']['context']) / receipt['path']
    if deleted:
        path.unlink()
    else:
        path.write_text(path.read_text() + '\nUser knowledge\n')
    base.bundle['documents'][0]['content'] += '\nNew release guide\n'
    preview = base.plan()
    assert preview['status'] == 'conflicts'
    assert preview['conflicts'][0]['reason'] == ('deleted' if deleted else 'user_modified')
    assert preview['conflicts'][0]['incoming'] == base.bundle['documents'][0]['content']
    assert base.install(preview['plan_revision'])['status'] == 'conflicts'
    assert path.exists() is not deleted
    if not deleted:
        assert 'User knowledge' in path.read_text()


def test_preserve_custom_instructions_role_and_ids(base):
    install(base)
    initial = base._read()
    root = base._context_path(initial['managed']['context'])
    instructions = root / 'INSTRUCTIONS.md'
    custom = instructions.read_text() + '\nCustom documentation rule\n'
    instructions.write_text(custom)
    other = base.runtime_factory(base.layout.root, 'other')
    try:
        other.workspace_create('other-create', base.bundle['instructions'], '# Other project', 'Create another context',
                               settings=base.bundle['settings'], project_description='Defines the independent context used to verify documentation role selection.')
    finally:
        other.close()
    base.select('other')
    base.bundle['documents'][0]['content'] += '\nDocumented new behavior\n'
    base.bundle['version'] = 'next-version'
    updated = install(base)
    assert updated['context'] == 'other' and updated['active_rules_changed'] is False
    assert instructions.read_text() == custom
    assert base._read()['managed']['documents']['guide']['id'] == initial['managed']['documents']['guide']['id']
    assert base.plan()['status'] == 'current'


def test_stale_plan_and_unmanaged_collision(base):
    preview = base.plan()
    base._context_path(preview['context']).mkdir()
    with pytest.raises(PCWError, match='changed since preview'):
        base.install(preview['plan_revision'])
    collision = base.plan()
    assert collision['conflicts'][0]['reason'] == 'unmanaged_context_exists'
    assert base.install(collision['plan_revision'])['status'] == 'conflicts'


def test_changed_approval_requires_real_confirmation(base):
    install(base)
    instructions = base._context_path(base.status()['context']) / 'INSTRUCTIONS.md'
    settings(base, {'knowledge_approval_required': True})
    base.bundle['documents'][0]['content'] += '\nNew documentation\n'
    preview = base.plan()
    assert preview['approval_required'] is True
    assert base.install(preview['plan_revision'])['status'] == 'review_requires_approval'
    assert not base._read().get('pending')
    assert base.install(preview['plan_revision'], 'Approve exact shown documentation revision')['status'] == 'installed'


def test_changed_limit_needs_split_preserves_docs(base):
    install(base)
    instructions = base._context_path(base.status()['context']) / 'INSTRUCTIONS.md'
    settings(base, {'material_max_tokens': 20})
    custom = instructions.read_text()
    before = copy.deepcopy(base._read()['managed'])
    base.bundle['documents'][0]['content'] += '\n' + 'long text ' * 100
    result = base.install(base.plan()['plan_revision'])
    assert result['status'] == 'needs_split'
    assert instructions.read_text() == custom and base._read()['managed'] == before


def test_partial_apply_retries_same_proposal(base):
    calls = []
    original = CycleRuntime.proposal_apply
    def partial(runtime, *args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            return {'status': 'partial', 'error': 'Injected interruption'}
        return original(runtime, *args, **kwargs)
    preview = base.plan()
    with patch.object(CycleRuntime, 'proposal_apply', partial):
        assert base.install(preview['plan_revision'])['status'] == 'partial'
        assert base.plan()['status'] == 'pending'
        assert base.install(preview['plan_revision'])['status'] == 'installed'
    assert len(set(calls)) == 1
    assert len(base._read()['managed']['documents']) == 2


def test_long_packaged_docs_initially_unlimited_and_linked_to_project(base):
    base.bundle['documents'][0]['content'] += '\n' + 'documented behavior ' * 150
    base.bundle['documents'][0]['links'] = ['template']
    install(base)
    state = base._read()
    runtime = base.runtime_factory(base.layout.root, state['context'])
    try:
        guide = runtime.read(material_id=state['managed']['documents']['guide']['id'])
        assert runtime.project_read()['id'] in guide['links']
        assert state['managed']['documents']['template']['id'] in guide['links']
    finally:
        runtime.close()


def test_managed_update_preserves_user_added_links(base):
    install(base)
    state = base._read()
    runtime = base.runtime_factory(base.layout.root, state['context'])
    try:
        cycle = runtime.startup('custom-links')['cycle_id']
        created = runtime.proposal_prepare(cycle, [{'key': 'custom', 'content': '# User reference\nAdditional context', 'description': 'Explains the user reference conditions needed by the product guide.'}])
        runtime.proposal_apply(cycle, created['proposal_id'], created['revision'])
        user_id = created['created_ids']['custom']
        guide_id = state['managed']['documents']['guide']['id']
        guide = runtime.read(material_id=guide_id)
        changed = runtime.proposal_prepare(cycle, [{'material_id': guide_id, 'expected_revision': guide['revision'], 'links': [*guide['links'], user_id]}])
        runtime.proposal_apply(cycle, changed['proposal_id'], changed['revision'])
        runtime.finish(cycle)
    finally:
        runtime.close()
    base.bundle['documents'][0]['content'] += '\nUpdated guide\n'
    assert base.plan()['conflicts'] == []
    install(base)
    runtime = base.runtime_factory(base.layout.root, state['context'])
    try:
        assert user_id in runtime.read(material_id=guide_id)['links']
    finally:
        runtime.close()


def test_crash_after_preparation_recovers_saved_proposal(base):
    original = base._write
    crashed = False
    def interrupted(state):
        nonlocal crashed
        if state.get('pending', {}).get('stage') == 'apply' and not crashed:
            crashed = True
            raise OSError('Crash before installer receipt')
        return original(state)
    preview = base.plan()
    with patch.object(base, '_write', interrupted):
        with pytest.raises(OSError, match='installer receipt'):
            base.install(preview['plan_revision'])
    saved = base._read()['pending']
    assert saved['stage'] == 'prepare'
    assert base.install(preview['plan_revision'])['status'] == 'installed'
    assert len(base._read()['managed']['documents']) == 2


def test_bundle_descriptions_are_authored_and_description_only_updates_are_reviewed(base):
    assert all(item.get('description', '').strip() for item in packaged_bundle()['documents'])
    install(base)
    state = base._read()
    entry = state['managed']['documents']['guide']
    runtime = base.runtime_factory(base.layout.root, state['context'])
    try:
        original = runtime.read(material_id=entry['id'])
        assert original['description'] == base.bundle['documents'][0]['description']
    finally:
        runtime.close()
    incoming = 'Explains the product workflow, verified limits and newly documented applicability.'
    base.bundle['documents'][0]['description'] = incoming
    preview = base.plan()
    assert preview['status'] == 'ready' and preview['changes'][0]['description'] == incoming
    install(base)
    runtime = base.runtime_factory(base.layout.root, state['context'])
    try:
        updated = runtime.read(material_id=entry['id'])
        assert updated['description'] == incoming and updated['content'] == original['content']
        assert updated['id'] == original['id']
    finally:
        runtime.close()


def test_managed_update_preserves_user_description_as_conflict(base):
    from workspace.runtime import _parse
    import yaml
    install(base)
    state = base._read()
    entry = state['managed']['documents']['guide']
    path = base._context_path(state['managed']['context']) / entry['path']
    metadata, body = _parse(read_utf8(path))
    metadata['description'] = 'Describes the user-specific workflow and its verified exceptions.'
    path.write_text('---\n' + yaml.safe_dump(metadata, allow_unicode=True) + '---\n' + body,
                    encoding='utf-8', newline='')
    before = path.read_bytes()
    base.bundle['documents'][0]['content'] += '\nUpstream behavior.\n'
    plan = base.plan()
    assert plan['status'] == 'conflicts' and plan['conflicts'][0]['reason'] == 'user_modified'
    assert base.install(plan['plan_revision'])['status'] == 'conflicts'
    assert path.read_bytes() == before


def test_native_link_alias_reformat_does_not_look_like_user_content_change(base):
    from workspace.runtime import _parse
    import yaml
    base.bundle['documents'][0]['links'] = ['template']
    install(base)
    state = base._read()
    entry = state['managed']['documents']['guide']
    path = base._context_path(state['managed']['context']) / entry['path']
    metadata, body = _parse(read_utf8(path))
    assert all(link.startswith('[[') for link in metadata['links'])
    metadata['links'] = [link.split('|', 1)[0] + '|Readable user display]]' for link in metadata['links']]
    path.write_text('---\n' + yaml.safe_dump(metadata, allow_unicode=True) + '---\n' + body,
                    encoding='utf-8', newline='')
    assert base.plan()['status'] == 'current'
    base.bundle['documents'][0]['content'] += '\nUpstream behavior.\n'
    assert base.plan()['conflicts'] == []
    install(base)
    assert base.plan()['status'] == 'current'


def test_missing_native_target_is_a_reviewable_conflict_with_no_rewrite(base):
    base.bundle['documents'][0]['links'] = ['template']
    install(base)
    state = base._read()
    root = base._context_path(state['managed']['context'])
    guide = root / state['managed']['documents']['guide']['path']
    target = root / state['managed']['documents']['template']['path']
    target.unlink()
    before = guide.read_bytes()
    plan = base.plan()
    assert any(item['reason'] == 'navigation_conflict' for item in plan['conflicts'])
    assert base.install(plan['plan_revision'])['status'] == 'conflicts'
    assert guide.read_bytes() == before and not target.exists()


def test_default_base_install_closes_and_cleans_without_cleanup_approval(base):
    assert base.bundle['settings']['cleanup_approval_required'] is False
    installed = install(base)
    assert installed['next_action'] is None
    assert 'cleanup' not in installed
    managed = base._read()['managed']
    runtime = base.runtime_factory(base.layout.root, managed['context'])
    try:
        assert runtime._records()['cycles'][managed['cycle_id']]['state'] == 'closed'
        assert not runtime._tmp(managed['cycle_id']).exists()
        for receipt in managed['documents'].values():
            assert runtime.read(material_id=receipt['id'])['index_status'] == 'indexed'
    finally:
        runtime.close()


@pytest.mark.parametrize('user_edit', [False, True])
def test_expired_managed_proposal_can_be_reprepared(base, monkeypatch, user_edit):
    install(base)
    base.bundle['documents'][0]['content'] += '\nUpdated product guidance.\n'
    plan = base.plan()
    with monkeypatch.context() as interruption:
        def interrupted(*args, **kwargs):
            raise OSError('Simulated pause after preparation')
        interruption.setattr(CycleRuntime, 'proposal_apply', interrupted)
        with pytest.raises(OSError, match='Simulated pause'):
            base.install(plan['plan_revision'])
    runtime = base.runtime_factory(base.layout.root, plan['context'])
    try:
        with runtime.control.storage.connect() as db:
            db.execute('UPDATE workspace_edit_reservations SET expires_at=0')
    finally:
        runtime.close()
    result = base.install(plan['plan_revision'])
    assert result['status'] == 'needs_review' and result['next_action'] == 'core.base_install'
    assert base._read()['pending']['stage'] == 'prepare'
    if user_edit:
        runtime = base.runtime_factory(base.layout.root, plan['context'])
        try:
            record = runtime.read(material_id=plan['changes'][0]['material_id'])
            path = runtime.engine.root / record['path']
            edited = path.read_bytes() + b'\nUser-maintained condition.\n'
            path.write_bytes(edited)
            with pytest.raises(PCWError, match='Managed base document changed'):
                base.install(plan['plan_revision'])
            assert path.read_bytes() == edited
        finally:
            runtime.close()
    else:
        assert base.install(plan['plan_revision'])['status'] == 'installed'
        assert base.plan()['status'] == 'current'
