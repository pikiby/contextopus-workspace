"""Workspace is installed, removed from dispatch and restored like any module."""
import hashlib

import pytest

from contextopus.core.resources import ProcessResources
from contextopus.startup import Application
from module_fixtures import installable
from test_runtime import Factory
from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
from workspace_support import install_workspace


def test_workspace_and_ordinary_module_preserve_separate_state_on_restart(tmp_path):
    root = tmp_path / 'root'
    installable(root, 'notes')
    with ProcessResources(Factory()) as resources:
        with Application(root, context='project', resources=resources, discover=False) as app:
            assert 'workspace.read' not in app.registry._operations
            install_workspace(root, app)
            app.execute('core.module_install', {'module': 'notes'})
            created = app.execute('workspace.workspace_create', {
                'request_id': 'create-project',
                'user_request': 'Create a project for module lifecycle verification.',
                'instructions_content': '# Rules\nKeep verified facts and their relationships.',
                'project_content': '# Project\nVerify independent optional modules.',
                'project_description': 'Acceptance criteria for independent module installation.',
                'settings': WORKSPACE_CREATION_DEFAULTS,
            })
            assert created['status'] == 'created'
            assert not (root / '.pcw').exists()
            cycle = app.execute('workspace.startup', {
                'request_id': 'save-linked-facts', 'user_request': 'Preserve two linked facts.'})['cycle_id']
            proposal = app.execute('workspace.proposal_prepare', {'cycle_id': cycle, 'changes': [
                {'key': 'first', 'content': '# First fact\nStable module identity.',
                 'description': 'Defines stable identity during module updates.', 'links': ['@second']},
                {'key': 'second', 'content': '# Second fact\nDurable module data.',
                 'description': 'Defines data retained when a module is disabled.'},
            ]})
            app.execute('workspace.proposal_apply', {
                'cycle_id': cycle, 'proposal_id': proposal['proposal_id'],
                'expected_revision': proposal['revision']})
            ident = proposal['created_ids']['first']
            before = app.query('workspace.read', {'material_id': ident})
            path = root / 'modules/workspace/workspace_contexts/project' / before['path']
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            app.execute('notes.save', {'value': 'independent data'})
            assert (root / 'core/storage/modules/workspace.sqlite3').is_file()
            assert (root / 'core/storage/modules/notes.sqlite3').is_file()
            assert (root / 'core/analytics/operations.sqlite3').is_file()
            app.execute('core.module_disable', {'module': 'workspace'})
            with pytest.raises(ValueError, match='Unknown operation'):
                app.query('workspace.read', {'material_id': ident})
            assert app.query('notes.read')['rows'] == [{'value': 'independent data'}]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            assert app.query('procedure', {'name': 'default'})['name'] == 'modules'
        with Application(root, context='project', resources=resources, discover=False) as restarted:
            assert 'workspace.read' not in restarted.registry._operations
            restarted.execute('core.module_enable', {'module': 'workspace'})
            after = restarted.query('workspace.read', {'material_id': ident})
            for field in ('id', 'path', 'content', 'links', 'description', 'revision'):
                assert after[field] == before[field]
            assert restarted.query('workspace.status', {'cycle_id': cycle})['cycle_status'] == 'active'
            assert restarted.query('notes.read')['rows'] == [{'value': 'independent data'}]
