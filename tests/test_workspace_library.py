from types import SimpleNamespace
from pathlib import Path
from workspace.library import library
from workspace.shared import SharedRuntime
from workspace.runtime import _parse
from workspace_support import prepare_workspace_storage

def test_source_browsing_without_derived_index(tmp_path):
    from contextopus.core.storage import StorageLayout
    root=tmp_path/'root'
    root.mkdir()
    source=root/'modules/workspace/workspace_contexts/project'
    (source/'knowledge').mkdir(parents=True)
    control=root/'modules/workspace/control/project'
    control.mkdir(parents=True)
    (control/'format.json').write_text('{"format_version":6}')
    for i in range(5):
        (source/'knowledge'/f'note-{i}.md').write_text(
            f'---\nid: note-{i}\ntype: '+('task' if i%2 else 'knowledge')+
            f'\ndescription: ALPHA {i}\nlinks: []\n---\n# Note {i}\nComplete text.')
    host=SimpleNamespace(layout=StorageLayout(root),context=SimpleNamespace(workspace=None),
                         resources=SimpleNamespace(acquire=lambda *args: (_ for _ in ()).throw(AssertionError('model loaded'))))
    result=library(host,'records',query='alpha',type='knowledge',offset=1,limit=1)
    assert result['total']==3 and result['has_more']
    assert result['records'][0]['key']==['workspace','project','note-2']
    assert library(host,'record',collection='project',id='note-2')['content'].endswith('Complete text.')
    linked=source/'knowledge/note-0.md'
    linked.write_text(linked.read_text().replace('links: []','links: ["[[note-2|Second]]"]')+'\nSee [[note-2|Second]].')
    record=library(host,'record',collection='project',id='note-0')
    assert len(record['links'])==1
    assert record['links'][0]['key']==['workspace','project','note-2']
    assert not (root/'core').exists()
    assert not (root/'modules/workspace/.rag').exists()
