"""Workspace module contribution; Core can run without importing this package."""

import hashlib
from pathlib import Path


def register(host):
    from .analytics import EventStore
    from .base_context import register as register_base, prepare_setup
    from .configuration import WorkspaceSettings, WORKSPACE_CREATION_DEFAULTS, WORKSPACE_SETTINGS
    from .discovery import register as register_discovery
    from .layout import WorkspaceLayout
    from .operations import register_operations, _DESCRIPTIONS
    from .server import runtime, close_runtimes
    from .source import WorkspaceEligibility

    register_operations(host.registry, provider_factory=host.resources.acquire,
                        instruction_reconcile=lambda: host.execute('core.instructions_reconcile'))
    register_discovery(host)
    from .library import register as register_library
    register_library(host)
    register_base(host)
    host.index.register_source('workspace', WorkspaceEligibility(host.layout, host.resources.acquire))
    host.settings_provider(WorkspaceSettings)
    for name in _DESCRIPTIONS:
        host.alias(name, 'workspace.' + name)

    package = Path(__file__).parent
    procedures = package / 'resources/procedures'
    for path in sorted(procedures.rglob('*.md')):
        name = path.relative_to(procedures).with_suffix('').as_posix()
        def read_procedure(path=path, name=name):
            return {'name': name, 'content': path.read_text(encoding='utf-8')}
        host.procedure(name, read_procedure)
        if name == 'workspace':
            host.procedure('default', read_procedure)
    host.procedure('default-instructions', lambda: {
        'name': 'default-instructions',
        'creation_defaults': {**WORKSPACE_CREATION_DEFAULTS,
                              'reread_instructions': WORKSPACE_SETTINGS['reread_instructions']},
        'content': (package / 'default_instructions.md').read_text(encoding='utf-8'),
    })

    def instructions_revision(context):
        if not context or not context.workspace:
            return None
        layout = WorkspaceLayout(host.layout.root)
        path = layout.workspace_base / context.workspace / 'INSTRUCTIONS.md'
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    host.search_operation('workspace.search', instruction_revision=instructions_revision)
    host.search_operation('workspace.projects_search', instruction_revision=instructions_revision)

    def initialize():
        layout = WorkspaceLayout(host.layout.root)
        if (layout.root / '.pcw').is_dir():
            return
        for path in (layout.root / 'modules/workspace/workspace_contexts',
                     layout.root / 'modules/workspace/control'):
            path.mkdir(parents=True, exist_ok=True)

    def apply_profile(config, chunking):
        from .runtime import SearchConfig
        layout = WorkspaceLayout(host.layout.root)
        contexts = layout.workspace_base
        names = [p.name for p in sorted(contexts.iterdir())
                 if not p.name.startswith('.') and p.is_dir() and (p / 'knowledge').is_dir()] if contexts.is_dir() else []
        if not names:
            return None
        selected = host.context.workspace if host.context.workspace in names else names[0]
        engine = runtime(str(layout.root), selected, provider_factory=host.resources.acquire).engine
        active = host.active_profile()
        return engine.switch_embeddings(config, active['search'] if active else vars(SearchConfig()), chunking)

    def install_apply(revision):
        result = EventStore(host.layout.root).storage_apply(revision)
        initialize()
        return result

    host.hook('install_prepare', lambda: EventStore(host.layout.root).storage_prepare())
    host.hook('install_apply', install_apply)
    host.hook('ready', lambda: EventStore(host.layout.root).storage_ready())
    host.hook('initialize', initialize)
    host.hook('setup', lambda: prepare_setup(host))
    host.hook('apply_profile', apply_profile)
    host.hook('before_relocation', close_runtimes)
    host.hook('close', close_runtimes)
