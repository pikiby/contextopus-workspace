"""0.8 memory-only cycle and deterministic token-window regressions."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pcw_runtime import PCWRuntime, PCWError
from pcw_runtime.chunking import TokenChunker, default_chunking, validate_chunking
from pcw_runtime.embeddings import EmbeddingError, default_config
from test_runtime import Factory, CharacterTokenizer, memory, cutoff


def chunker(size=10, overlap=3, tokenizer=None):
    provider = Factory()(default_config())
    if tokenizer is not None:
        provider.tokenizer = lambda: tokenizer
    config = {**default_chunking(), 'chunk_size_tokens': size, 'overlap_tokens': overlap}
    return TokenChunker(config, default_config(), provider)


@pytest.mark.parametrize('length,positions', [(0, []), (5, [(0,5)]), (10,[(0,10)]),
    (11,[(0,10),(7,11)]), (17,[(0,10),(7,17)]), (18,[(0,10),(7,17),(14,18)])])
def test_token_windows_cover_source_without_extra_overlap_tail(length, positions):
    text='abcdefghijklmnopqrstuvwxyz'[:length]
    pieces=chunker().split(text)
    assert [(p['token_start'],p['token_end']) for p in pieces]==positions
    for p in pieces:
        assert p['text']==text[p['char_start']:p['char_end']]
    if pieces:
        rebuilt=pieces[0]['text']
        for previous,p in zip(pieces,pieces[1:]):
            assert previous['text'][-3:]==p['text'][:3]
            rebuilt += p['text'][previous['char_end']-p['char_start']:]
        assert rebuilt==text


@pytest.mark.parametrize('size,overlap', [(0,0),(10,0),(10,10),(3,5),(True,1),(10,1.5)])
def test_bad_chunk_configuration_is_rejected(size,overlap):
    with pytest.raises(EmbeddingError):
        chunker(size,overlap)


def test_unicode_multi_token_characters_are_not_split():
    class UnicodeTokenizer(CharacterTokenizer):
        def __call__(self,text,**kwargs):
            offsets=[(i,i+1) for i,c in enumerate(text) for _ in range(2 if c=='🙂' else 1)]
            return {'input_ids':list(range(len(offsets))), 'offset_mapping':offsets}
    text='ab🙂def🙂ghijk'
    pieces=chunker(5,2,UnicodeTokenizer()).split(text)
    assert pieces[-1]['char_end']==len(text)
    for p in pieces:
        assert p['token_end']-p['token_start']<=5
        assert p['text']==text[p['char_start']:p['char_end']]
        assert '\ufffd' not in p['text']
    for a,b in zip(pieces,pieces[1:]):
        assert a['char_start']<b['char_start']<a['char_end']


def test_input_prefix_and_special_tokens_count_towards_limit():
    c=chunker(10,3)
    c.embeddings['profile'].update(max_input_tokens=10)
    with pytest.raises(EmbeddingError,match='formatting'):
        c.split('1234567890')


def test_normal_save_indexes_without_ledger_and_returns_compact_receipt(memory):
    runtime,factory=memory
    text='# Хранение\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nprint(1)\n```\n'
    result=runtime.create(text)
    assert result['index_status']=='indexed' and result['parts']==1
    assert 'content' not in result and 'executor_session_id' not in result
    assert runtime.read(result['id'])['content']==text
    assert factory.calls[-1][1:]==('document',[text])
    assert not (runtime.control.root/'task-ledger.json').exists()
    assert runtime.search(text,**{k:v for k,v in cutoff().items() if k!='metric'})['results'][0]['id']==result['id']
    assert len(list(runtime.knowledge.glob('*.md')))==1


def test_failed_index_keeps_saved_body_and_retry_uses_saved_text(memory):
    runtime,factory=memory
    with patch.object(runtime,'_get_chunker',side_effect=EmbeddingError('Tokenizer unavailable')):
        saved=runtime.create('Durable facts')
    assert saved['index_status']=='pending_preparation'
    assert saved['index_error']=='Tokenizer unavailable'
    assert runtime.read(saved['id'])['content']=='Durable facts'
    runtime.close()
    reopened=PCWRuntime(runtime.work_root,'project',provider_factory=factory)
    try:
        assert reopened.startup()['pending_count']==1
        assert reopened.reconcile()['status']=='ok'
        assert reopened.status()['pending_count']==0
        assert len(list(reopened.knowledge.glob('*.md')))==1
    finally: reopened.close()


def test_failed_update_withholds_stale_search_and_cas_still_applies(memory):
    runtime,factory=memory
    saved=runtime.create('old facts')
    with patch.object(runtime._get_provider(),'embed',side_effect=EmbeddingError('offline')):
        changed=runtime.update(saved['id'],'new facts',saved['revision'])
    assert changed['index_status']=='pending_preparation'
    assert runtime.table.count_rows()==0
    assert runtime.search('old facts')['results']==[]
    with pytest.raises(PCWError,match='changed since'):
        runtime.update(saved['id'],'lost overwrite',saved['revision'])
    assert runtime.reconcile()['status']=='ok'
    assert runtime._rows()[0]['text']=='new facts'


def test_external_edit_during_embedding_is_not_published(memory):
    runtime,factory=memory
    saved=runtime.create('old text')
    path=runtime.root/saved['path']
    factory.on_embed=lambda:path.write_text(path.read_text().replace('new text','external edit'))
    changed=runtime.update(saved['id'],'new text')
    assert changed['index_status']=='pending_preparation'
    assert runtime.table.count_rows()==0
    assert runtime.read(saved['id'])['content']=='external edit'
    factory.on_embed=None
    assert runtime.reconcile()['status']=='ok'


def test_rechunk_switch_is_atomic_and_preserves_knowledge(memory):
    runtime,factory=memory
    saved=runtime.create('abcdefghijklmnopqrstuvwxyz')
    before=(runtime.root/saved['path']).read_bytes()
    config=runtime.active['config']
    chunks={**default_chunking(),'chunk_size_tokens':10,'overlap_tokens':3}
    result=runtime.switch_embeddings(config,cutoff(),chunks)
    assert result['status']=='ok'
    assert len(runtime._rows())==4
    assert runtime.status()['pending_count']==0
    assert (runtime.root/saved['path']).read_bytes()==before
    calls=len(factory.calls)
    runtime.update(saved['id'],'abcdefghijklmnopqrstuvwxyz')
    assert len(factory.calls)==calls


def test_partial_row_write_is_not_accepted_as_indexed(memory):
    runtime,_=memory
    saved=runtime.create('abc')
    row=runtime._rows()[0]
    runtime.table.update(values={'part_count':2})
    assert runtime.status()['pending_count']==1
    assert runtime.table.count_rows()==0
    assert runtime.reconcile()['status']=='ok'


def test_existing_workspace_upgrade_does_not_change_files_or_resume_tasks(memory):
    runtime,factory=memory
    saved=runtime.create('Preserve my notes')
    (runtime.root/'current.md').write_text('Legacy task state')
    body=(runtime.root/saved['path']).read_bytes()
    ledger={'active':'old', 'tasks':{'old':{'status':'active','request':{'goal':'blocked old goal'},'assignments':{}}}}
    (runtime.control.root/'task-ledger.json').write_text(json.dumps(ledger))
    active=copy.deepcopy(runtime.active)
    active.pop('chunking');active.pop('index_format')
    (runtime.state/'active.json').write_text(json.dumps(active))
    calls=len(factory.calls)
    runtime.close()
    new=PCWRuntime(runtime.work_root,'project',provider_factory=factory)
    try:
        startup=new.startup()
        assert 'current' not in startup
        assert (new.root/'current.md').read_text()=='Legacy task state'
        assert startup['pending_count']==1 and len(factory.calls)==calls
        assert not {'task','environment','tools_map','assignments'} & startup.keys()
        assert (new.root/saved['path']).read_bytes()==body
        assert new.reconcile()['status']=='ok'
        assert json.loads((new.control.root/'task-ledger.json').read_text())==ledger
    finally:new.close()


def test_legacy_incorrect_revision_remains_excluded_until_corrected(memory):
    runtime,_=memory
    saved=runtime.create('Known incorrect fact')
    ledger={'tasks':{'old':{'assignments':{'check':{'affected_materials':{saved['id']:saved['revision']},'discrepancy_reason':'Wrong'}}}}}
    (runtime.control.root/'task-ledger.json').write_text(json.dumps(ledger))
    assert runtime.read(saved['id'])['knowledge_status']=='needs_correction'
    assert runtime.search('Known incorrect fact')['results']==[]
    corrected=runtime.update(saved['id'],'Corrected fact')
    assert 'knowledge_status' not in corrected
    assert runtime.search('Corrected fact',d_max=2,min_jump=10)['results']


def test_remote_source_requires_explicit_tokenizer(memory):
    runtime,_=memory
    config=copy.deepcopy(default_config())
    config['provider']={'type':'openai-compatible','base_url':'http://localhost:12345'}
    with pytest.raises(EmbeddingError,match='explicit compatible tokenizer'):
        runtime.switch_embeddings(config,cutoff())


def test_switch_rechecks_earlier_sources_before_publication(memory):
    runtime,factory=memory
    a=runtime.create('alpha');runtime.create('beta')
    path=runtime.root/a['path'];before=copy.deepcopy(runtime.active)
    calls=0
    def edit():
        nonlocal calls
        calls+=1
        if calls==2:path.write_text(path.read_text().replace('alpha','changed'))
    factory.on_embed=edit
    config=copy.deepcopy(before['config']);config['profile']['revision']='next'
    result=runtime.switch_embeddings(config,cutoff())
    assert result['status']=='failed' and result['active_unchanged']
    assert runtime.active==before
    assert runtime.read(a['id'])['content']=='changed'


def test_save_confirmation_failure_still_returns_durable_id(memory):
    runtime,_=memory
    save=runtime._save
    def written_then_error(material):
        save(material)
        raise OSError('fsync failed after publication')
    with patch.object(runtime,'_save',side_effect=written_then_error):
        result=runtime.create('Already written')
    assert result['status']=='partial' and result['next_action']=='read'
    assert runtime.read(result['id'])['content']=='Already written'
    assert len(list(runtime.knowledge.glob('*.md')))==1
    assert runtime.reconcile()['status']=='ok'


def test_remote_embeddings_complete_cycle_with_compatible_tokenizer(memory):
    import os
    from pcw_runtime.embeddings import HTTPProvider
    runtime,_=memory
    item=runtime.create('Remote embedding facts')
    cfg=copy.deepcopy(default_config())
    cfg['provider']={'type':'openai-compatible','base_url':'https://example.invalid/v1','api_key_env':'PCW_TEST_KEY'}
    cfg['profile'].update(model='fixture-embedding-model',revision='1',dimensions=3,input_format='raw',query_instruction='')
    chunks={**default_chunking(),'tokenizer':{'type':'huggingface','model':'fixture-tokenizer','revision':'1'}}
    provider=HTTPProvider(cfg)
    sent=[]
    def post(body):
        sent.append(body)
        return {'data':[{'index':i,'embedding':[1.0,2.0,3.0]} for i in reversed(range(len(body['input'])))]}
    with patch.object(provider,'_post',side_effect=post), patch.object(runtime,'_factory',return_value=provider), patch.object(TokenChunker,'_load',return_value=CharacterTokenizer()):
        result=runtime.switch_embeddings(cfg,cutoff(),chunks)
        assert result['status']=='ok'
        new=runtime.create('Second remote fact')
        assert new['index_status']=='indexed'
        assert runtime.search('facts')['results']
        assert all(len(row['vector'])==3 for row in runtime._rows())
    assert all(body['model']=='fixture-embedding-model' and body['encoding_format']=='float' for body in sent)
    assert runtime.read(item['id'])['content']=='Remote embedding facts'
