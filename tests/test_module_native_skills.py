import pytest
from contextopus.integrations.native import plan, integrate, IntegrationConflict
from contextopus.core.module_management import ModuleManager
from contextopus.core.operations import OperationRegistry
from types import SimpleNamespace
import json


def setup_module_root(root):
    module = root / 'modules/sample'
    skill = module / 'package/skills/sample-help'
    (skill / 'scripts').mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: sample-help\ndescription: Sample help\n---\nRead scripts/help.py')
    (skill / 'scripts/help.py').write_text('print("help")\n')
    (skill / 'asset.bin').write_bytes(b'\x00\xff')
    (module / 'module.json').write_text(json.dumps({'id':'sample','version':'1.0','skills':'skills','operations':[]}))
    manager = ModuleManager(SimpleNamespace(layout=SimpleNamespace(root=root),registry=OperationRegistry()))
    return manager, skill








def test_native_updates_previous_managed_w_analyze_description(tmp_path):
    import hashlib
    from workspace_support import install_workspace
    root = tmp_path / 'root'
    install_workspace(root)
    project = tmp_path / 'project'
    integrate(plan('codex', project, root=root))
    skill = project / '.agents/skills/contextopus-workspace-analyze/SKILL.md'
    current = skill.read_bytes()
    previous = current.replace(b'description:', b'description: Previous managed description. #', 1)
    skill.write_bytes(previous)
    manifest_path = project / '.contextopus/codex-integration.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['skills']['.agents/skills/contextopus-workspace-analyze/SKILL.md'] = hashlib.sha256(previous).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    preview = plan('codex', project, root=root)
    integrate(preview)
    assert skill.read_bytes() == current




def test_disabled_workspace_retires_managed_skills_once_and_preserves_edited_skill(tmp_path):
    from workspace_support import install_workspace
    from contextopus.startup import Application
    root, project = tmp_path / 'root', tmp_path / 'project'
    install_workspace(root)
    integrate(plan('codex', project, root=root))
    assert not integrate(plan('codex', project, root=root))['written']
    edited = project / '.agents/skills/contextopus-workspace-search/SKILL.md'
    edited.write_text('User changes remain')
    with Application(root, discover=False) as app:
        app.execute('core.module_disable', {'module': 'workspace'})
    preview = plan('codex', project, root=root)
    paths = [item['path'] for item in preview['removals']]
    assert len(paths) == len(set(paths)) == 5
    assert str(edited) in {item['path'] for item in preview['preserved']}
    integrate(preview)
    assert edited.read_text() == 'User changes remain'
    assert not (project / '.agents/skills/contextopus-workspace/SKILL.md').exists()
    receipt = json.loads((project / '.contextopus/codex-integration.json').read_text())
    assert receipt['module_skills'] == {}
    assert '.agents/skills/contextopus-workspace/SKILL.md' not in receipt['skills']
    assert (project / '.agents/skills/contextopus-modules/SKILL.md').is_file()
