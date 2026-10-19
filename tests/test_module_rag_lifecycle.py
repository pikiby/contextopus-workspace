"""Disabled or unavailable lifecycle-owned vectors never bypass source validation."""
import json

import pytest

from contextopus.core.chunking import default_chunking
from contextopus.core.embeddings import default_config
from contextopus.core.rag import DocumentKey, IndexDocument, RagIndex
from contextopus.core.resources import ProcessResources
from contextopus.core.storage import StorageLayout
from contextopus.startup import Application
from test_runtime import Factory
from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
from workspace_support import install_workspace


def put(index, module, ident):
    index.index(IndexDocument(DocumentKey(module, 'notes', ident), 'r1',
                             'shared searchable fact', module + '://' + ident))


def test_generic_lifecycle_filters_disabled_and_unavailable_before_nearest(tmp_path):
    module = tmp_path / 'modules/sample'
    (module / 'package').mkdir(parents=True)
    manifest = {'id': 'sample', 'version': '1.0', 'operations': []}
    (module / 'module.json').write_text(json.dumps(manifest))
    with ProcessResources(Factory()) as resources, Application(tmp_path, resources=resources, discover=False) as app:
        app.execute('core.module_install', {'module': 'sample'})
        put(app.index, 'sample', 'managed')
        put(app.index, 'app_owned', 'independent')
        app.execute('core.module_disable', {'module': 'sample'})
        response = app.query('core.search', {'query': 'fact', 'distance': 2.0, 'candidate_limit': 1})
        assert [row['key'][0] for row in response['results']] == ['app_owned']
        assert response['diagnostics']['excluded_modules'] == {'sample': 'disabled'}
        assert app.index.table.count_rows() == 2
        with pytest.raises(ValueError, match='sample is disabled'):
            app.query('core.search', {'query': 'fact', 'module': 'sample'})
        # Standalone indexes read the same persisted state without importing code.
        standalone = RagIndex(StorageLayout(tmp_path), resources)
        assert {row['key'][0] for row in standalone.search('fact', distance=2)} == {'app_owned'}
        app.execute('core.module_enable', {'module': 'sample'})
        assert {row['key'][0] for row in app.query('core.search', {'query': 'fact', 'distance': 2.0})['results']} == {'sample', 'app_owned'}
        (module / 'package/changed.py').write_text('# release changed without preparation\n')
        response = app.query('core.search', {'query': 'fact', 'distance': 2.0})
        assert {row['key'][0] for row in response['results']} == {'app_owned'}
        assert response['diagnostics']['excluded_modules'] == {'sample': 'unavailable'}
        assert {row['key'][0] for row in standalone.search('fact', distance=2)} == {'app_owned'}
        with pytest.raises(ValueError, match='sample is unavailable'):
            app.query('core.search', {'query': 'fact', 'module': 'sample'})
        assert app.index.table.count_rows() == 2


def test_real_workspace_disable_removes_search_access_and_keeps_stored_vectors(tmp_path):
    with ProcessResources(Factory()) as resources, Application(tmp_path, 'project', resources=resources, discover=False) as app:
        install_workspace(tmp_path, app)
        app.execute('workspace.workspace_create', {
            'request_id': 'create', 'user_request': 'Create this isolated lifecycle fixture.',
            'instructions_content': '# Rules\nPreserve verified facts.',
            'project_content': '# Project\nShared searchable fact.',
            'project_description': 'Tests optional Workspace source lifecycle.',
            'settings': WORKSPACE_CREATION_DEFAULTS,
        })
        assert app.query('core.search', {'query': 'fact', 'module': 'workspace', 'distance': 2.0})['results']
        count = app.index.table.count_rows()
        app.execute('core.module_disable', {'module': 'workspace'})
        with pytest.raises(ValueError, match='workspace is disabled'):
            app.query('core.search', {'query': 'fact', 'module': 'workspace'})
        response = app.query('core.search', {'query': 'fact', 'distance': 2.0})
        assert not response['results']
        assert response['diagnostics']['excluded_modules']['workspace'] == 'disabled'
        assert app.index.table.count_rows() == count
        app.execute('core.module_enable', {'module': 'workspace'})
        assert app.query('core.search', {'query': 'fact', 'module': 'workspace', 'distance': 2.0})['results']
