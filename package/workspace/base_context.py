"""Versioned product documentation stored as ordinary Workspace records.

The role pointer never selects active instructions. Only an explicit install
applies its exact preview, through the standard Workspace proposal machinery.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
import tempfile

from contextopus.core.storage import sync_directory
from .layout import WorkspaceLayout as StorageLayout
from .cycles import CycleRuntime
from .runtime import PCWError, Material, _parse
from .records import validate_description
from .links import LinkCodec
from .text_io import read_utf8

DEFAULT_CONTEXT = 'contextopus-base'


def _revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def packaged_bundle():
    source = files(__package__).joinpath('resources', 'base_context')
    manifest = json.loads(source.joinpath('manifest.json').read_text(encoding='utf-8'))
    documents = []
    for item in manifest['documents']:
        content = source.joinpath(item['file']).read_text(encoding='utf-8')
        documents.append({**item, 'content': content})
    instructions = files(__package__).joinpath('default_instructions.md').read_text(encoding='utf-8')
    instructions += '\n## Product documentation context\n\nThis context holds packaged product documentation and has no material length limit. Reading these records never changes an active task context or its instructions. Future edits to these rules govern product documentation updates too.\n'
    from .configuration import WORKSPACE_CREATION_DEFAULTS
    return {**manifest, 'documents': documents, 'instructions': instructions,
            'settings': {**WORKSPACE_CREATION_DEFAULTS, 'material_max_tokens': None},
            'project_description': 'Defines the scope and use of the installed Contextopus guides, examples and templates as product reference material.',
            'project': '# Contextopus product documentation\n\nInstalled-version guides, examples and templates. These records are reference material; reading them never changes the active context or its rules.\n'}


class BaseContext:
    def __init__(self, work_root, *, runtime_factory=CycleRuntime, bundle=None):
        self.layout = StorageLayout.resolve(work_root)
        self.path = self.layout.configuration / 'base-context.json'
        self.runtime_factory = runtime_factory
        self.bundle = bundle if bundle is not None else packaged_bundle()

    def _read(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else {}

    def _write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise PCWError('Base context configuration cannot be a symlink')
        fd, temporary = tempfile.mkstemp(prefix='.base-context-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            sync_directory(self.path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _context_path(self, name):
        self.layout._name(name)
        path = self.layout.workspace_base / name
        if path.is_symlink():
            raise PCWError('Base context cannot be a symlink')
        return path

    def _link_codec(self):
        """Read only the source catalog; preview never initializes an engine."""
        catalog, owners = {}, {}
        if self.layout.workspace_base.is_dir():
            for root in sorted(self.layout.workspace_base.iterdir()):
                if root.name.startswith('.') or root.is_symlink() or not root.is_dir():
                    continue
                from .context_document import record_paths
                for path in record_paths(root):
                    if path.is_symlink() or any(p.startswith('.') for p in path.relative_to(root).parts):
                        continue
                    metadata, body = _parse(read_utf8(path))
                    ident = metadata.get('id')
                    if ident is None:
                        continue
                    if ident in catalog:
                        raise PCWError(f'Duplicate YAML material ID: {ident}')
                    catalog[ident] = Material(ident, path.relative_to(root).as_posix(), metadata, body)
                    owners[ident] = root.name
        return LinkCodec(self.layout.root, catalog, owners)

    def status(self):
        state = self._read()
        selected = state.get('context')
        installed = state.get('managed', {})
        return {'context': selected, 'path': str(self._context_path(selected)) if selected else None,
                'present': bool(selected and (self._context_path(selected) / 'Context.md').is_file()),
                'installed_version': installed.get('version'), 'packaged_version': self.bundle['version'],
                'managed_context': installed.get('context'),
                'pending': {key: state['pending'].get(key) for key in ('revision', 'stage')} if state.get('pending') else None,
                'next_action': 'core.base_install' if state.get('pending') else 'core.base_plan' if not installed or installed.get('version') != self.bundle['version'] else None,
                'active_rules_changed': False}

    def select(self, context: str):
        path = self._context_path(context)
        if not (path / 'Context.md').is_file() or not (path / 'knowledge').is_dir():
            raise PCWError('Select an existing Workspace context')
        with self.layout.guard():
            state = self._read()
            state['context'] = context
            self._write(state)
        return self.status()

    def plan(self):
        state = self._read()
        if state.get('pending'):
            pending = state['pending']
            return {**pending['plan'], 'status': 'pending', 'plan_revision': pending['revision'],
                    'next_action': 'core.base_install', 'active_rules_changed': False}
        managed = state.get('managed', {})
        context = managed.get('context', DEFAULT_CONTEXT)
        root = self._context_path(context)
        create = not managed
        conflicts, changes, unchanged = [], [], []
        incoming = {item['key']: item for item in self.bundle['documents']}
        codec = self._link_codec()
        if create and root.exists():
            conflicts.append({'reason': 'unmanaged_context_exists', 'path': str(root),
                              'incoming': self.bundle})
        instructions_path = root / 'INSTRUCTIONS.md'
        instructions_hash = _file_hash(instructions_path)
        if managed and instructions_hash is None:
            conflicts.append({'reason': 'instructions_missing', 'path': str(instructions_path),
                              'incoming': self.bundle['instructions']})
        instructions = read_utf8(instructions_path) if instructions_hash else self.bundle['instructions']
        context_path = root / 'Context.md'
        if managed and context_path.is_file():
            from contextopus.core.documents.instructions import InstructionLibrary
            metadata, _ = _parse(read_utf8(context_path))
            references = metadata.get('instructions', {})
            try:
                store = InstructionLibrary(self.layout)
                required = store.resolve(list(references.values()))
                instructions = store.read(references['workspace'])['content']
                instructions_hash = required['revision']
                conflicts = [item for item in conflicts if item['reason'] != 'instructions_missing']
            except (ValueError, OSError, KeyError) as exc:
                conflicts.append({'reason': 'instructions_missing', 'path': str(context_path), 'error': str(exc)})
        if create:
            from .configuration import WORKSPACE_CREATION_DEFAULTS
            settings = {**WORKSPACE_CREATION_DEFAULTS, **self.bundle.get('settings', {})}
        else:
            from .configuration import WorkspaceSettings as Settings, WORKSPACE_POLICY_DEFAULTS
            settings = Settings(self.layout).workspace_snapshot(context=context,
                fields=list(WORKSPACE_POLICY_DEFAULTS))["values"]
        for key, item in incoming.items():
            validate_description(item, required=True)
            previous = managed.get('documents', {}).get(key)
            if previous:
                path = root / previous['path']
                actual = _file_hash(path)
                try:
                    metadata, body = _parse(read_utf8(path)) if actual else ({}, '')
                    links = codec.decode(metadata.get('links', []), previous['id']) if actual else []
                except PCWError as exc:
                    conflicts.append({'key': key, 'path': str(path), 'reason': 'navigation_conflict',
                                      'error': str(exc), 'current': read_utf8(path), 'incoming': item['content']})
                    continue
                description_changed_by_user = (actual is not None and metadata.get('description') != previous.get('description')
                    and metadata.get('description') != item['description'])
                compatible_metadata_edit = (actual is not None and _revision(body) == previous.get('body_revision')
                    and metadata.get('id') == previous['id'] and metadata.get('type') == previous.get('type', item['type'])
                    and set(previous.get('links', [])) <= set(links) and not description_changed_by_user)
                if description_changed_by_user or actual != previous['file_hash'] and not compatible_metadata_edit:
                    conflicts.append({'key': key, 'path': str(path),
                                      'reason': 'deleted' if actual is None else 'user_modified',
                                      'current': read_utf8(path) if actual else None,
                                      'incoming': item['content']})
                    continue
                required_links = [managed.get('documents', {}).get(key, {}).get('id') for key in item.get('links', [])]
                links_complete = all(ident and ident in links for ident in required_links)
                if body == item['content'] and metadata.get('type') == item['type'] and metadata.get('description') == item['description'] and links_complete:
                    unchanged.append(key)
                    continue
                changes.append({'key': key, 'material_id': previous['id'],
                                'expected_file_hash': actual, 'before': body, 'content': item['content'],
                                'type': item['type'], 'description': item['description'], 'bundle_links': item.get('links', [])})
            else:
                changes.append({'key': key, 'before': None, 'content': item['content'], 'type': item['type'], 'description': item['description'], 'bundle_links': item.get('links', [])})
        # Removed bundle entries remain user-visible records, never automatic deletions.
        retired = sorted(set(managed.get('documents', {})) - set(incoming))
        result = {'context': context, 'version': self.bundle['version'], 'create': create,
                  'instructions_revision': instructions_hash, 'changes': changes,
                  'conflicts': conflicts, 'unchanged': unchanged, 'retained_retired': retired,
                  'approval_required': settings['knowledge_approval_required'],
                  'material_max_tokens': settings['material_max_tokens'],
                  'initial_instructions': self.bundle['instructions'] if create else None,
                  'initial_project': self.bundle['project'] if create else None,
                  'initial_project_description': self.bundle.get('project_description') if create else None,
                  'initial_settings': settings if create else None,
                  'state_revision': _revision(state), 'active_rules_changed': False}
        result['plan_revision'] = _revision(result)
        result['status'] = 'conflicts' if conflicts else 'ready' if changes or create or managed.get('version') != self.bundle['version'] else 'current'
        return result

    def install(self, plan_revision: str, confirmation: str | None = None):
        with self.layout.guard():
            state = self._read()
            pending = state.get('pending')
            managed = state.get('managed', {})
            if not pending and managed.get('plan_revision') == plan_revision:
                runtime = self.runtime_factory(self.layout.root, managed['context'])
                try:
                    cycle = {'cycle_id': managed['cycle_id'],
                             **runtime._cleanup_snapshot(managed['cycle_id'])}
                finally:
                    runtime.close()
                cleanup = ({'cycle_id': cycle['cycle_id'], 'status': 'blocked', 'tmp_preserved': True,
                            'cleanup_revision': cycle['cleanup_revision'], 'temporary_files': cycle['temporary_files']}
                           if cycle['temporary_files'] else None)
                return {**self.status(), 'status': 'installed', 'replayed': True,
                        'plan_revision': plan_revision,
                        **({'cleanup': cleanup, 'next_action': 'finish'} if cleanup else {})}
            preview = self.plan()
            if preview['plan_revision'] != plan_revision:
                raise PCWError('Base context changed since preview; read core.base_plan again')
            if preview['conflicts']:
                return {'status': 'conflicts', 'conflicts': preview['conflicts'], 'active_rules_changed': False}
            if not pending and preview['status'] == 'current':
                return {**self.status(), 'status': 'current'}
            if preview['approval_required'] and (not isinstance(confirmation, str) or not confirmation.strip()):
                return {'status': 'review_requires_approval', 'plan_revision': plan_revision,
                        'review_changes': preview['changes'], 'active_rules_changed': False}
            if not pending:
                pending = {'revision': plan_revision, 'plan': preview, 'stage': 'create',
                           'documents': dict(state.get('managed', {}).get('documents', {}))}
                state['pending'] = pending
                self._write(state)
            runtime = self.runtime_factory(self.layout.root, preview['context'])
            try:
                return self._apply(runtime, state, pending, confirmation)
            finally:
                runtime.close()

    def _apply(self, runtime, state, pending, confirmation):
        preview, revision = pending['plan'], pending['revision']
        if pending['stage'] == 'create':
            if preview['create']:
                created = runtime.workspace_create('base-create-' + revision[:32],
                    preview['initial_instructions'], preview['initial_project'],
                    'Install the exact packaged Contextopus base documentation from the reviewed plan',
                    project_description=preview.get('initial_project_description'),
                    settings=preview.get('initial_settings'))
                if created['status'] != 'created':
                    return {'status': created['status'], 'plan_revision': revision, 'result': created}
            pending['stage'] = 'prepare'
            self._write(state)
        if pending['stage'] == 'prepare':
            opened = runtime.startup('base-install-' + revision[:32])
            pending['cycle_id'] = opened['cycle_id']
            if 'task_revision' not in pending:
                if opened['clarification_count']:
                    return {'status': 'needs_review', 'plan_revision': revision,
                            'reason': 'The base-install cycle has clarifications not covered by the reviewed bundle plan.',
                            'active_rules_changed': False}
                pending['task_revision'] = opened['task_revision']
            self._write(state)
            changes = []
            project = runtime.project_read()
            for item in preview['changes']:
                change = {key: item[key] for key in ('content', 'type', 'description')}
                if item.get('material_id'):
                    current = runtime.read(material_id=item['material_id'])
                    path = runtime.engine.root / current['path']
                    if _file_hash(path) != item['expected_file_hash']:
                        raise PCWError('Managed base document changed; preserve it and review a fresh plan')
                    change.update(material_id=item['material_id'], expected_revision=current['revision'], links=list(current['links']))
                else:
                    change['key'] = item['key']
                    change['links'] = []
                desired = [pending['documents'][key]['id'] if key in pending['documents'] else '@' + key for key in item.get('bundle_links', [])]
                desired.append(project['id'])
                change['links'] = list(dict.fromkeys([*change['links'], *desired]))
                changes.append(change)
            try:
                # A crash after runtime preparation but before our receipt must
                # resume that exact dedicated-cycle proposal, never allocate twice.
                proposals = runtime._records()['cycles'][pending['cycle_id']]['proposals']
                if len(proposals) > 1:
                    raise PCWError('Unexpected additional proposal in base installation cycle')
                if proposals:
                    ident, entry = next(iter(proposals.items()))
                    saved = runtime._load_plan(pending['cycle_id'], ident, entry)
                    if entry['state'] == 'discarded':
                        proposal = runtime.proposal_prepare(pending['cycle_id'], changes,
                            expected_task_revision=pending['task_revision'],
                            proposal_id=ident, summary='Update Contextopus product documentation')
                    else:
                        proposal = {'proposal_id': ident, 'revision': entry['revision'],
                                    'status': entry['state'], 'created_ids': saved['created_ids']}
                else:
                    proposal = runtime.proposal_prepare(pending['cycle_id'], changes,
                        expected_task_revision=pending['task_revision'],
                        no_changes_reason='Packaged documents already match' if not changes else None,
                        summary='Update Contextopus product documentation')
            except PCWError as exc:
                # Size-policy failures stay recoverable without modifying instructions.
                return {'status': 'needs_review', 'error': str(exc), 'plan_revision': revision,
                        'next_action': 'review_base_context_instructions', 'active_rules_changed': False}
            if proposal['status'] in {'needs_split', 'task_changed', 'reprepare_required'}:
                return {**proposal, 'plan_revision': revision, 'active_rules_changed': False}
            pending.update(stage='finish' if proposal['status'] == 'no_changes' else 'apply', proposal_id=proposal['proposal_id'],
                           proposal_revision=proposal['revision'], created_ids=proposal['created_ids'])
            self._write(state)
        if pending['stage'] == 'apply':
            review = runtime.proposal_read(pending['cycle_id'], pending['proposal_id'])
            if review['state'] == 'prepared':
                entry = runtime._records()['cycles'][pending['cycle_id']]['proposals'][pending['proposal_id']]
                saved = runtime._load_plan(pending['cycle_id'], pending['proposal_id'], entry)
                if 'edit_reservations' in saved:
                    try:
                        runtime._edits().validate(pending['cycle_id'], saved['edit_reservations'])
                    except PCWError as exc:
                        # Rebuild from the reviewed bundle and current file hashes on
                        # the next call; never renew ownership of an old proposal.
                        runtime.proposal_discard(pending['cycle_id'], pending['proposal_id'], review['revision'])
                        pending['stage'] = 'prepare'
                        self._write(state)
                        return {'status': 'needs_review', 'error': str(exc), 'plan_revision': revision,
                                'next_action': 'core.base_install', 'active_rules_changed': False}
            if review['approval_required'] and (not isinstance(confirmation, str) or not confirmation.strip()):
                return {'status': 'review_requires_approval', 'plan_revision': revision,
                        'review_changes': review['review_changes'], 'active_rules_changed': False}
            result = runtime.proposal_apply(pending['cycle_id'], pending['proposal_id'],
                                           pending['proposal_revision'], confirmation=confirmation)
            if result['status'] != 'applied':
                return {'status': result['status'], 'plan_revision': revision, 'result': result,
                        'active_rules_changed': False}
            for item in preview['changes']:
                ident = item.get('material_id') or pending['created_ids'][item['key']]
                current = runtime.read(material_id=ident)
                pending['documents'][item['key']] = {'id': ident, 'path': current['path'],
                    'file_hash': _file_hash(runtime.engine.root / current['path']),
                    'body_revision': _revision(item['content']), 'type': item['type'],
                    'description': item['description'],
                    'links': list(current['links'])}
            pending['stage'] = 'finish'
            self._write(state)
        result = runtime.finish(pending['cycle_id'])
        cleanup_pending = (result['status'] == 'blocked' and result.get('tmp_preserved')
                           and 'cleanup_revision' in result)
        if result['status'] != 'closed' and not cleanup_pending:
            return {'status': 'partial', 'plan_revision': revision, 'result': result}
        # Installed records and their managed receipt are durable already.
        # Temporary-file deletion has its own approval and must not make a
        # successful documentation install look incomplete or approve cleanup.
        cleanup = {'cycle_id': pending['cycle_id'], **result} if cleanup_pending else None
        state['managed'] = {'context': preview['context'], 'version': preview['version'],
                            'documents': pending['documents'], 'plan_revision': revision,
                            'cycle_id': pending['cycle_id']}
        state.setdefault('context', preview['context'])
        state.pop('pending', None)
        self._write(state)
        return {**self.status(), 'status': 'installed', 'plan_revision': revision,
                **({'cleanup': cleanup, 'next_action': 'finish'} if cleanup else {})}


def register(app):
    service = BaseContext(app.layout.root, runtime_factory=lambda root, context:
        CycleRuntime(root, context, provider_factory=app.resources.acquire))
    app.base_context = service
    app._operation('workspace.base_status', 'query', service.status)
    app._operation('workspace.base_plan', 'query', service.plan)
    app._operation('workspace.base_install', 'execute', service.install,
                   {'plan_revision': (str, ...), 'confirmation': (str | None, None)})
    app._operation('workspace.base_select', 'execute', service.select, {'context': (str, ...)})
    for name in ('status', 'plan', 'install', 'select'):
        app.alias('core.base_' + name, 'workspace.base_' + name)


def prepare_setup(app):
    """Deliver bundled docs only at explicit init/model-apply boundaries.

    Readiness/status/search callers never invoke this function. Failures in
    documentation or derivative catalog delivery cannot undo a working model.
    """
    if app.active_profile() is None:
        return {'status': 'model_setup_required',
                'base_context': {'status': 'pending', 'next_action': 'core.model_plan'},
                'algorithm_catalog': {'status': 'pending', 'next_action': 'core.model_plan'}}
    try:
        catalog = {'status': 'ready', **app.execute('core.algorithms_refresh')}
    except Exception as exc:
        catalog = {'status': 'pending', 'error': str(exc), 'next_action': 'core.algorithms_refresh'}
    try:
        preview = app.query('core.base_plan')
        if preview['status'] == 'conflicts':
            documentation = {'status': 'conflicts', 'plan': preview, 'next_action': 'core.base_plan'}
        elif preview['approval_required'] and preview['status'] != 'current':
            documentation = {'status': 'review_requires_approval', 'plan': preview,
                             'next_action': 'core.base_install'}
        else:
            documentation = app.execute('core.base_install', {'plan_revision': preview['plan_revision']})
            if documentation['status'] not in {'installed', 'current'}:
                documentation.update(plan=preview, next_action='core.base_install')
    except Exception as exc:
        documentation = {'status': 'pending', 'error': str(exc), 'next_action': 'core.base_plan'}
    ready = documentation['status'] in {'installed', 'current'} and catalog['status'] == 'ready'
    return {'status': 'ready' if ready else 'pending', 'base_context': documentation,
            'algorithm_catalog': catalog}
