"""New contexts default to automatic cleanup; old missing policy stays safe."""
import json

import pytest
import yaml

from workspace.configuration import WorkspaceSettings as Settings, WORKSPACE_CREATION_DEFAULTS, WORKSPACE_POLICY_DEFAULTS
from workspace.cycles import CycleRuntime
from workspace.runtime import PCWError
from workspace.policies import extract_settings, policies
from workspace.workspaces import EMPTY_PROJECT
from test_runtime import Factory


def test_cleanup_creation_default_is_independent_of_legacy_fallback():
    assert WORKSPACE_CREATION_DEFAULTS['cleanup_approval_required'] is False
    assert WORKSPACE_POLICY_DEFAULTS['cleanup_approval_required'] is True
    assert extract_settings('# Rules', creation=True)[0]['cleanup_approval_required'] is False
    with pytest.raises(PCWError, match='original policy settings'):
        policies('# Rules', creation=True)
    assert extract_settings('# Legacy rules')[0]['cleanup_approval_required'] is True
    legacy = '---\n' + yaml.safe_dump({key: value for key, value in WORKSPACE_POLICY_DEFAULTS.items()
                                                    if key != 'cleanup_approval_required'}) + '---\nRules\n'
    assert policies(legacy, creation=True)['cleanup_approval_required'] is False
    assert policies(legacy)['cleanup_approval_required'] is True


@pytest.mark.parametrize('mode,expected', [('recommended', False), ('legacy-settings', False),
                                         ('legacy-instructions', False),
                                         ('explicit-true', True), ('explicit-false', False),
                                         ('instructions-true', True), ('instructions-false', False)])
def test_created_context_persists_creation_cleanup_choice(tmp_path, mode, expected):
    runtime = CycleRuntime(tmp_path, 'project', provider_factory=Factory())
    instructions = '# Rules\n'
    arguments = {}
    if mode in {'recommended', 'legacy-settings', 'explicit-true', 'explicit-false'}:
        settings = dict(WORKSPACE_CREATION_DEFAULTS)
        if mode == 'legacy-settings':
            settings.pop('cleanup_approval_required')
        elif mode.startswith('explicit-'):
            settings['cleanup_approval_required'] = expected
        arguments['settings'] = settings
    elif mode == 'legacy-instructions' or mode.startswith('instructions-'):
        settings = dict(WORKSPACE_POLICY_DEFAULTS)
        if mode == 'legacy-instructions':
            settings.pop('cleanup_approval_required')
        else:
            settings['cleanup_approval_required'] = expected
        instructions = '---\n' + yaml.safe_dump(settings) + '---\n# Rules\n'
    try:
        runtime.workspace_create('create', instructions, EMPTY_PROJECT, 'Create this context',
                                 project_description='Context boundaries for cleanup defaults', **arguments)
        assert runtime.settings_read(fields=['cleanup_approval_required'])['values']['cleanup_approval_required'] is expected
    finally:
        runtime.close()
    reopened = CycleRuntime(tmp_path, 'project', provider_factory=Factory())
    try:
        assert reopened.settings_read(fields=['cleanup_approval_required'])['values']['cleanup_approval_required'] is expected
    finally:
        reopened.close()


def test_existing_context_without_cleanup_setting_keeps_legacy_fallback(tmp_path):
    runtime = CycleRuntime(tmp_path, 'project', provider_factory=Factory())
    try:
        runtime.workspace_create('create', '# Rules', EMPTY_PROJECT, 'Create test context',
                                 settings=WORKSPACE_CREATION_DEFAULTS,
                                 project_description='Legacy fallback test')
        path = Settings(runtime.control.layout)._scope_path('workspace', 'project')
        old_configuration = json.loads(path.read_text())
        old_configuration.pop('cleanup_approval_required')
        path.write_text(json.dumps(old_configuration))
        assert Settings(runtime.control.layout).workspace_snapshot(context='project', fields=['cleanup_approval_required'])['values']['cleanup_approval_required'] is True
        with pytest.raises(PCWError, match='settings are missing'):
            runtime.settings_read(fields=['cleanup_approval_required'])
        transition, _ = runtime._settings_transition(4, {})
        assert transition['effective']['cleanup_approval_required'] is True
        assert 'cleanup_approval_required' not in json.loads(path.read_text())
    finally:
        runtime.close()
