"""Scoped legacy filename identities used only by the reviewed migration.

The receipt is the authority for old addresses after a context has migrated.
Normal record identity and the shared catalog remain unchanged.
"""
import json
from collections import Counter
from pathlib import Path
import uuid

from .control import control_store
from .links import LinkCodec
from .runtime import Material, PCWError, _id, _parse, _revision
from .text_io import read_utf8


def mapped_id(workspace, path):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'contextopus:legacy:' + json.dumps(
        (workspace, path), separators=(',', ':'))).hex + '.md'


def validate_mapping(item):
    if set(item) != {'workspace', 'path', 'old_id', 'new_id'}:
        raise PCWError('Invalid retained legacy identity map')
    _id(item['workspace'])
    _id(item['old_id'])
    path = Path(item['path'])
    if path.is_absolute() or '..' in path.parts or path.parts[:1] != ('knowledge',):
        raise PCWError('Invalid retained legacy identity path')
    if item['old_id'] != path.name:
        raise PCWError('Retained legacy identity must identify its original filename')
    if item['new_id'] != mapped_id(item['workspace'], item['path']):
        raise PCWError('Invalid retained deterministic legacy identity')


def pending_sources(runtime, root, version):
    """Read existing immutable legacy claims, never execute foreign proposals."""
    if version >= 2:
        return []
    registry = control_store(runtime.engine.work_root, root.name).root / 'cycles.json'
    runtime.engine._assert_safe(registry)
    if not registry.is_file():
        return []
    result = []
    for cycle_id, cycle in sorted(json.loads(read_utf8(registry)).get('cycles', {}).items()):
        _id(cycle_id)
        for proposal_id, entry in sorted(cycle.get('proposals', {}).items()):
            if entry.get('state') not in {'prepared', 'applying', 'partial', 'index_pending'}:
                continue
            _id(proposal_id)
            path = root / '.tmp' / cycle_id / 'proposals' / (proposal_id + '.json')
            if not path.is_file():
                path = root / 'tmp' / cycle_id / 'proposals' / (proposal_id + '.json')
            runtime.engine._assert_safe(path)
            plan = json.loads(read_utf8(path))
            revision = _revision(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
            review = path.with_suffix('.md')
            runtime.engine._assert_safe(review)
            if revision != entry.get('revision') or read_utf8(review) != runtime._review(plan):
                raise PCWError(f'Pending legacy proposal changed in {root.name}; restore its reviewed revision')
            if plan.get('kind', 'knowledge') != 'knowledge':
                continue
            for ident, change in sorted(plan['entries'].items()):
                after = change.get('after')
                if change.get('before') is not None or not after:
                    continue
                relative = Path(after['path'])
                if relative.is_absolute() or '..' in relative.parts or relative.parts[:1] != ('knowledge',):
                    raise PCWError('Invalid pending legacy material path')
                runtime.engine._assert_safe(root / relative)
                metadata, body = _parse(after['text'])
                result.append(dict(workspace=root.name, path=after['path'], version=version,
                                   old_id=_id(ident), metadata=metadata, body=body, pending=True,
                                   proposal_revision=revision))
    return result


class MigrationIdentity:
    def __init__(self, runtime):
        self.runtime = runtime
        self.workspace = runtime.engine.workspace
        self.rows, self.retained = [], {}
        self.fingerprints = {}
        for root in sorted(runtime.engine.root.parent.iterdir()):
            if root.name.startswith('.') or not root.is_dir():
                continue
            runtime.engine._assert_safe(root)
            control = control_store(runtime.engine.work_root, root.name).root
            marker = control / 'format.json'
            runtime.engine._assert_safe(marker)
            version = json.loads(read_utf8(marker)).get('format_version', 0) if marker.is_file() else 0
            receipt = control / 'migration-v5.json'
            if not receipt.is_file():
                receipt = control / 'migration-v4.json'
            runtime.engine._assert_safe(receipt)
            retained_revision = None
            if receipt.is_file():
                operation = json.loads(read_utf8(receipt))
                retained_revision = _revision(json.dumps(operation.get('identity_map', []), sort_keys=True))
                for item in operation.get('identity_map', []):
                    validate_mapping(item)
                    key = (item['workspace'], item['path'])
                    previous = self.retained.get(key)
                    if previous and previous != item:
                        raise PCWError('Conflicting retained legacy identity maps')
                    self.retained[key] = item
            self.fingerprints[root.name] = {'format': version, 'files': {}, 'retained_identity_revision': retained_revision}
            for path in sorted((root / 'knowledge').rglob('*.md')):
                if any(p.startswith('.') for p in path.relative_to(root / 'knowledge').parts):
                    continue
                runtime.engine._assert_safe(path)
                text = read_utf8(path)
                metadata, body = _parse(text)
                relative = path.relative_to(root).as_posix()
                old = _id(metadata.get('id', path.name) if version >= 2 else path.name)
                self.rows.append(dict(workspace=root.name, path=relative, version=version,
                                      old_id=old, metadata=metadata, body=body))
                self.fingerprints[root.name]['files'][relative] = _revision(text)
        for item in self.fingerprints.values():
            files = item.pop('files')
            item['file_count'] = len(files)
            item['files_revision'] = _revision(json.dumps(files, sort_keys=True))
        # Not-yet-written proposals participate in the same collision map.
        known = {(row['workspace'], row['path']) for row in self.rows}
        for workspace, fingerprint in self.fingerprints.items():
            claims = pending_sources(runtime, runtime.engine.root.parent / workspace, fingerprint['format'])
            fingerprint['pending_identity_revision'] = _revision(json.dumps(claims, sort_keys=True))
            for row in claims:
                key = (row['workspace'], row['path'])
                if key not in known:
                    self.rows.append(row)
                    known.add(key)
        counts = Counter(row['old_id'] for row in self.rows)
        self.mapping = dict(self.retained)
        for row in self.rows:
            key = (row['workspace'], row['path'])
            if row['version'] < 2 and (counts[row['old_id']] > 1 or key in self.retained):
                ident = mapped_id(*key)
                item = dict(workspace=key[0], path=key[1], old_id=row['old_id'], new_id=ident)
                if key in self.retained and self.retained[key] != item:
                    raise PCWError('Retained legacy identity map differs from deterministic address')
                self.mapping[key] = item
                row['id'] = ident
            else:
                row['id'] = row['old_id']
        self.aliases = {}
        self.catalog, self.owners = {}, {}
        for row in self.rows:
            ident, workspace = row['id'], row['workspace']
            if row.get('pending'):
                for alias in {row['old_id'], row['metadata'].get('id', row['old_id'])}:
                    self._alias(workspace, _id(alias), ident)
                continue
            if ident in self.catalog:
                raise PCWError(f'Duplicate YAML material ID {ident}; restore the actual identity before migration')
            self.catalog[ident] = Material(ident, row['path'], row['metadata'], row['body'])
            self.owners[ident] = workspace
            for alias in {row['old_id'], row['metadata'].get('id', row['old_id'])}:
                self._alias(workspace, _id(alias), ident)
        # Completed receipts keep old context-local references authoritative.
        for item in self.retained.values():
            if item['new_id'] in self.catalog:
                self._alias(item['workspace'], item['old_id'], item['new_id'])
        self.preserved_paths = set()
        codec = self.codec()
        for row in self.rows:
            if row.get('pending') or row['workspace'] == self.workspace or row['version'] < 2:
                continue
            for value in row['metadata'].get('links', []):
                targets = codec.decode([value], row['id'], row['workspace'])
                for target in targets:
                    if self.owners[target] == self.workspace:
                        if not value.startswith('[[') and value != target:
                            raise PCWError('Foreign incoming legacy ID link requires a reviewed native path before migration')
                        self.preserved_paths.add(self.catalog[target].path)

    def _alias(self, workspace, old, ident):
        values = self.aliases.setdefault(workspace, {})
        if old in values and values[old] != ident:
            raise PCWError(f'Ambiguous legacy ID {old} inside {workspace}')
        values[old] = ident

    def preview(self):
        return sorted(self.mapping.values(), key=lambda i: (i['workspace'], i['path']))

    def binding(self):
        return {'identity_map': self.preview(), 'identity_catalog': self.fingerprints,
                'identity_map_scope': {'apply_workspace': self.workspace,
                                       'foreign_entries': 'Reservations only; foreign contexts are not changed'}}

    def codec(self, catalog=None, owners=None):
        return MigrationLinkCodec(self, self.catalog if catalog is None else catalog,
                                  self.owners if owners is None else owners)


class MigrationLinkCodec(LinkCodec):
    def __init__(self, identity, catalog, owners):
        self.identity = identity
        super().__init__(identity.runtime.engine.work_root, catalog, owners)

    def decode(self, values, own_id, source_workspace=None, source_path=None):
        if not isinstance(values, list):
            raise PCWError('links must be a list of IDs or quoted wikilinks')
        workspace = source_workspace or self.owners.get(own_id, self.identity.workspace)
        translated = []
        for value in values:
            if isinstance(value, str) and not value.startswith('[['):
                local = self.identity.aliases.get(workspace, {})
                if value in local:
                    value = local[value]
                elif value not in self.catalog:
                    matches = {a[value] for a in self.identity.aliases.values() if value in a}
                    if len(matches) > 1:
                        raise PCWError(f'Ambiguous cross-context legacy link {value}; use its exact native path')
                    if matches:
                        value = matches.pop()
            translated.append(value)
        result = super().decode(translated, own_id, workspace, source_path)
        versions = {row['workspace']: row['version'] for row in self.identity.rows}
        for target in result:
            if target not in self.catalog:
                raise PCWError(f'Unresolved migration link {target}; repair its exact target')
            owner = self.owners[target]
            if owner != self.identity.workspace and versions.get(owner, 4) < 2:
                raise PCWError(f'Foreign legacy target in {owner} requires migration first; cross-context legacy groups are not migrated implicitly')
        return result


def catalog_fingerprints(runtime):
    """Read-only dependency binding, including new/deleted foreign records."""
    result = {}
    for root in sorted(runtime.engine.root.parent.iterdir()):
        if root.name.startswith('.') or not root.is_dir():
            continue
        runtime.engine._assert_safe(root)
        marker = control_store(runtime.engine.work_root, root.name).root / 'format.json'
        runtime.engine._assert_safe(marker)
        version = json.loads(read_utf8(marker)).get('format_version', 0) if marker.is_file() else 0
        receipt = marker.parent / 'migration-v5.json'
        if not receipt.is_file():
            receipt = marker.parent / 'migration-v4.json'
        runtime.engine._assert_safe(receipt)
        retained_revision = (_revision(json.dumps(json.loads(read_utf8(receipt)).get('identity_map', []), sort_keys=True))
                             if receipt.is_file() else None)
        files = {}
        for path in sorted((root / 'knowledge').rglob('*.md')):
            if any(p.startswith('.') for p in path.relative_to(root / 'knowledge').parts):
                continue
            runtime.engine._assert_safe(path)
            files[path.relative_to(root).as_posix()] = _revision(read_utf8(path))
        result[root.name] = {'format': version, 'file_count': len(files),
                             'files_revision': _revision(json.dumps(files, sort_keys=True)),
                             'retained_identity_revision': retained_revision,
                             'pending_identity_revision': _revision(json.dumps(pending_sources(runtime, root, version), sort_keys=True))}
    return result


def legacy_reference(engine, ident, *, local_task=False):
    """Resolve only verified, local receipt aliases; never shadow a real ID."""
    if ident is None:
        return None
    _id(ident)
    receipt = engine.control.root / 'migration-v5.json'
    if not receipt.is_file():
        receipt = engine.control.root / 'migration-v4.json'
    engine._assert_safe(receipt)
    if not receipt.is_file():
        return ident
    operation = json.loads(read_utf8(receipt))
    if operation.get('status') != 'complete':
        return ident
    matches = set()
    for item in operation.get('identity_map', []):
        validate_mapping(item)
        if item['workspace'] == engine.workspace and item['old_id'] == ident:
            matches.add(item['new_id'])
    if not matches:
        return ident
    if len(matches) != 1:
        raise PCWError(f'Ambiguous retained legacy reference {ident}; use the reviewed final ID')
    target = matches.pop()
    catalog = engine.catalog
    if ident in catalog and ident != target and not local_task:
        raise PCWError(f'Legacy reference {ident} conflicts with a current YAML ID; use final ID {target}')
    if target not in catalog or engine.owners[target] != engine.workspace:
        raise PCWError(f'Retained legacy target {target} is missing; inspect migration receipt')
    return target
