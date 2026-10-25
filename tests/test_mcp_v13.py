"""Workspace transport and lossless presentation across the journal boundary."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from workspace_support import install_workspace, workspace_module, SOURCE
from contextopus.interfaces.mcp.presentation import present
from contextopus.interfaces.mcp import server
from contextopus.integrations.reconnect import select_read_tool
from contextopus.integrations.remote import RemoteApplication


def material():
    return {'id': 'a', 'title': 'Useful material', 'path': 'knowledge/a.md', 'workspace': 'one',
            'description': 'Conditions for the requested action.', 'status': None,
            'content': 'Exact source body', 'links': ['b'],
            'link_targets': [{'id': 'b', 'title': 'Target', 'path': 'knowledge/b.md', 'workspace': 'two'}],
            'index_status': 'indexed', 'analytics': {'status': 'recorded', 'event_id': 'e'}}


def test_presentation_preserves_full_data_and_reviews_without_duplicate_links():
    source = {'materials': [material()], 'review_changes': [{'after': material()}],
              'settings': {'enabled': False, 'limit': 0, 'material_max_tokens': None},
              'warnings': [], 'history': {'status': 'pending', 'error': 'commit failure'}}
    original = copy.deepcopy(source)
    compact = present(source, operation='workspace.proposal_apply')
    assert source == original
    assert compact['materials'][0]['links'] == source['materials'][0]['link_targets']
    assert 'link_targets' not in compact['materials'][0]
    assert 'analytics' not in compact['materials'][0]
    assert 'index_status' not in compact['materials'][0]
    assert compact['materials'][0]['path'] == source['materials'][0]['path']
    assert compact['materials'][0]['workspace'] == 'one'
    assert compact['review_changes'] == original['review_changes']
    assert compact['settings'] == original['settings']
    assert compact['history'] == original['history']
    assert present(source, operation='workspace.proposal_apply', detail='full') is source


def test_compact_search_retains_fragment_text_when_explicit_and_all_faults():
    from contextopus.core.filtering import select
    row = {**material(), 'key': ['workspace', 'one', 'a'], 'distance': .1, 'excerpt': 'Matched part'}
    source = select([row], {'algorithm': 'nearest_n'})
    source.update(pending=['unindexed-id'], indexing={'status': 'failed', 'error': 'reason'})
    original = copy.deepcopy(source)
    compact = present(source, operation='workspace.search')
    assert 'excerpt' not in compact['results'][0]
    assert 'candidates' not in compact['diagnostics']
    assert compact['diagnostics']['counts']['selected_materials'] == 1
    assert compact['pending'] == ['unindexed-id']
    assert compact['indexing'] == source['indexing']
    explicit = present(source, operation='workspace.search', arguments={'output': 'fragments'})
    assert explicit['results'][0]['excerpt'] == 'Matched part'
    assert source == original
    compact['diagnostics']['counts']['selected_materials'] = 999
    assert source == original


def test_core_search_materials_retain_path_context_and_description():
    source = {'results': [{'key': ['workspace', 'one', 'a'], 'location': 'knowledge/a.md',
                           'metadata': {'description': 'Subject and conditions', 'custom': {'keep': None, 'enabled': False}}, 'distance': .1,
                           'excerpt': 'private fragment'}]}
    compact = present(source, operation='core.search')['results'][0]
    assert compact['path'] == 'knowledge/a.md'
    assert compact['workspace'] == 'one'
    assert compact['description'] == 'Subject and conditions'
    assert 'excerpt' not in compact
    assert compact['metadata'] == {'custom': {'keep': None, 'enabled': False}}
    assert 'location' not in compact


def fixture_server(monkeypatch, tmp_path, *, legacy_tools=False, app=None):
    app = app or SimpleNamespace(services=SimpleNamespace(runtime_mcp={}),
                          query=Mock(return_value={'ok': True}), execute=Mock(return_value={'ok': True}))
    if not hasattr(app, 'normalize_operation'):
        app.normalize_operation = lambda operation: ('workspace.' + operation
            if operation in {'search', 'read', 'read_many', 'finish'} else operation)
    owner = SimpleNamespace(application=Mock(return_value=app),
                            resources=SimpleNamespace(start_services=lambda: None),
                            current_root=lambda: str(tmp_path), close=lambda: None)
    monkeypatch.setattr(server, 'ServerOwner', lambda root: owner)
    return server.create_server(tmp_path, legacy_tools=legacy_tools), app


def value(result):
    if isinstance(result, tuple):
        return result[1]
    if isinstance(result, dict):
        return result
    return json.loads(''.join(item.text for item in result if hasattr(item, 'text')))


def test_default_tools_and_direct_guide_selectors(monkeypatch, tmp_path):
    mcp, app = fixture_server(monkeypatch, tmp_path)
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {'guide', 'read', 'write', 'finish'}
    guide_description = next(t.description for t in tools if t.name == 'guide')
    assert 'only a signature, not a replacement for a procedure' in guide_description
    schemas = {t.name: t.inputSchema for t in tools}
    assert schemas['read']['properties']['detail']['enum'] == ['compact', 'full']
    asyncio.run(mcp.call_tool('guide', {}))
    app.query.assert_called_with('procedure', {'name': 'default'})
    asyncio.run(mcp.call_tool('guide', {'operation': 'search'}))
    app.query.assert_called_with('describe', {'operation': 'workspace.search'})
    asyncio.run(mcp.call_tool('guide', {'catalog': True}))
    app.query.assert_called_with('describe', {})
    before = app.query.call_count
    with pytest.raises(Exception, match='Choose procedure'):
        asyncio.run(mcp.call_tool('guide', {'procedure': 'workspace', 'catalog': True}))
    assert app.query.call_count == before


def test_dedicated_finish_exposes_bounded_scope_and_preserves_runtime_guards(monkeypatch, tmp_path):
    mcp, app = fixture_server(monkeypatch, tmp_path)
    tools = asyncio.run(mcp.list_tools())
    finish = next(tool for tool in tools if tool.name == 'finish')
    assert finish.annotations.readOnlyHint is False
    assert finish.annotations.destructiveHint is True
    assert finish.annotations.idempotentHint is True
    assert finish.inputSchema['required'] == ['cycle_id']
    app.execute.return_value = {'status': 'blocked', 'incoming_dependencies': ['other-card']}
    result = value(asyncio.run(mcp.call_tool('finish', {
        'cycle_id': 'selected', 'expected_revision': 'revision', 'confirmation': 'Delete these files',
        'work_root': str(tmp_path), 'context': 'project',
    })))
    assert result['status'] == 'blocked'
    assert result['incoming_dependencies'] == ['other-card']
    app.execute.assert_called_once_with('workspace.finish', {
        'cycle_id': 'selected', 'expected_revision': 'revision',
        'confirmation': 'Delete these files', 'cancel': False,
    })
    with pytest.raises(Exception, match='context and workspace disagree'):
        asyncio.run(mcp.call_tool('finish', {'cycle_id': 'selected', 'context': 'one', 'workspace': 'two'}))
    assert app.execute.call_count == 1


def test_initialize_delivers_native_tool_routing_and_preserves_real_tool_names(monkeypatch, tmp_path):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    mcp, app = fixture_server(monkeypatch, tmp_path)

    async def inspect_protocol():
        lowlevel = mcp._mcp_server
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with anyio.create_task_group() as tasks:
                async def serve():
                    await lowlevel.run(*server_streams, lowlevel.create_initialization_options())
                tasks.start_soon(serve)
                async with ClientSession(*client_streams) as session:
                    initialized = await session.initialize()
                    instructions = initialized.instructions
                    assert "Contextopus MCP tools advertised by this client" in instructions
                    assert "native names may include a server prefix" in instructions
                    assert "work_root identifies Contextopus data" in instructions
                    assert "Do not replace Contextopus operations with shell commands" in instructions
                    assert "Native skill loading and obtaining declared external sources" in instructions
                    assert "existing approval rules" in instructions
                    assert "only an operation signature and does not replace a procedure" in instructions
                    assert "reload when changed or lost" in instructions
                    tools = (await session.list_tools()).tools
                    assert {tool.name for tool in tools} == {"guide", "read", "write", "finish"}
                    # Shell-specific aliases belong to the client, not the server catalog.
                    assert "mcp_contextopus_guide" not in instructions
                    await session.call_tool("guide", {})
                tasks.cancel_scope.cancel()

    asyncio.run(inspect_protocol())
    app.query.assert_called_once_with("procedure", {"name": "default"})


def test_workspace_procedure_requires_first_rules_read_and_inherited_query_settings(tmp_path):
    from contextopus.startup import Application

    with Application(tmp_path, discover=False) as app:
        install_workspace(tmp_path, app=app)
        procedure = app.query("procedure", {"name": "workspace"})["content"]
    independent = next(line for line in procedure.splitlines() if line.startswith("- **Independent retrieval:**"))
    assert independent.index('workspace.settings_read(fields=["reread_instructions"])') < independent.index("workspace.instructions_read") < independent.index("workspace.search with inherited settings")
    assert "obtain and read the complete workspace.instructions_read" in procedure
    assert "Read workspace.project_read when" in procedure
    assert "it never replaces the first reading" in procedure
    assert "inherit the selected context's algorithm and limits" in procedure
    assert "omit overrides rather than inventing them" in procedure


@pytest.mark.parametrize('operation', ['search', 'read', 'read_many'])
def test_workspace_signature_aliases_deliver_shared_preconditions_without_reading_rules(
    monkeypatch, tmp_path, operation,
):
    from contextopus.startup import Application
    from workspace import server as workspace_server
    from workspace.operations import _RETRIEVAL_PRECONDITIONS

    runtime = Mock(side_effect=AssertionError('signature help must not open a Workspace runtime'))
    monkeypatch.setattr(workspace_server, 'runtime', runtime)
    with Application(tmp_path, discover=False) as app:
        install_workspace(tmp_path, app=app)
        monkeypatch.setattr(workspace_module(app, 'server'), 'runtime', runtime)
        query = Mock(wraps=app.query)
        app.query = query
        mcp, _ = fixture_server(monkeypatch, tmp_path, app=app)
        results = [value(asyncio.run(mcp.call_tool('guide', {'operation': alias})))
                   for alias in (operation, 'workspace.' + operation)]
        assert results[0] == results[1]
        result = results[0]
        assert result['operation'] == 'workspace.' + operation
        assert result['kind'] == 'query'
        assert result['arguments_schema'] == app.registry.describe(result['operation'])['arguments_schema']
        assert result['description'].startswith(_RETRIEVAL_PRECONDITIONS)
        assert 'operation signature does not replace it' in result['description']
        assert 'workspace.settings_read' in result['description']
        assert 'complete current required instructions' in result['description']
        assert 'workspace.startup for work' in result['description']
        assert 'workspace.instructions_read for independent retrieval' in result['description']
        assert 'same task/context' in result['description']
        assert 'false never skips the first full reading' in result['description']
        assert query.call_count == 2
        for call in query.call_args_list:
            assert call.args == ('describe', {'operation': 'workspace.' + operation})
        runtime.assert_not_called()


def test_workspace_search_help_preserves_nullable_optional_override_schema(monkeypatch, tmp_path):
    from pydantic import ValidationError
    from contextopus.startup import Application
    from workspace.operations import _ARGUMENT_MODELS

    with Application(tmp_path, discover=False) as app:
        install_workspace(tmp_path, app=app)
        mcp, _ = fixture_server(monkeypatch, tmp_path, app=app)
        signature = value(asyncio.run(mcp.call_tool('guide', {'operation': 'workspace.search'})))
    assert "inherit the selected context's algorithm and limits" in signature['description']
    assert 'unless the user explicitly supplied them' in signature['description']
    schema = signature['arguments_schema']
    n = schema['properties']['N']
    assert n['anyOf'] == [{'type': 'integer'}, {'type': 'null'}]
    assert n['default'] is None
    assert 'N' not in schema.get('required', [])
    assert 'omit or pass null to inherit the configured limit' in n['description']
    assert schema['properties']['output']['default'] == 'materials'
    assert schema['additionalProperties'] is False
    model = _ARGUMENT_MODELS['search']
    assert model.model_validate({'query': 'facts'}).N is None
    assert model.model_validate({'query': 'facts', 'N': None}).N is None
    assert model.model_validate({'query': 'facts', 'N': 10}).N == 10
    with pytest.raises(ValidationError):
        model.model_validate({'query': 'facts', 'N': '10'})


def test_core_signature_and_catalog_do_not_gain_workspace_prerequisites(monkeypatch, tmp_path):
    from contextopus.startup import Application

    with Application(tmp_path, discover=False) as app:
        mcp, _ = fixture_server(monkeypatch, tmp_path, app=app)
        expected = app.registry.describe('core.status')
        result = value(asyncio.run(mcp.call_tool('guide', {'operation': 'core.status'})))
        assert result == expected
        assert 'INSTRUCTIONS' not in result['description']
        catalog = value(asyncio.run(mcp.call_tool('guide', {'catalog': True})))
        assert catalog == app.registry.describe()
        assert set(catalog) == {'operations'}


def test_mcp_search_inherits_saved_n_and_keeps_explicit_override(monkeypatch, tmp_path):
    from contextopus.startup import Application
    from contextopus.core.resources import ProcessResources
    from workspace.configuration import WorkspaceSettings as Settings, WORKSPACE_CREATION_DEFAULTS
    from workspace.cycles import CycleRuntime
    from workspace import server as workspace_server
    from test_runtime import Factory

    factory = Factory()
    runtime = CycleRuntime(tmp_path, 'one', provider_factory=factory)
    try:
        runtime.workspace_create(
            'seed', '# Rules\nRead these rules before independent retrieval.\n', '# Project\nTest scope.',
            'Create this isolated search fixture',
            settings={**WORKSPACE_CREATION_DEFAULTS, 'git_history_enabled': False},
            project_description='Tests request limits against saved context settings.',
        )
        for number in range(3):
            runtime.engine.create('# Fact ' + str(number), description='Verified fixture fact.')
        settings = Settings(runtime.control.layout)
        preview = settings.preview({'selection': {'algorithm': 'nearest_n', 'parameters': {'N': 2}}},
                                   module='workspace', context='one')
        settings.apply(preview['preview_revision'])
    finally:
        runtime.close()

    monkeypatch.setattr(workspace_server, '_runtimes', {})
    with ProcessResources(factory) as resources, Application(
        tmp_path, 'one', resources=resources, discover=False,
    ) as app:
        install_workspace(tmp_path, app=app)
        try:
            mcp, _ = fixture_server(monkeypatch, tmp_path, app=app)
            for override, count, effective_n in [({}, 2, 2), ({'N': None}, 2, 2), ({'N': 10}, 3, 10)]:
                result = value(asyncio.run(mcp.call_tool('read', {
                    'operation': 'workspace.search', 'work_root': str(tmp_path), 'context': 'one',
                    'arguments': {'query': 'facts', 'types': ['knowledge'], **override},
                })))
                assert len(result['results']) == count
                assert result['diagnostics']['effective_settings'] == {
                    'algorithm': 'nearest_n', 'parameters': {'N': effective_n}}
            assert settings.snapshot(module='workspace', context='one')['values']['selection'] == {
                'algorithm': 'nearest_n', 'parameters': {'N': 2}}
        finally:
            workspace_server.close_runtimes()


def test_legacy_aliases_are_explicit_full_responses(monkeypatch, tmp_path):
    mcp, app = fixture_server(monkeypatch, tmp_path, legacy_tools=True)
    assert {t.name for t in asyncio.run(mcp.list_tools())} == {
        'guide', 'read', 'write', 'finish', 'contextopus_query', 'contextopus_execute', 'pcw_query', 'pcw_execute'}
    result = {'results': [dict(material(), excerpt='Retained body')]}
    app.query.return_value = result
    assert value(asyncio.run(mcp.call_tool('contextopus_query', {'operation': 'workspace.search'}))) == result


def test_transport_presents_only_after_operation_returns_and_retains_write_failures(monkeypatch, tmp_path):
    mcp, app = fixture_server(monkeypatch, tmp_path)
    result = {'results': [dict(material(), excerpt='Fragment')], 'search_event_id': 'e'}
    observed = []
    def query(operation, arguments):
        observed.append(copy.deepcopy(result))  # Stand-in for the completed journal.
        return result
    app.query.side_effect = query
    compact = value(asyncio.run(mcp.call_tool('read', {'operation': 'workspace.search'})))
    assert 'excerpt' not in compact['results'][0]
    assert observed[0]['results'][0]['excerpt'] == result['results'][0]['excerpt'] == 'Fragment'
    app.execute.side_effect = ValueError('Current confirmation is required')
    with pytest.raises(Exception, match='confirmation'):
        asyncio.run(mcp.call_tool('write', {'operation': 'workspace.proposal_apply'}))
    app.execute.assert_called_once()
    with pytest.raises(Exception):
        asyncio.run(mcp.call_tool('write', {'operation': 'workspace.proposal_apply', 'detail': 'invalid'}))
    app.execute.assert_called_once()


def test_coordinate_conflict_fails_before_dispatch(monkeypatch, tmp_path):
    mcp, app = fixture_server(monkeypatch, tmp_path)
    with pytest.raises(Exception, match='disagree'):
        asyncio.run(mcp.call_tool('read', {'operation': 'core.status', 'context': 'one', 'workspace': 'two'}))
    app.query.assert_not_called()


def test_native_tool_selection_and_remote_protocol_are_deliberate():
    assert select_read_tool(['guide', 'read', 'write']) == 'read'
    assert select_read_tool(['mcp__contextopus__read']) == 'mcp__contextopus__read'
    assert select_read_tool(['contextopus_query']) is None
    assert select_read_tool(['contextopus_query'], legacy_tools=True) == 'contextopus_query'
    for legacy, query, execute in [(False, 'read', 'write'), (True, 'contextopus_query', 'contextopus_execute')]:
        app = RemoteApplication('/tmp/fixture', legacy_tools=legacy)
        try:
            app._sync = Mock(return_value={})
            app.query('core.status')
            app._sync.assert_called_with(query, 'core.status', None)
            app.execute('core.init')
            app._sync.assert_called_with(execute, 'core.init', None)
        finally:
            app.close()


def test_unrelated_module_and_historical_payload_are_not_reinterpreted():
    payload = {'id': 'custom', 'path': 'own', 'status': None, 'links': ['raw'],
               'link_targets': [{'id': 'raw'}], 'pending': [], 'missing': False,
               'analytics': {'status': 'recorded'}}
    assert present(payload, operation='third.query') == payload
    assert present(payload, operation='workspace.analytics_read') == payload


def test_real_operation_journal_keeps_candidates_before_compact_transport(monkeypatch, tmp_path):
    from contextopus.startup import Application
    from contextopus.core.operations import OperationSpec
    from contextopus.core.filtering import select
    from contextopus.core.search_history import SearchHistory
    source = select([{'key': ['demo', 'one', str(i)], 'distance': i / 10,
                      'revision': 'r1', 'excerpt': 'Source fragment'} for i in range(2)],
                    {'algorithm': 'nearest_n'})
    with Application(tmp_path, discover=False) as app:
        app.registry._operations['core.search'] = OperationSpec(
            'core.search', 'query', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                                     'required': ['query'], 'additionalProperties': False},
            lambda context, query: source)
        mcp, _ = fixture_server(monkeypatch, tmp_path, app=app)
        response = value(asyncio.run(mcp.call_tool('read', {
            'operation': 'core.search', 'arguments': {'query': 'Private query'}})))
        assert 'candidates' not in response['diagnostics']
        assert all('excerpt' not in item for item in response['results'])
        evidence = SearchHistory(app.journal).read(response['search_event_id'])
        assert len(evidence['diagnostics']['candidates']) == 2
        assert evidence['diagnostics']['counts'] == source['diagnostics']['counts']
        assert source['results'][0]['excerpt'] == 'Source fragment'
        assert 'candidates' in source['diagnostics']


def test_registered_lightweight_settings_schema_and_free_rule_template(tmp_path):
    from contextopus.startup import Application
    import yaml
    with Application(tmp_path, discover=False) as app:
        install_workspace(tmp_path, app=app)
        schema = app.query('describe', {'operation': 'core.settings'})['arguments_schema']['properties']
        assert {'fields', 'agent', 'agent_model'} <= schema.keys()
        assert app.query('describe', {'operation': 'workspace.settings_read'})['kind'] == 'query'
        assert app.query('describe', {'operation': 'workspace.settings_preview'})['kind'] == 'execute'
        assert app.query('describe', {'operation': 'workspace.settings_apply'})['kind'] == 'execute'
        defaults = app.query('procedure', {'name': 'default-instructions'})
        assert not defaults['content'].startswith('---')
        from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
        assert defaults['creation_defaults'] == {**WORKSPACE_CREATION_DEFAULTS, 'reread_instructions': False}
        for name in ('workspace', 'workspace-search', 'workspace-settings', 'workspace-create', 'workspace-context', 'workspace-analyze', 'modules'):
            from importlib.resources import files
            skill = ((SOURCE / 'package/workspace/skills' / name / 'SKILL.md').read_text()
                     if name.startswith('w') else files('contextopus').joinpath('resources', 'skills', name, 'SKILL.md').read_text())
            metadata = yaml.safe_load(skill.split('---', 2)[1])
            assert metadata['name'] == name and metadata['description']


def test_guide_exposes_runtime_proposal_change_contract(monkeypatch, tmp_path):
    from typing import get_type_hints
    from pydantic import ValidationError
    from contextopus.startup import Application
    from workspace.cycles import ProposalChange, PROPOSAL_CHANGE_FIELDS
    from workspace.operations import _ARGUMENT_MODELS

    with Application(tmp_path, discover=False) as app:
        install_workspace(tmp_path, app=app)
        mcp, _ = fixture_server(monkeypatch, tmp_path, app=app)
        result = value(asyncio.run(mcp.call_tool('guide', {
            'operation': 'workspace.proposal_prepare'})))
        schema = result['arguments_schema']
        ref = schema['properties']['changes']['items']['$ref'].rsplit('/', 1)[1]
        change_schema = schema['$defs'][ref]
        assert set(change_schema['properties']) == PROPOSAL_CHANGE_FIELDS == set(get_type_hints(ProposalChange))
        assert change_schema['additionalProperties'] is False
        assert 'title' not in change_schema['properties']
        assert 'heading' in change_schema['description']
        model = _ARGUMENT_MODELS['proposal_prepare']
        # Existing valid JSON inputs retain optional nulls, custom vocabulary,
        # empty lists and explicit false rather than being coerced or omitted.
        changes = [
            {'key': 'new', 'material_id': None, 'content': '# Heading',
             'description': 'Reviewed subject', 'type': 'custom-kind',
             'status': None, 'links': [], 'delete': False, 'path': 'custom.md'},
            {'material_id': 'existing', 'expected_revision': 'revision', 'status': None},
            {'material_id': 'existing', 'expected_revision': 'revision', 'delete': True},
        ]
        assert model.model_validate({'cycle_id': 'cycle', 'changes': changes}).changes == changes
        with pytest.raises(ValidationError, match='title'):
            model.model_validate({'cycle_id': 'cycle', 'changes': [
                {'key': 'new', 'content': '# Heading', 'title': 'Guessed field'}]})


@pytest.mark.parametrize('payload', [[], [{'id':'one'}], [{'id':'one'},{'id':'two'}], False, 0, None, 'ordinary text'])
def test_mcp_nonobject_json_results_keep_shape_in_one_content_block(monkeypatch, tmp_path, payload):
    mcp, app = fixture_server(monkeypatch, tmp_path)
    app.query.return_value = payload
    result = asyncio.run(mcp.call_tool('read', {'operation':'core.modules','detail':'full'}))
    assert len(result) == 1
    assert value(result) == payload


