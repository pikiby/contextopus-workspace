"""Explicit Core setup delivers docs after a working embedding profile."""
from unittest.mock import patch
import pytest

from contextopus.core import model_setup
from contextopus.core.resources import ProcessResources
from contextopus.startup import Application
from test_runtime import Factory
from workspace_support import install_workspace


@pytest.fixture
def app(tmp_path, monkeypatch):
    hardware = {'cpu': {'available': True}, 'cuda': {'available': False, 'count': 0, 'devices': []},
                'mps': {'available': False}, 'torch_available': False, 'diagnostics': []}
    monkeypatch.setattr(model_setup, 'detect_hardware', lambda probe=None: hardware)
    with ProcessResources(Factory()) as resources:
        with Application(tmp_path / 'root', resources=resources, discover=False) as application:
            install_workspace(application.layout.root, app=application)
            yield application


def workspace_setup(result):
    return result['setup']['workspace'][0]


def configure(app):
    preview = app.query('core.model_plan', {'device': 'cpu'})
    result = app.execute('core.model_apply', {'plan': preview})
    assert result['status'] == 'ok'
    return result


def test_read_queries_and_first_init_wait_for_model_then_apply_delivers_docs(app):
    app.query('core.base_status')
    app.query('core.base_plan')
    app.query('core.algorithms')
    assert not app.module_manager.contributions['workspace'].base_context.path.exists()
    first = app.execute('core.init')
    assert workspace_setup(first)['status'] == 'model_setup_required'
    assert not app.module_manager.contributions['workspace'].base_context.path.exists()
    assert app.active_profile() is None
    result = configure(app)
    assert workspace_setup(result)['status'] == 'ready', result
    assert workspace_setup(result)['base_context']['status'] == 'installed'
    assert workspace_setup(result)['algorithm_catalog']['count'] > 0
    status = app.query('core.base_status')
    assert status['present'] is True
    assert status['installed_version'] == status['packaged_version']
    assert app.query('core.base_plan')['status'] == 'current'
    semantic = app.query('core.algorithms', {'query': 'nearest fixed window', 'semantic': True})
    assert semantic.get('catalog_state') not in {'missing', 'stale'}
    assert semantic['results']


def test_existing_ready_root_init_prepares_upgrade_docs_and_refreshes_catalog(app):
    preview = app.query('core.model_plan', {'device': 'cpu'})
    # Simulate a root configured by a previous installation without docs/catalog setup.
    assert app.apply_profile(preview['config'], preview['chunking'])['status'] == 'ok'
    assert not app.module_manager.contributions['workspace'].base_context.path.exists()
    assert app.query('core.algorithms', {'query': 'fixed window', 'semantic': True})['catalog_state'] == 'missing'
    result = app.execute('core.init')
    assert workspace_setup(result)['status'] == 'ready'
    assert app.query('core.base_status')['present']
    assert app.query('core.algorithms', {'query': 'fixed window', 'semantic': True})['results']


def test_init_keeps_user_docs_changes_and_returns_explicit_upgrade_plan(app):
    configure(app)
    state = app.module_manager.contributions['workspace'].base_context._read()
    entry = state['managed']['documents']['workspace-guide']
    path = app.module_manager.contributions['workspace'].base_context._context_path(state['managed']['context']) / entry['path']
    path.write_bytes(path.read_bytes() + b'\nUser documentation note\n')
    original = path.read_bytes()
    result = app.execute('core.init')
    assert result['status'] == 'initialized'
    docs = workspace_setup(result)['base_context']
    assert docs['status'] == 'conflicts'
    assert docs['plan']['conflicts'][0]['reason'] == 'user_modified'
    assert docs['plan']['plan_revision']
    assert path.read_bytes() == original


def test_delivery_failure_preserves_successfully_installed_model(app):
    original = app.execute
    def interrupted(operation, arguments=None):
        if operation == 'core.base_install':
            raise OSError('Injected documentation delivery failure')
        return original(operation, arguments)
    with patch.object(app, 'execute', interrupted):
        result = configure(app)
    assert result['status'] == 'ok'
    assert app.active_profile() is not None
    assert workspace_setup(result)['status'] == 'pending'
    assert workspace_setup(result)['base_context']['status'] == 'pending'
    assert workspace_setup(result)['base_context']['next_action'] == 'core.base_plan'
    # Explicit retry of setup delivers docs without reconfiguring the working model.
    assert workspace_setup(app.execute('core.init'))['status'] == 'ready'


def switch_profile(app, revision):
    import copy
    config = copy.deepcopy(app.active_profile()['config'])
    config['profile']['revision'] = revision
    preview = app.query('core.model_plan', {'config': config, 'device': 'cpu'})
    return app.execute('core.model_apply', {'plan': preview})


def test_profile_switch_after_setup_rebuilds_docs_and_algorithm_cards(app):
    configure(app)
    before = app.active_profile()['table']
    result = switch_profile(app, 'new-profile')
    assert result['status'] == 'ok'
    assert app.active_profile()['table'] != before
    assert workspace_setup(result)['status'] == 'ready'
    assert app.query('core.base_plan')['status'] == 'current'
    found = app.query('core.algorithms', {'query': 'nearest fixed window', 'semantic': True})
    assert found['results'] and found.get('catalog_state') not in {'missing', 'stale'}


def test_profile_switch_recovers_catalog_only_after_failed_docs_delivery(app):
    original = app.execute
    def interrupted(operation, arguments=None):
        if operation == 'core.base_install':
            raise OSError('Injected documentation delivery failure')
        return original(operation, arguments)
    with patch.object(app, 'execute', interrupted):
        configure(app)
    assert not app.module_manager.contributions['workspace'].base_context.path.exists()
    old_table = app.active_profile()['table']
    result = switch_profile(app, 'new-profile-after-docs-failure')
    assert result['status'] == 'ok' and workspace_setup(result)['status'] == 'ready'
    assert app.active_profile()['table'] != old_table
    assert app.query('core.base_status')['present']
    assert app.query('core.algorithms', {'query': 'nearest', 'semantic': True})['results']


@pytest.mark.parametrize('with_docs', [False, True])
@pytest.mark.parametrize('source_key', [('third_party', 'notes'), ('core', None)])
def test_profile_switch_preserves_foreign_source_rows_without_adapter(app, with_docs, source_key):
    from contextopus.core.rag import DocumentKey, IndexDocument
    if with_docs:
        configure(app)
    else:
        preview = app.query('core.model_plan', {'device': 'cpu'})
        app.apply_profile(preview['config'], preview['chunking'])
        app.execute('core.algorithms_refresh')
    app.index.index(IndexDocument(DocumentKey(*source_key, 'important'),
                    'source-v1', '# Foreign source\nNever discard this document', 'foreign/source.md', {}))
    before = app.active_profile()
    rows = app.index.read_rows(app.index.table, ['module', 'collection', 'object_id', 'text'], "object_id = 'important'")
    with pytest.raises(ValueError, match='source adapters'):
        switch_profile(app, 'must-not-publish')
    assert app.active_profile() == before
    assert app.index.read_rows(app.index.table, ['module', 'collection', 'object_id', 'text'], "object_id = 'important'") == rows
