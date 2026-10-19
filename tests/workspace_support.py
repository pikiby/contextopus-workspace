"""Install the separately distributed Workspace fixture through Core lifecycle."""
from pathlib import Path
import shutil


SOURCE = Path(__file__).resolve().parents[1]


def install_workspace(root, app=None):
    root = Path(root)
    target = root / 'modules/workspace'
    target.mkdir(parents=True, exist_ok=True)
    if not (target / 'module.json').exists():
        shutil.copyfile(SOURCE / 'module.json', target / 'module.json')
        shutil.copytree(SOURCE / 'package', target / 'package',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if app is not None:
        return app.execute('core.module_install', {'module': 'workspace'})
    from contextopus.startup import Application
    with Application(root, discover=False) as selected:
        return selected.execute('core.module_install', {'module': 'workspace'})


def workspace_module(app, name):
    import sys
    host = app.module_manager.contributions['workspace']
    return sys.modules[host._namespace + '.' + name]


def prepare_workspace_storage(root):
    """Explicit SQL setup for domain fixtures that do not run Core installation."""
    from workspace.analytics import EventStore
    store = EventStore(root)
    plan = store.storage_prepare()
    if plan['needs_migration']:
        store.storage_apply(plan['revision'])
