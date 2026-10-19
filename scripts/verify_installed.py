"""Verify an already installed candidate using temporary data, without model calls.

Run with the clean environment's interpreter. This script does not install,
start an OS service, modify native-client configuration or access a user context.
"""
import argparse
from importlib import metadata
import json
import shutil
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

WORKSPACE_SKILLS = {'w', 'w-search', 'w-create', 'w-settings', 'w-context', 'w-analyze'}


def planned_skills(root):
    """Inspect a native integration preview without writing client configuration."""
    from contextopus.integrations import plan
    preview = plan('codex', root / 'client-preview', root=root)
    return {Path(item['path']).parent.name for item in preview['writes']
            if Path(item['path']).name == 'SKILL.md'}


def workspace_smoke(root, module, expected_version):
    """Explicitly install the separate artifact, then verify disable and restart."""
    from contextopus.startup import Application

    module = Path(module).resolve()
    destination = root / 'modules/workspace'
    if module.is_dir():
        shutil.copytree(module, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    else:
        with zipfile.ZipFile(module) as archive:
            for name in archive.namelist():
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or not name.startswith('workspace/'):
                    raise ValueError('Unsafe Workspace module archive member: ' + name)
            archive.extractall(root / 'modules')
    manifest = json.loads((destination / 'module.json').read_text())
    if manifest['id'] != 'workspace':
        raise ValueError('Expected the separate Workspace folder module')
    base_manifest = json.loads((destination / 'package/workspace/resources/base_context/manifest.json').read_text())
    if base_manifest['version'] != manifest['version']:
        raise ValueError('Workspace base-context version differs from its module release')
    storage = root / 'core/storage/modules/workspace.sqlite3'
    with Application(root, discover=False) as app:
        if storage.exists() or 'workspace' in app.registry.modules or planned_skills(root) != {'m'}:
            raise ValueError('Workspace discovery unexpectedly activated module content')
        result = app.execute('core.module_install', {'module': 'workspace'})
        if result['state'] != 'enabled' or not storage.is_file():
            raise ValueError('Workspace explicit installation did not prepare domain storage')
        for name in ('workspace', 'search', 'instructions', 'upgrade', 'reference/required-sources'):
            if not app.query('procedure', {'name': name})['content'].strip():
                raise ValueError('An installed Workspace procedure is empty')
        defaults = app.query('procedure', {'name': 'default-instructions'})
        if defaults['content'].startswith('---') or defaults['creation_defaults']['reread_instructions'] is not False:
            raise ValueError('Installed Workspace creation rules/defaults are stale')
        if defaults['creation_defaults']['cleanup_approval_required'] is not False:
            raise ValueError('Installed Workspace cleanup default is stale')
        for operation, kind in [('workspace.settings_read', 'query'),
                                ('workspace.settings_preview', 'execute'),
                                ('workspace.settings_apply', 'execute'),
                                ('workspace.reserve', 'execute'),
                                ('workspace.reservations', 'query'),
                                ('workspace.reservation_renew', 'execute'),
                                ('workspace.reservation_release', 'execute')]:
            if app.query('describe', {'operation': operation})['kind'] != kind:
                raise ValueError('Workspace operation registration is incomplete')
        if app.query('workspace.contexts')['total'] != 0:
            raise ValueError('Temporary Workspace unexpectedly contains user contexts')
        if planned_skills(root) != WORKSPACE_SKILLS | {'m'}:
            raise ValueError('Enabled Workspace skills were not discovered through its manifest')
        if app.resources.providers._entries:
            raise ValueError('Workspace installation unexpectedly loaded an embedding model')
        app.execute('core.module_disable', {'module': 'workspace'})
        if 'workspace' in app.index.source_adapters or planned_skills(root) != {'m'}:
            raise ValueError('Disabled Workspace retained its RAG source or skills')
        if any(name.startswith('workspace.') for name in app.registry._operations):
            raise ValueError('Disabled Workspace retained its operations')
    with Application(root, discover=False) as restored:
        if 'workspace' in restored.registry.modules:
            raise ValueError('Owner restart ignored persisted Workspace disablement')
        restored.execute('core.module_enable', {'module': 'workspace'})
        if restored.query('workspace.contexts')['total'] != 0:
            raise ValueError('Workspace failed to reactivate after owner restart')
        if restored.resources.providers._entries:
            raise ValueError('Workspace restart unexpectedly loaded an embedding model')
    return {'module': 'workspace', 'version': manifest['version'],
            'core_version': expected_version, 'artifact': str(module),
            'installed_via': 'core.module_install', 'separate_storage': True,
            'disable_and_restart_verified': True, 'enabled_skill_count': 7,
            'base_context_version': base_manifest['version']}



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-module', type=Path, required=True, help='Built folder or ZIP')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='workspace-installed-check-') as temporary:
        print(json.dumps(workspace_smoke(Path(temporary) / 'root', args.workspace_module,
                                        metadata.version('contextopus-core')), indent=2))
