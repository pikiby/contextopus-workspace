import json
from contextopus.core.settings import Settings
from contextopus.core.storage import StorageLayout
from contextopus.core.rag import RagIndex, DocumentKey, IndexDocument
from contextopus.core.resources import ProcessResources
from test_runtime import Factory


def test_settings_scope_switch_clears_other_algorithm_parameters(tmp_path):
    layout=StorageLayout(tmp_path)
    config=layout.configuration; config.mkdir(parents=True)
    (config/'settings.json').write_text(json.dumps({'selection':{'algorithm':'distance_jump','parameters':{'multiplier':9}}}))
    (config/'modules').mkdir()
    (config/'modules/demo.json').write_text(json.dumps({'selection':{'algorithm':'nearest_n','parameters':{'N':3}}}))
    context=config/'contexts/demo';context.mkdir(parents=True)
    (context/'one.json').write_text(json.dumps({'selection':{'parameters':{'N':2}}}))
    settings=Settings(layout)
    snapshot=settings.snapshot('demo','one',request={'selection':{'parameters':{'N':1}}})
    assert snapshot['values']['selection']=={'algorithm':'nearest_n','parameters':{'N':1}}
    assert snapshot['sources']['selection.parameters.N']=='request'
    assert settings.snapshot('demo','one')['values']['selection']['parameters']['N']==2
    assert 'multiplier' not in snapshot['values']['selection']['parameters']


def test_offline_vectors_scope_selection_and_metadata_only_snapshot(tmp_path):
    from contextopus.core.embeddings import default_config
    factory=Factory()
    with ProcessResources(factory) as resources:
        index=RagIndex(StorageLayout(tmp_path),resources,embeddings=default_config())
        # Provider is deterministic; query and document vectors are local, no downloads.
        for module,ident,text in [('other','a','outside scope'),('demo','b','inside scope'),('demo','c','second item')]:
            index.index(IndexDocument(DocumentKey(module,'one',ident),'revision-1',text,ident,{}))
        result=index.search_detailed('inside scope',module='demo',collection='one',selection={'algorithm':'none','parameters':{'page_size':1}})
        assert len(result['results'])==1
        assert result['results'][0]['key'][0]=='demo'
        diagnostics=result['diagnostics']
        assert diagnostics['counts']['eligible_documents']==2
        assert diagnostics['counts']['eligible_fragments']==2
        assert diagnostics['counts']['considered_fragments']==2
        assert diagnostics['counts']['selected_materials']==1
        assert all(c['key'][0]=='demo' for c in diagnostics['candidates'])
        assert all(c['revision']=='revision-1' for c in diagnostics['candidates'])
        assert 'inside scope' not in json.dumps(diagnostics)
        assert len(index.search('anything',module='demo',selection={'algorithm':'nearest_n','parameters':{'N':2}}))==2


def test_workspace_uses_enabled_module_algorithm_without_index_injection(tmp_path):
    from test_shared_v4 import context, save
    from contextopus.core.module_management import ModuleManager
    runtime=context(tmp_path,'one',Factory())
    try:
        save(runtime,'# Material\nlocal vector evidence')
        package=tmp_path/'modules/demo/package'
        provider=package/'filter_algorithms/custom';provider.mkdir(parents=True)
        (package.parent/'module.json').write_text(json.dumps({'id':'demo','version':'1.0.0'}))
        description={'id':'custom','version':'1','purpose':'custom','parameters_schema':{'type':'object','additionalProperties':False},'defaults':{},'when_to_use':'test','how_to_adjust':'none'}
        (provider/'description.json').write_text(json.dumps(description))
        (provider/'algorithm.py').write_text("def select(rows,parameters):\n    return rows[:1], {'reason':'custom_workspace_provider'}\n")
        manager=ModuleManager.__new__(ModuleManager);manager.base=tmp_path/'modules'
        layout=StorageLayout(tmp_path);layout.configuration.mkdir(parents=True,exist_ok=True)
        (layout.configuration/'modules.json').write_text(json.dumps({'demo':{'enabled':True,'prepared':manager._digest('demo')}}))
        assert runtime.engine.index.algorithm_registry is None
        found=runtime.search(query='local',selection={'algorithm':'custom'})
        assert found['diagnostics']['algorithm']=='custom'
        assert found['diagnostics']['boundary']['reason']=='custom_workspace_provider'
        assert found['results']
    finally:
        runtime.close()


def test_preview_mode_is_bound_and_returned_changes_cannot_mutate_apply(tmp_path):
    settings=Settings(StorageLayout(tmp_path))
    changes={'selection':{'algorithm':'nearest_n','parameters':{'N':2}}}
    ephemeral=settings.preview(changes,request_only=True)
    saved=settings.preview(changes,request_only=False)
    assert ephemeral['preview_revision']!=saved['preview_revision']
    changes['selection']['parameters']['N']=8
    saved['changes']['selection']['parameters']['N']=7
    assert settings.apply(ephemeral['preview_revision'])['saved'] is False
    result=settings.apply(saved['preview_revision'])
    assert result['settings']['values']['selection']['parameters']['N']==2


def test_modern_selection_overrides_same_layer_legacy_fields_after_save(tmp_path):
    layout=StorageLayout(tmp_path);layout.configuration.mkdir(parents=True)
    (layout.configuration/'settings.json').write_text(json.dumps({'searchN':2,'distance':.8}))
    settings=Settings(layout)
    preview=settings.preview({'selection':{'algorithm':'nearest_n','parameters':{'N':4}}})
    assert preview['effective']['selection']['parameters']['N']==4
    result=settings.apply(preview['preview_revision'])
    assert result['settings']['values']['selection']['parameters']['N']==4
    assert result['settings']['sources']['selection.parameters.N']=='root.configuration'


def test_public_algorithm_catalog_ignores_document_location_filters(tmp_path):
    from contextopus.startup import Application
    with ProcessResources(Factory()) as resources, Application(tmp_path, resources=resources, discover=False) as app:
        app.execute('core.algorithms_refresh')
        preview=app.query('core.settings_preview',{'changes':{'include':['**/*.md'],'exclude':['core/**']}})
        app.execute('core.settings_apply',{'preview_revision':preview['preview_revision']})
        result=app.query('core.algorithms',{'query':'distance','semantic':True,'limit':2})
        assert len(result['results'])==2
        assert all(row['key'][:2]==['core','search_algorithm'] for row in result['results'])


def test_empty_core_search_reports_scope_and_limit_without_loading_model(tmp_path):
    index=RagIndex(StorageLayout(tmp_path))
    answer=index.search_detailed('nothing',module='demo',collection='one',candidate_limit=5)
    diagnostics=answer['diagnostics']
    assert diagnostics['scope']['module']=='demo'
    assert diagnostics['scope']['contexts']==['one']
    assert diagnostics['scope']['settings_context']=='one'
    assert diagnostics['limits']['nearest']==5
    assert diagnostics['candidate_pool_truncated'] is False
    assert diagnostics['counts']['eligible_documents']==0


def test_workspace_candidate_limit_counts_fragments_and_records_scope(tmp_path):
    from test_shared_v4 import context,save
    runtime=context(tmp_path,'one',Factory())
    try:
        save(runtime,'# Long material\n'+('fragment evidence '*120))
        answer=runtime.search(query='evidence',candidate_limit=2,output='fragments',selection={'algorithm':'nearest_n'})
        diagnostics=answer['diagnostics'];counts=diagnostics['counts']
        assert counts['eligible_documents']==2
        assert counts['eligible_fragments']>2
        assert counts['considered_fragments']==2
        assert diagnostics['candidate_pool_truncated'] is True
        assert diagnostics['scope']['module']=='workspace'
        assert diagnostics['scope']['contexts']==['one']
        assert diagnostics['scope']['settings_context']=='one'
    finally:
        runtime.close()


def test_public_disabled_algorithm_remains_visible_and_can_be_repaired(tmp_path):
    import pytest
    from contextopus.startup import Application
    provider=tmp_path/'modules/demo/package/filter_algorithms/custom';provider.mkdir(parents=True)
    (provider.parents[2]/'module.json').write_text(json.dumps({'id':'demo','version':'1.0.0'}))
    description={'id':'custom','version':'1','purpose':'custom','parameters_schema':{'type':'object','additionalProperties':False},'defaults':{},'when_to_use':'test','how_to_adjust':'none'}
    (provider/'description.json').write_text(json.dumps(description))
    marker=tmp_path/'executed.marker'
    (provider/'algorithm.py').write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\ndef select(rows,parameters):\n    return rows[:1], {{'reason':'custom'}}\n")
    with ProcessResources(Factory()) as resources,Application(tmp_path,resources=resources,discover=False) as app:
        app.index.index(IndexDocument(DocumentKey('app_notes','one','one'),'r1','offline evidence','one',{}))
        app.execute('core.module_prepare',{'module':'demo'})
        app.execute('core.module_enable',{'module':'demo'})
        preview=app.query('core.settings_preview',{'changes':{'selection':{'algorithm':'custom'}}})
        app.execute('core.settings_apply',{'preview_revision':preview['preview_revision']})
        assert marker.exists()
        marker.unlink()
        stale=app.query('core.settings_preview',{'changes':{'selection':{'algorithm':'nearest_n'}}})
        app.execute('core.module_disable',{'module':'demo'})
        with pytest.raises(ValueError,match='Configuration changed'):
            app.execute('core.settings_apply',{'preview_revision':stale['preview_revision']})
        snapshot=app.query('core.settings')
        assert snapshot['values']['selection']['algorithm']=='custom'
        assert snapshot['selection_status']['valid'] is False
        assert snapshot['selection_status']['code']=='algorithm_unavailable'
        assert snapshot['selection_status']['next_action']=='core.settings_preview'
        with pytest.raises(ValueError,match='Unknown search algorithm'):
            app.query('core.search',{'query':'offline','module':'app_notes','collection':'one'})
        found=app.query('core.search',{'query':'offline','module':'app_notes','collection':'one','selection':{'algorithm':'nearest_n'}})
        assert found['results']
        assert app.query('core.settings')['values']['selection']['algorithm']=='custom'
        replacement=app.query('core.settings_preview',{'changes':{'selection':{'algorithm':'nearest_n','parameters':{'N':1}}}})
        repaired=app.execute('core.settings_apply',{'preview_revision':replacement['preview_revision']})
        assert repaired['settings']['selection_status']['valid'] is True
        assert repaired['settings']['values']['selection']['algorithm']=='nearest_n'
        assert app.query('core.search',{'query':'offline','module':'app_notes','collection':'one'})['results']
        assert not marker.exists()


def test_core_material_limit_and_candidate_limit_have_separate_units(tmp_path):
    from contextopus.core.embeddings import default_config
    with ProcessResources(Factory()) as resources:
        index = RagIndex(StorageLayout(tmp_path), resources, embeddings=default_config())
        index.index(IndexDocument(DocumentKey('demo', 'one', 'a'), 'r1', 'a' * 6000, 'a.md', {}))
        index.index(IndexDocument(DocumentKey('demo', 'one', 'b'), 'r1', 'second document', 'b.md', {}))
        # Query the exact first indexed part; repeated complete parts of a rank
        # equally, but must occupy only one material slot.
        parts = index.read_rows(index._generic_table(), ['object_id', 'text'])
        query = next(part['text'] for part in parts if part['object_id'] == 'a')
        selection = {'algorithm': 'nearest_n', 'parameters': {'N': 2}}
        complete = index.search_detailed(query, module='demo', collection='one', selection=selection)
        assert {row['key'][-1] for row in complete['results']} == {'a', 'b'}
        counts = complete['diagnostics']['counts']
        assert counts['considered_fragments'] > counts['considered_documents'] == 2
        assert complete['diagnostics']['completeness']['selection_input_complete'] is True
        capped = index.search_detailed(query, module='demo', collection='one', selection=selection,
                                       candidate_limit=1)
        diag = capped['diagnostics']
        assert len(capped['results']) == 1
        assert diag['limits']['candidate_limit'] == 1
        assert diag['limits']['result']['limit'] == 2
        assert diag['completeness']['selection_input_complete'] is False
        assert diag['completeness']['reasons'] == ['candidate_limit']
        assert diag['completeness']['candidate_snapshot_complete'] is True
