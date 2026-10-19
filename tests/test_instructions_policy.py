"""Instruction approvals are independent and bound to the prior revision."""
import hashlib
import json

import pytest
from contextopus.integrations import integrate, plan
from workspace_support import install_workspace
from workspace.policies import policies
from workspace.runtime import PCWError
from test_workspace_v3 import workspace


def prepare(runtime, cycle, content):
    current = runtime.instructions_read(cycle)
    return runtime.instructions_prepare(cycle, content, current['revision'], 'Change these instructions')


@pytest.mark.parametrize('required', [True, False])
def test_instruction_policy_and_transition(workspace, required):
    r = workspace
    c = r.startup('policy')['cycle_id']
    first = r.settings_preview({'instructions_approval_required': False}, 'Disable instructions approval')
    assert first['approval_required'] is True
    with pytest.raises(PCWError, match='confirmation'):
        r.settings_apply(first['preview_revision'])
    r.settings_apply(first['preview_revision'], 'Approve shown disabling change')
    content = r.instructions_read(c)['content'] + '\nNew rule\n'
    second = prepare(r, c, content)
    assert second['approval_required'] is False
    assert second['next_action'] == 'present_changes_in_chat_then_apply'
    assert r.instructions_apply(c, second['proposal_id'], second['revision'])['status'] == 'applied'
    if required:
        transition = r.settings_preview({'instructions_approval_required': True}, 'Restore instructions approval')
        assert not transition['approval_required']
        r.settings_apply(transition['preview_revision'])
    third = prepare(r, c, content + '\nAnother rule\n')
    assert third['approval_required'] is required
    if required:
        with pytest.raises(PCWError, match='confirmation'):
            r.instructions_apply(c, third['proposal_id'], third['revision'])


def test_missing_policy_and_creation(workspace):
    r = workspace
    assert policies('# Legacy')['instructions_approval_required'] is True
    import yaml
    from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
    current = '---\n' + yaml.safe_dump(WORKSPACE_CREATION_DEFAULTS) + '---\nLegacy rules\n'
    assert r.settings_read(fields=['instructions_approval_required'])['values']['instructions_approval_required']
    assert policies(current, creation=True)['instructions_approval_required'] is True
    with pytest.raises(PCWError, match='settings'):
        policies(current.replace('instructions_approval_required: true\n', ''), creation=True)


@pytest.mark.parametrize('value', ['"false"', 'null', '0', 'no', '[false]', 'true\ninstructions_approval_required: false'])
def test_invalid_instruction_setting(value):
    with pytest.raises(PCWError):
        policies('---\ninstructions_approval_required: ' + value + '\n---\n')


@pytest.mark.parametrize('managed', [True, False])
def test_context_skill_migration_preserves_foreign(tmp_path, managed):
    install_workspace(tmp_path / "root")
    old = tmp_path / '.agents/skills/w-find/SKILL.md'
    old.parent.mkdir(parents=True)
    old.write_text('User modified discovery skill')
    if managed:
        metadata = tmp_path / '.contextopus/codex-integration.json'
        metadata.parent.mkdir()
        metadata.write_text(json.dumps({'skills': {old.relative_to(tmp_path).as_posix(): hashlib.sha256(old.read_bytes()).hexdigest()}}))
    preview = plan('codex', tmp_path, root=tmp_path / 'root')
    assert bool(preview['removals']) is managed
    assert bool(preview['preserved']) is not managed
    integrate(preview)
    assert old.exists() is not managed
    assert (old.parent.parent / 'w-context/SKILL.md').exists()
    assert not plan('codex', tmp_path, root=tmp_path / 'root')['removals']


@pytest.mark.parametrize('line_ending', [b'\n', b'\r\n'])
def test_known_outgoing_context_skill_migrates_without_metadata(tmp_path, line_ending):
    install_workspace(tmp_path / "root")
    # Exact shipped pre-rename bytes. Explicit escapes keep the fixture stable
    # with shallow checkouts and Windows source-file newline conversion.
    original = (
        b'---\n'
        b'name: w-find\n'
        b'description: Find Contextopus projects by name, path or meaning before choosing a context.\n'
        b'---\n\n'
        b'Call `contextopus_query` with `operation: "procedure"` and `arguments: {"name": "find"}`. '
        b'Follow the returned procedure. Preserve the user-selected memory root and context; '
        b'discovery does not switch them. Describe only unknown operation signatures. '
        b'If MCP is unavailable, report the connection failure.\n'
    )
    assert hashlib.sha256(original).hexdigest() == '61445e2cb00828b58123e8ec1d093a6611a4830ef02dd0b60c3a1463a8d8bc36'
    old = tmp_path / '.agents/skills/w-find/SKILL.md'
    old.parent.mkdir(parents=True)
    old.write_bytes(original.replace(b'\n', line_ending))
    preview = plan('codex', tmp_path, root=tmp_path / 'root')
    assert [entry['path'] for entry in preview['removals']] == [str(old)]
    integrate(preview)
    assert not old.exists()
    assert (old.parent.parent / 'w-context/SKILL.md').is_file()


def test_module_context_skill_remains_during_core_native_plugin_cutover(tmp_path):
    install_workspace(tmp_path / "root")
    from importlib.metadata import version
    integrate(plan('codex', tmp_path, scope='user', root=tmp_path / 'root'))
    skill = tmp_path / '.agents/skills/w-context/SKILL.md'
    assert skill.is_file()
    marker = tmp_path / ('.codex/plugins/cache/contextopus/contextopus/' + version('contextopus-core') + '/.codex-plugin/plugin.json')
    marker.parent.mkdir(parents=True)
    marker.write_text('{"name":"contextopus"}')
    integrate(plan('codex', tmp_path, scope='user', native_plugin=True, root=tmp_path / 'root'))
    # The native Core plugin does not own or replace independent module skills.
    assert skill.exists()


def test_false_instruction_policy_is_independent_of_true_knowledge_policy(workspace):
    r = workspace
    c = r.startup('independent-policy')['cycle_id']
    initial = r.settings_preview({'knowledge_approval_required': True, 'instructions_approval_required': False},
                                 'Separate knowledge approval from instructions approval')
    r.settings_apply(initial['preview_revision'], 'Approve these independent settings')
    content = r.instructions_read(c)['content']
    proposal = prepare(r, c, content + '\nNew rule\n')
    assert proposal['approval_required'] is False
    assert r.instructions_apply(c, proposal['proposal_id'], proposal['revision'])['status'] == 'applied'
