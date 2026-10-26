"""Read-only source library; browsing does not depend on derived vectors."""
import re
from contextopus.core.operations import OperationSpec
from .discovery import contexts
from .shared import SharedRuntime
from .records import record_revision

SCHEMA = {'type': 'object', 'properties': {'action': {'enum': ['collections', 'records', 'record']}, 'collection': {'type': 'string'}, 'query': {'type': 'string'}, 'mode': {'enum': ['text', 'semantic']}, 'type': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'id': {'type': 'string'}}, 'required': ['action'], 'additionalProperties': False}


def library(host, action, collection=None, query='', mode='text', type=None, offset=0, limit=50, id=None):
    names = []
    cursor = 0
    while True:
        page = contexts(host.layout, offset=cursor, limit=100)
        names.extend(item['name'] for item in page['contexts'])
        if page['next_offset'] is None:
            break
        cursor = page['next_offset']
    if action == 'collections':
        return {'collections': [{'id': name, 'title': name} for name in names],
                'views': [{'title': 'Contexts', 'type': 'project'},
                          {'title': 'All records', 'type': ''}]}
    if collection and collection not in names:
        raise ValueError('Unknown Workspace collection')
    if not names:
        if action == 'record':
            raise ValueError('Unknown Workspace record')
        return {'records': [], 'total': 0, 'has_more': False, 'types': []}
    selected = collection or (getattr(host.context, 'workspace', None) if getattr(host.context, 'workspace', None) in names else names[0])
    engine = SharedRuntime(host.layout.root, selected, initialize=False, allow_create=False,
                           provider_factory=host.resources.acquire)
    try:
        # A source capture uses the module's parser, validation and link codec,
        # without opening/repairing its RAG index or starting a work cycle.
        snapshot = engine.library_capture()
        def label(ident):
            material = engine.catalog[ident]
            return {'key': ['workspace', engine.owners[ident], ident],
                    **{k: v for k, v in engine._label(ident).items() if k in ('title','description','type','status','tags','path')}}
        if action == 'record':
            material = engine.catalog.get(id)
            if material is None or (collection and engine.owners[id] != collection):
                raise ValueError('Unknown Workspace record')
            links = [label(target) for target in material.links if target in engine.catalog]
            seen = {tuple(item['key']) for item in links}
            # Native body and metadata wikilinks resolve through the same codec.
            codec = engine.link_codec()
            references = re.findall(r'\[\[[^]\n]+\]\]', material.content)
            def metadata_references(value):
                if isinstance(value, str):
                    return re.findall(r'\[\[[^]\n]+\]\]', value)
                if isinstance(value, dict):
                    return [ref for item in value.values() for ref in metadata_references(item)]
                if isinstance(value, list):
                    return [ref for item in value for ref in metadata_references(item)]
                return []
            references += metadata_references(material.metadata)
            from contextopus.core.documents.instructions import InstructionLibrary
            instructions = InstructionLibrary(host.layout)
            bound = []
            for role, reference in material.metadata.get('instructions', {}).items():
                try:
                    instruction_reference = reference[2:-2].split('|', 1)[0] if reference.startswith('[[') else reference
                    instruction = instructions.read(instruction_reference.removeprefix('core/instructions/'))
                    item = {'key': ['core', 'instructions', instruction['id']],
                            'title': instruction['title'], 'description': instruction['description'],
                            'path': 'core/instructions/' + instruction['path']}
                except (ValueError, OSError):
                    item = {'title': reference, 'reference': reference, 'unavailable': True}
                bound.append({**item, 'label': role})
                seen.add(tuple(item.get('key', [reference])))
            for reference in dict.fromkeys(references):
                targets = []
                try:
                    targets = codec.decode([reference], id)
                except ValueError:
                    pass
                if targets:
                    item = label(targets[0])
                else:
                    try:
                        instruction_reference = reference[2:-2].split('|',1)[0]
                        instruction_reference = instruction_reference.removeprefix('core/instructions/')
                        instruction = instructions.read(instruction_reference)
                        item = {'key': ['core','instructions',instruction['id']], 'title': instruction['title'],
                                'description': instruction['description']}
                    except (ValueError, OSError):
                        item = {'title': reference[2:-2].split('|')[-1], 'reference': reference, 'unavailable': True}
                key = tuple(item.get('key', [reference]))
                if key not in seen:
                    links.append(item)
                    seen.add(key)
            return {**label(id), 'content': material.content, 'metadata': material.metadata,
                    'revision': record_revision(material), 'links': links,
                    'link_groups': [{'title': 'Bound instructions', 'links': bound,
                                     'empty_message': 'No instructions bound.'}]
                    if material.path == 'Context.md' or bound else []}
        if action != 'records':
            raise ValueError('Unknown library action')
        records = [label(ident) for ident in sorted(engine.catalog)
                   if (not collection or engine.owners[ident] == collection)]
        types = sorted({item['type'] for item in records})
        records.sort(key=lambda item:(item['title'].casefold(),item['key']))
        if type:
            records = [item for item in records if item['type'] == type]
        if mode == 'semantic' and query.strip():
            with engine.read_guard():
                found = engine.search(query, categories=[collection] if collection else None,
                                      all_categories=not bool(collection), types=[type] if type else None)
            by_key = {tuple(item['key']): item for item in records}
            records = [{**by_key[tuple(item['key'])], 'distance': item['distance']}
                       for item in found['results'] if tuple(item['key']) in by_key]
            return {'records': records[offset:offset+limit], 'total': None,
                    'has_more': offset+limit < len(records), 'types': types,
                    'diagnostics': found['diagnostics'],
                    'warnings': ([f"{found['pending_count']} records await indexing"] if found.get('pending_count') else [])}
        if query:
            needle = query.casefold()
            records = [item for item in records if needle in (item['title']+'\n'+item['description']).casefold()]
        return {'records': records[offset:offset+limit], 'total': len(records),
                'has_more': offset+limit < len(records), 'types': types,
                'warnings': ['Incompatible contexts omitted: '+', '.join(snapshot['incompatible'])] if snapshot['incompatible'] else []}
    finally:
        engine.close()


def register(host):
    host.registry.register(OperationSpec('workspace.library','query',SCHEMA,
        lambda context, **arguments: library(host, **arguments),
        'Browse authoritative Workspace records and links without a work cycle.', guard_root=False))
