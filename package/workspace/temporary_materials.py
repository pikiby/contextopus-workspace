"""Temporary cards use the same YAML records as permanent knowledge."""
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
import re
import os
import posixpath
import ntpath
import uuid
import unicodedata
import yaml

from .runtime import PCWError, Material, _id, _parse, _revision
from .records import validate_description
from .context_identity import context_metadata
from .sources import declarations, migrate_sources
from .operation_types import Offset, PageLimit
from urllib.parse import unquote


def temporary_card(path, root, *, parse_sources=True):
    text = path.read_bytes().decode('utf-8')
    metadata, body = _parse(text)
    ident = _id(metadata.get('id', path.name))  # legacy drafts remain readable
    result = {'id': ident, 'path': Path(os.path.relpath(path, root)).as_posix(),
              'content': body, 'metadata': metadata, 'revision': _revision(text),
              'status': 'temporary'}
    if parse_sources:
        try:
            result['sources'] = declarations(migrate_sources(body))
        except PCWError as exc:
            result.update(sources=[], sources_status='needs_correction', source_error=str(exc),
                          source_diagnostics=[{'path': result['path'], 'error': str(exc)}])
    return result


def card_name(name, content):
    if name is None:
        heading = re.search(r'^#[ \t]+([^\r\n]+)', content, re.M)
        name = heading.group(1) if heading else 'Working note'
    if not isinstance(name, str) or not name.strip():
        raise PCWError('name must be nonempty text')
    name = unicodedata.normalize('NFKC', name)
    name = re.sub(r'[^\w .-]', '-', name, flags=re.UNICODE).strip(' .')[:100]
    if not name or name in {'.', '..'}:
        raise PCWError('Invalid temporary card name')
    name = re.sub(r'\.md$', '', name, flags=re.I).strip(' .')
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]', name.split('.', 1)[0], re.I):
        name = 'Note - ' + name
    return name + '.md'


def card_name_key(name):
    return unicodedata.normalize('NFKC', str(name)).casefold()


def source_references(metadata, *, strict=True):
    """Read declared source references without interpreting their instructions."""
    references = []
    for field in ('sources', 'required_sources'):
        values = metadata.get(field, [])
        if not isinstance(values, list):
            if strict:
                raise PCWError(field + ' must be a list')
            values = [values]
        for value in values:
            if isinstance(value, str):
                references.append(value)
            elif isinstance(value, dict):
                reference = value.get('reference', value.get('url'))
                if not isinstance(reference, str):
                    if strict:
                        raise PCWError('Source declaration requires a text reference')
                    continue
                references.append(reference)
            elif strict:
                raise PCWError('Source declaration requires a reference')
    return references


def is_contextopus_temporary_reference(value, *, known_cycles=()):
    """Recognize managed storage, never infer ownership from a generic tmp segment."""
    value = unquote(str(value)).removeprefix('file://')
    if value.startswith(('https://', 'http://')):
        return False
    parts = PurePosixPath(posixpath.normpath(value)).parts
    if not parts:
        return False
    if parts[0] == '.tmp':
        return True  # reserved current-context temporary root, even before creation
    if len(parts) >= 2 and parts[0] == 'tmp' and parts[1] in known_cycles:
        return True
    for index, part in enumerate(parts):
        if (part == '.pcw' and len(parts) > index + 2
                and not parts[index + 1].startswith('.')
                and parts[index + 2] in {'.tmp', 'tmp'}):
            return True
        if (tuple(parts[index:index + 3]) == ('modules', 'workspace', 'workspace_contexts')
                and len(parts) > index + 4 and parts[index + 4] in {'.tmp', 'tmp'}):
            return True
    return False


def temporary_dependencies(card, cards, *, known_cycles=()):
    """Resolve ordinary note references and source declarations to temporary IDs."""
    body = card.get('content', '')
    try:
        embedded, body = _parse(body)
    except PCWError:
        embedded = {}
    references = list(card.get('metadata', {}).get('links', []))
    references += list(embedded.get('links', []))
    references += source_references(card.get('metadata', {}), strict=False)
    references += source_references(embedded, strict=False)
    references += re.findall(r'\[\[([^]\n]+)\]\]', body)
    references += re.findall(r'\[[^]\n]*\]\(([^)\n]+)\)', body)
    for block in re.findall(r'(?ms)^```ya?ml[^\n]*\n(.*?)^```[ \t]*\r?$', body):
        if not re.search(r'(?m)^(?:sources|required_sources)\s*:', block):
            continue
        try:
            data = yaml.safe_load(block)
            if isinstance(data, dict):
                references += source_references(data, strict=False)
        except yaml.YAMLError:
            # Historical drafts can quote broken source examples. Inventory is
            # not authoring validation; retain identifiable edges conservatively.
            references += [value.strip().strip('"\'') for value in re.findall(
                r'(?m)^\s*(?:-\s*)?(?:reference|url)\s*:\s*(.*?)\s*$', block)]
    found = set()
    for reference in references:
        value = str(reference).strip().removeprefix('[[').removesuffix(']]').split('|', 1)[0]
        value = re.sub(r'''\s+["'].*$''', '', value).strip('<>')
        if value.startswith(('https://', 'http://')):
            continue
        value = unquote(value).split('#', 1)[0].removeprefix('file://')
        if value.startswith(('https://', 'http://')):
            continue
        managed = is_contextopus_temporary_reference(value, known_cycles=known_cycles)
        if managed:
            found.add('@temporary-path')
        for actual_path, target in cards:
            path = target['path']
            if PurePosixPath(value).is_absolute() or PureWindowsPath(value).root:
                if actual_path is not None:
                    actual = str(actual_path)
                    if '\\' in actual or PureWindowsPath(actual).drive:
                        # Compare native Windows spelling only against the
                        # exact inventoried path, never a matching tail.
                        same = ntpath.normcase(ntpath.normpath(value)) == ntpath.normcase(ntpath.normpath(actual))
                    else:
                        same = posixpath.normpath(value) == posixpath.normpath(actual)
                    if same:
                        found.add(target['id'])
                continue
            tail = '.tmp/' + path.split('.tmp/', 1)[1] if '.tmp/' in path else path
            exact = (value == target['id'] or value.removesuffix('.md') == path.removesuffix('.md')
                     or value.removesuffix('.md') == Path(path).stem)
            rooted = managed and (value == tail or value.endswith('/' + tail)
                                  or value.endswith('/' + path)
                                  or path.removesuffix('.md').endswith('/' + value.removesuffix('.md')))
            if exact or rooted:
                found.add(target['id'])
    return found


class TemporaryMaterialsMixin:
    def _temporary_safe(self, path):
        base = self.engine.layout.workspace_base
        if not path.resolve(strict=False).is_relative_to(base.resolve()):
            raise PCWError('Temporary path escapes workspace storage')
        for candidate in (path, *path.parents):
            if candidate == base.parent:
                break
            if candidate.is_symlink():
                raise PCWError('Symlinks are not allowed in memory paths: ' + str(candidate))

    def _temporary_cards(self):
        base = self.engine.layout.workspace_base
        cards = []
        paths = set(base.glob('*/.tmp/*/materials/*.md')) | set(base.glob('*/tmp/*/materials/*.md'))
        for path in sorted(paths):
            self._temporary_safe(path)
            card = temporary_card(path, self.engine.root, parse_sources=False)
            card['workspace'] = path.relative_to(base).parts[0]
            cards.append((path, card))
        return cards

    def _temporary_dependencies(self, card, cards):
        return temporary_dependencies(card, cards, known_cycles=self._records()['cycles'])

    def _permanent_temporary_incoming(self, deleting, cards=None):
        cards = self._temporary_cards() if cards is None else cards
        incoming = []
        for path in sorted(self.engine.layout.workspace_base.glob('*/knowledge/**/*.md')):
            self._temporary_safe(path)
            metadata, body = _parse(path.read_bytes().decode('utf-8'))
            if deleting.intersection(self._temporary_dependencies({'metadata': metadata, 'content': body}, cards)):
                incoming.append({'id': metadata.get('id'),
                                 'path': path.relative_to(self.engine.layout.workspace_base).as_posix()})
        return incoming

    def _assert_permanent_temporary_free(self, snapshots):
        cards = self._temporary_cards()
        for snapshot in snapshots:
            if snapshot is None:
                continue
            if 'text' in snapshot:
                metadata, body = _parse(snapshot['text'])
            else:
                metadata, body = snapshot['metadata'], snapshot['content']
            if self._temporary_dependencies({'metadata': metadata, 'content': body}, cards):
                raise PCWError('Permanent knowledge cannot depend on temporary materials; move the source into knowledge first')

    def _temporary_codec(self):
        catalog, owners = dict(self.engine.catalog), dict(self.engine.owners)
        for path, card in self._temporary_cards():
            catalog[card['id']] = Material(card['id'], path.relative_to(self.engine.layout.workspace_base / card['workspace']).as_posix(), card['metadata'], card['content'])
            owners[card['id']] = card['workspace']
        return self.engine.link_codec(catalog, owners), catalog

    def _temporary_result(self, card):
        codec, catalog = self._temporary_codec()
        links = codec.decode(card['metadata'].get('links', []), card['id'])
        labels = SimpleNamespace(catalog=catalog, materials=getattr(self.engine, "materials", {}),
                                 owners={**self.engine.owners, **{item["id"]: item["workspace"] for _, item in self._temporary_cards()}},
                                 workspace=self.engine.workspace)
        label = lambda ident: self.engine._label.__func__(labels, ident)
        sources = temporary_card(self.engine.root / card['path'], self.engine.root)
        result = {**card, **{key: value for key, value in sources.items() if key in {'sources', 'source_diagnostics', 'sources_status', 'source_error'}},
                  **label(card['id']), 'links': links,
                  'link_targets': [label(ident) for ident in links], 'temporary': True}
        result['metadata'] = {**card['metadata'], 'links': links}
        return result

    def _temporary_name_collision(self, destination, exclude=None):
        if not destination.parent.exists():
            return False
        return any(path != exclude and card_name_key(path.name) == card_name_key(destination.name)
                   for path in destination.parent.iterdir())

    def _temporary_find(self, cycle_id, material_id):
        _id(material_id)
        for path, card in self._temporary_cards():
            if path.parent.parent == self._tmp(cycle_id) and card['id'] == material_id:
                return path, card
        raise PCWError('Temporary material is missing in this cycle; use tmp_list with the same cycle_id')

    def tmp_write(self, cycle_id: str, content: str, material_id: str | None = None,
                  expected_revision: str | None = None, name: str | None = None,
                  description: str | None = None, links: list[str] | None = None,
                  type: str | None = None, status: str | None = None):
        with self._cycle(cycle_id) as (_, cycle):
            self._require_work(cycle)
            if not isinstance(content, str):
                raise PCWError('content must be ordinary Markdown text')
            supplied, body = _parse(content)
            body = migrate_sources(body)
            if material_id is not None:
                path, old = self._temporary_find(cycle_id, material_id)
                if not isinstance(expected_revision, str) or re.fullmatch(r'[0-9a-f]{64}', expected_revision) is None:
                    raise PCWError('Temporary material expected_revision must be exactly 64 lowercase hexadecimal characters; use tmp_read with the same cycle_id and material_id and copy its revision unchanged')
                if expected_revision != old['revision']:
                    raise PCWError('Temporary material changed: revision is stale; use tmp_read and copy its revision unchanged')
                metadata = dict(old['metadata'])
            else:
                ident = uuid.uuid4().hex
                path = self._tmp(cycle_id) / 'materials' / card_name(name, body)
                self.engine._assert_safe(path)
                if self._temporary_name_collision(path):
                    if name is not None:
                        raise PCWError('Temporary card name already exists; choose another name or update its stable ID')
                    path = path.with_name(path.stem + ' - ' + ident[:8] + '.md')
                    self.engine._assert_safe(path)
                metadata = {'id': ident, 'type': 'knowledge', 'links': []}
            old_path = path
            if material_id is not None and name is not None:
                destination = path.with_name(card_name(name, body))
                self.engine._assert_safe(destination)
                if destination != path:
                    if self._temporary_name_collision(destination, exclude=path):
                        raise PCWError('Temporary card name already exists')
                    incoming = [c['id'] for p, c in self._temporary_cards()
                                if p != path and material_id in self._temporary_dependencies(c, self._temporary_cards())]
                    if incoming:
                        raise PCWError('Update incoming temporary references before renaming: ' + ', '.join(incoming))
                    if self._permanent_temporary_incoming({material_id}):
                        raise PCWError('Repair incoming permanent references before renaming temporary material')
                    path = destination
            if 'id' in supplied and supplied['id'] != metadata['id']:
                raise PCWError('Temporary card ID cannot be changed')
            metadata.update(supplied)
            for key, value in [('description', description), ('links', links), ('type', type), ('status', status)]:
                if value is not None:
                    metadata[key] = value
            metadata = context_metadata(metadata, self.engine.workspace)
            validate_description(metadata)
            source_references(metadata)
            if not isinstance(metadata.get('links'), list) or any(not isinstance(x, str) for x in metadata['links']):
                raise PCWError('links must be a list of material IDs')
            catalog, owners = dict(self.engine.catalog), dict(self.engine.owners)
            for temporary_path, card in self._temporary_cards():
                catalog[card['id']] = Material(card['id'], temporary_path.relative_to(self.engine.layout.workspace_base / card['workspace']).as_posix(), card['metadata'], card['content'])
                owners[card['id']] = card['workspace']
            catalog[metadata['id']] = Material(metadata['id'], path.relative_to(self.engine.root).as_posix(), metadata, body)
            owners[metadata['id']] = self.engine.workspace
            codec = self.engine.link_codec(catalog, owners)
            metadata['links'] = codec.decode(metadata['links'], metadata['id'])
            for link in metadata['links']:
                if link not in catalog:
                    raise PCWError('Temporary card link target is missing: ' + link)
            prospective = {'id': metadata['id'], 'path': Path(os.path.relpath(path, self.engine.root)).as_posix(),
                           'metadata': metadata, 'content': body, 'workspace': self.engine.workspace}
            incoming_permanent = self._permanent_temporary_incoming(
                {metadata['id']}, [item for item in self._temporary_cards() if item[1]['id'] != metadata['id']] + [(path, prospective)])
            if incoming_permanent:
                raise PCWError('Permanent knowledge would depend on this temporary card; use a distinct name or repair the permanent reference')
            self.engine._write(path, codec.record_text(metadata, body))
            if old_path != path:
                old_path.unlink()
            return self._temporary_result(temporary_card(path, self.engine.root))

    def tmp_read(self, cycle_id: str, material_id: str):
        with self._cycle(cycle_id):
            return self._temporary_result(self._temporary_find(cycle_id, material_id)[1])

    def tmp_list(self, cycle_id: str, offset: Offset = 0, limit: PageLimit = 50):
        with self._cycle(cycle_id):
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise PCWError('Use a nonnegative offset and limit between 1 and 100')
            cards = [card for path, card in self._temporary_cards() if path.parent.parent == self._tmp(cycle_id)]
            items = [{k: v for k, v in self._temporary_result(card).items() if k != 'content'} for card in cards]
            return {'materials': items[offset:offset + limit], 'total': len(cards), 'status': 'temporary'}

    def tmp_delete(self, cycle_id: str, material_id: str, expected_revision: str,
                   confirmation: str | None = None):
        with self._cycle(cycle_id) as (_, cycle):
            self._require_work(cycle)
            path, card = self._temporary_find(cycle_id, material_id)
            if expected_revision != card['revision']:
                raise PCWError('Temporary material changed: revision is stale')
            incoming = [c['id'] for p, c in self._temporary_cards() if p != path and material_id in self._temporary_dependencies(c, self._temporary_cards())]
            if incoming:
                raise PCWError('Temporary material has incoming links: ' + ', '.join(incoming))
            permanent = self._permanent_temporary_incoming({material_id})
            if permanent:
                raise PCWError('Permanent knowledge depends on temporary material; repair it before deletion: ' + ', '.join(item['path'] for item in permanent))
            if self._policies().get('cleanup_approval_required', True) and (
                    not isinstance(confirmation, str) or not confirmation.strip()):
                return {'id': material_id, 'status': 'blocked', 'revision': card['revision'],
                        'path': card['path'], 'reason': 'Confirm deletion of this exact temporary card revision'}
            path.unlink()
            return {'id': material_id, 'deleted': True}
