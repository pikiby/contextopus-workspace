"""Bounded, reproducible queries of recorded analytics evidence."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json

from .runtime import PCWError

_FILTERS = ('workspaces', 'cycle_id', 'task_id', 'tool', 'since', 'until',
            'import_id', 'instruction_revision', 'runtime_version', 'procedures_revision')


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value):
    try:
        return _timestamp(value).isoformat()
    except (ValueError, TypeError, OverflowError) as exc:
        raise PCWError('Invalid analytics date: ' + str(value)) from exc


def _sql_time(value):
    try:
        return _timestamp(value).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _decode(cursor):
    try:
        if not isinstance(cursor, str) or len(cursor) > 16384:
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if value['v'] != 1 or not isinstance(value['scope'], dict):
            raise ValueError()
        if any(type(value[k]) is not int or value[k] < 0 for k in ('anchor', 'last')):
            raise ValueError()
        if value['last'] > value['anchor']:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PCWError('Invalid analytics cursor') from exc


def query_events(store, *, workspaces=None, cycle_id=None, task_id=None,
                 tool=None, since=None, until=None, import_id=None,
                 instruction_revision=None, runtime_version=None, procedures_revision=None,
                 limit=100, cursor=None, offset=0):
    """Return a rowid-ordered page; continuation freezes scope and insert boundary.

    Dates without an offset are interpreted as UTC. Bounds are inclusive at
    since and exclusive at until. Explicit cycle/task/import may span history.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise PCWError('Analytics limit must be between 1 and 1000')
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise PCWError('Analytics offset must be nonnegative')
    supplied = dict(workspaces=workspaces, cycle_id=cycle_id, task_id=task_id,
                    tool=tool, since=since, until=until, import_id=import_id,
                    instruction_revision=instruction_revision, runtime_version=runtime_version,
                    procedures_revision=procedures_revision)
    if workspaces is not None:
        if not isinstance(workspaces, (list, tuple)) or not workspaces or any(not isinstance(w, str) or not w for w in workspaces):
            raise PCWError('workspaces must be a nonempty list of names')
        supplied['workspaces'] = sorted(set(workspaces))
    for name in ('since', 'until'):
        if supplied[name] is not None:
            supplied[name] = _iso(supplied[name])
    token = _decode(cursor) if cursor else None
    if token:
        if offset:
            raise PCWError('Cannot combine analytics cursor and offset')
        scope = token['scope']
        if set(scope) != set(_FILTERS):
            raise PCWError('Invalid analytics cursor scope')
        if any(value is not None and value != scope[key] for key, value in supplied.items()):
            raise PCWError('Analytics cursor filters cannot change')
    else:
        scope = supplied
        if since is None and until is None and not any((cycle_id, task_id, import_id)):
            # Include events stamped on this clock tick while retaining an
            # exclusive bound and the frozen rowid insertion boundary.
            end = datetime.now(timezone.utc)
            scope.update(since=(end - timedelta(days=7)).isoformat(),
                         until=(end + timedelta(microseconds=1)).isoformat())
        elif scope['since'] is not None and scope['until'] is None:
            scope['until'] = (datetime.now(timezone.utc) + timedelta(microseconds=1)).isoformat()
        elif scope['since'] is None and scope['until'] is not None and not any((cycle_id, task_id, import_id)):
            scope['since'] = (_timestamp(scope['until']) - timedelta(days=7)).isoformat()
    _validate_scope(scope)
    if not any(scope[key] for key in ('since', 'until', 'cycle_id', 'task_id', 'import_id')):
        raise PCWError('Analytics scope requires a period, cycle, task, or import')
    for key in ('since', 'until'):
        if scope[key] is not None:
            scope[key] = _iso(scope[key])
    if scope['since'] and scope['until'] and _timestamp(scope['since']) >= _timestamp(scope['until']):
        raise PCWError('Analytics since must precede until')
    with store.connect() as db:
        db.create_function('analytics_epoch', 1, _sql_time, deterministic=True)
        # Native expression indexes remain writable by all EventStore connections.
        # Coarse SQLite bounds select candidates; Python preserves microseconds.
        db.execute('CREATE INDEX IF NOT EXISTS events_analytics_date ON events(julianday(started_at))')
        anchor = token['anchor'] if token else db.execute('SELECT coalesce(max(rowid),0) FROM events').fetchone()[0]
        clauses, values = ['rowid <= ?'], [anchor]
        for key, column in (('cycle_id','cycle_id'), ('task_id','task_id'), ('tool','tool'), ('import_id','import_id'), ('instruction_revision','instructions_revision'), ('runtime_version','runtime_version'), ('procedures_revision','procedures_revision')):
            if scope[key] is not None:
                clauses.append(column + ' = ?')
                values.append(scope[key])
        if scope['workspaces']:
            clauses.append('workspace IN (' + ','.join('?' for _ in scope['workspaces']) + ')')
            values.extend(scope['workspaces'])
        base = ' AND '.join(clauses)
        unknown = db.execute('SELECT count(*) FROM events WHERE ' + base + ' AND julianday(started_at) IS NULL', values).fetchone()[0]
        for key, op in (('since','>='), ('until','<')):
            if scope[key]:
                clauses.append('julianday(started_at) ' + op + ' julianday(?)')
                margin = -timedelta(seconds=1) if key == 'since' else timedelta(seconds=1)
                values.append((_timestamp(scope[key]) + margin).isoformat())
                clauses.append('analytics_epoch(started_at) ' + op + ' ?')
                values.append(_timestamp(scope[key]).timestamp())
        where = ' AND '.join(clauses)
        table = 'events INDEXED BY events_analytics_date' if scope['since'] or scope['until'] else 'events'
        totals = dict(db.execute(
            "SELECT count(*) AS calls, "
            "coalesce(sum(state='error' OR error IS NOT NULL),0) AS errors, "
            "coalesce(sum(state='started'),0) AS unknown_outcomes, "
            "sum(duration_ms) AS duration_ms, sum(result_bytes) AS result_bytes "
            "FROM " + table + " WHERE " + where, values).fetchone())
        imported = db.execute('SELECT count(*) FROM ' + table + ' WHERE ' + where + " AND (import_id IS NOT NULL)", values).fetchone()[0]
        total = totals['calls']
        rows = db.execute('SELECT rowid AS analytics_rowid, * FROM ' + table + ' WHERE ' + where + ' AND rowid > ? ORDER BY rowid LIMIT ? OFFSET ?', values + [token['last'] if token else 0, limit + 1, offset]).fetchall()
        more = len(rows) > limit
        events = [dict(row) for row in rows[:limit]]
        # Blob columns are references. Body retrieval belongs to analytics_read.
        next_cursor = None
        if more:
            next_cursor = base64.urlsafe_b64encode(json.dumps(dict(v=1, scope=scope, anchor=anchor, last=events[-1]['analytics_rowid']), sort_keys=True).encode()).decode()
        return dict(events=events, scope=scope, next_cursor=next_cursor,
                    total_events=total, totals=totals, anchor_rowid=anchor,
                    coverage=dict(imported_events=imported, unknown_timestamp_events=unknown,
                                  unknown_timestamp_count_kind='sqlite_unparseable_lower_bound',
                                  timestamp_coverage_note='ISO date/time records only; malformed legacy dates normalized by SQLite may be uncounted in unknown coverage.',
                                  unknown_timestamps_excluded=bool(scope['since'] or scope['until'])))


def analytics_report(store, *, max_events=1000, cursor=None, max_blob_bytes=4_000_000, **scope):
    """Summarize one bounded evidence page, explicitly labelling partial reports."""
    if type(max_blob_bytes) is not int or not 0 <= max_blob_bytes <= 16_000_000:
        raise PCWError('Analytics blob budget must be between 0 and 16000000 bytes')
    page = query_events(store, limit=max_events, cursor=cursor, **scope)
    groups = {key: {} for key in ('operation', 'outcome', 'workspace', 'instruction_revision', 'runtime_version', 'procedures_revision')}
    usage = dict(search_calls=0, returned_matches=0, read_calls=0, read_materials=0,
                 declared_from_id_link_reads=0, write_calls=0, errors=0)
    material_ids = set()
    metadata, blob_coverage = _report_metadata(store, page['events'], max_blob_bytes)
    for event in page['events']:
        for group, value in (('operation', event['tool']), ('outcome',event['state']), ('workspace',event['workspace']), ('instruction_revision',event.get('instructions_revision')), ('runtime_version',event.get('runtime_version')), ('procedures_revision',event.get('procedures_revision'))):
            label = value if value is not None else 'unknown'
            groups[group][label] = groups[group].get(label, 0) + 1
        if event.get('error') is not None or event['state'] == 'error': usage['errors'] += 1
        tool = event['tool'].rsplit('.', 1)[-1]
        params = metadata.get((event['analytics_rowid'], 'params')) or {}
        result = metadata.get((event['analytics_rowid'], 'result')) or {}
        if not isinstance(params, dict): params = {}
        if not isinstance(result, dict): result = {}
        if 'search' in tool:
            usage['search_calls'] += 1
            matches = result.get('matches', result.get('results', []))
            if isinstance(matches, list): usage['returned_matches'] += len(matches)
        if tool in ('read', 'read_many', 'pcw_read', 'pcw_read_many'):
            usage['read_calls'] += 1
            if event['state'] != 'completed' or event.get('error') is not None:
                continue
            if params.get('from_id'): usage['declared_from_id_link_reads'] += 1
            materials = result.get('materials', result.get('items', [result]))
            if isinstance(materials, list):
                for material in materials:
                    if isinstance(material, dict):
                        ident = material.get('id') or material.get('material_id')
                        if ident:
                            usage['read_materials'] += 1
                            material_ids.add((material.get('workspace') or result.get('workspace') or event['workspace'], str(ident)))
        if any(part in tool for part in ('write', 'update', 'delete', 'create', 'append', 'proposal_apply', 'consolidate')):
            usage['write_calls'] += 1
    usage['unique_read_materials'] = len(material_ids)
    return dict(scope=page['scope'], totals=page['totals'], selected_scope_events=page['total_events'],
                analyzed_events=len(page['events']), partial=bool(cursor or page['next_cursor'] or scope.get('offset')),
                statistics_scope='returned_page', groups=groups, context_usage=usage,
                instruction_basis=_instruction_eras(page['events']),
                instruction_basis_policy='Recorded event revisions only; current rules are never applied retroactively.',
                next_cursor=page['next_cursor'], coverage={**page['coverage'], **blob_coverage},
                evidence_limits=['Recorded calls and declared links only; no inference of actual model use, session identity, or token savings.'])


def _validate_scope(scope):
    if set(scope) != set(_FILTERS):
        raise PCWError('Invalid analytics scope keys')
    for key, value in scope.items():
        if value is None:
            continue
        if key == 'workspaces':
            if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 100 or any(not isinstance(w, str) or not 1 <= len(w) <= 1024 for w in value):
                raise PCWError('Invalid analytics workspace scope')
        elif not isinstance(value, str) or not 1 <= len(value) <= 1024:
            raise PCWError('Invalid analytics scope value: ' + key)


def _report_metadata(store, events, budget):
    values, used, skipped = {}, 0, 0
    with store.connect() as db:
        for event in events:
            for field in ('params', 'result'):
                ref = event.get(field)
                if not ref:
                    continue
                # Check bytes before fetching old full-content blobs into Python.
                size = db.execute('SELECT length(CAST(text AS BLOB)) FROM blobs WHERE id=?', (ref,)).fetchone()
                if size is None:
                    skipped += 1
                    continue
                if size[0] > budget - used:
                    skipped += 1
                    continue
                blob = db.execute('SELECT text FROM blobs WHERE id=?', (ref,)).fetchone()
                used += size[0]
                try:
                    values[event['analytics_rowid'], field] = json.loads(blob[0])
                except (TypeError, ValueError):
                    skipped += 1
    return values, dict(metadata_blob_bytes_read=used, metadata_blobs_skipped=skipped,
                        usage_metadata_complete=skipped == 0)


def validate_assessment_scope(store, event_ids, scope=None, instruction_basis=None):
    """Validate cited evidence against its declared scope and recorded rule era.

    An explicit event_ids scope permits reviewing exact evidence without a time
    window. It never widens into a history query. Rule claims must match every
    cited event, or explicitly state unknown instead of using present rules.
    """
    if not isinstance(event_ids, list) or not 1 <= len(event_ids) <= 1000 or any(not isinstance(i, str) or not i or len(i) > 1024 for i in event_ids) or len(set(event_ids)) != len(event_ids):
        raise PCWError('Assessment requires 1 to 1000 distinct event IDs')
    if scope is None:
        scope = {'event_ids': list(event_ids)}
    if not isinstance(scope, dict) or not scope:
        raise PCWError('Assessment requires an explicit scope')
    extra = set(scope) - set(_FILTERS) - {'event_ids'}
    if extra:
        raise PCWError('Unknown assessment scope fields')
    explicit_ids = scope.get('event_ids')
    if explicit_ids is not None:
        if not isinstance(explicit_ids, list) or len(explicit_ids) > 1000 or any(not isinstance(i, str) for i in explicit_ids) or set(explicit_ids) != set(event_ids):
            raise PCWError('Assessment event ID scope must exactly match evidence')
    filters = {key: scope.get(key) for key in _FILTERS}
    _validate_scope(filters)
    if explicit_ids is None and not any(filters[key] for key in ('since', 'until', 'cycle_id', 'task_id', 'import_id')):
        raise PCWError('Assessment scope requires a period, cycle, task, import, or exact event IDs')
    for key in ('since', 'until'):
        if filters[key] is not None:
            filters[key] = _iso(filters[key])
    if filters['since'] and filters['until'] and _timestamp(filters['since']) >= _timestamp(filters['until']):
        raise PCWError('Assessment since must precede until')
    with store.connect() as db:
        rows = db.execute('SELECT * FROM events WHERE id IN (' + ','.join('?' for _ in event_ids) + ')', event_ids).fetchall()
    if len(rows) != len(event_ids):
        raise PCWError('Assessment cites missing event evidence')
    for row in rows:
        event = dict(row)
        if filters['workspaces'] is not None and event['workspace'] not in filters['workspaces']:
            raise PCWError('Assessment evidence is outside the declared workspace scope')
        for key, column in (('cycle_id','cycle_id'), ('task_id','task_id'), ('tool','tool'), ('import_id','import_id'), ('instruction_revision','instructions_revision'), ('procedures_revision','procedures_revision'), ('runtime_version','runtime_version')):
            if filters[key] is not None and event.get(column) != filters[key]:
                raise PCWError('Assessment evidence is outside the declared ' + key + ' scope')
        if filters['since'] or filters['until']:
            stamp = _sql_time(event.get('started_at'))
            if stamp is None or (filters['since'] and stamp < _timestamp(filters['since']).timestamp()) or (filters['until'] and stamp >= _timestamp(filters['until']).timestamp()):
                raise PCWError('Assessment evidence is outside the declared period')
    recorded = {row['id']: dict(row) for row in rows}
    for event in recorded.values():
        ref = event.get('instructions_ref')
        if isinstance(ref, str):
            try:
                ref = json.loads(ref)
            except (ValueError, TypeError):
                ref = None
        event['instructions_ref'] = ref if isinstance(ref, dict) else None
    if instruction_basis is None:
        by_revision = {}
        for event in recorded.values():
            revision = event.get('instructions_revision')
            ref = event.get('instructions_ref')
            key = (revision, json.dumps(ref, sort_keys=True))
            if key not in by_revision:
                by_revision[key] = dict(event_ids=[], instructions_revision=revision,
                                        instructions_ref=ref,
                                        status='recorded' if revision is not None else 'unknown',
                                        rule_text_available=_rule_text_available(ref))
            by_revision[key]['event_ids'].append(event['id'])
        instruction_basis = list(by_revision.values())
    if not isinstance(instruction_basis, list) or not 1 <= len(instruction_basis) <= len(event_ids):
        raise PCWError('Assessment instruction basis must be a list of recorded or unknown eras')
    covered = set()
    for basis in instruction_basis:
        if not isinstance(basis, dict) or set(basis) - {'event_ids', 'instructions_revision', 'instructions_ref', 'status', 'rule_text_available'}:
            raise PCWError('Invalid assessment instruction basis entry')
        ids = basis.get('event_ids')
        status = basis.get('status')
        revision = basis.get('instructions_revision')
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or set(ids) - set(event_ids) or covered.intersection(ids):
            raise PCWError('Assessment instruction basis must cover distinct cited events')
        if status not in ('recorded', 'unknown') or (status == 'unknown' and revision is not None) or (status == 'recorded' and (not isinstance(revision, str) or not revision)):
            raise PCWError('Assessment instruction basis requires a recorded revision or explicit unknown')
        for ident in ids:
            event = recorded[ident]
            if status == 'recorded' and revision != event.get('instructions_revision'):
                raise PCWError('Assessment instruction revision does not match recorded evidence')
            if 'rule_text_available' in basis and (type(basis['rule_text_available']) is not bool or basis['rule_text_available'] != _rule_text_available(event.get('instructions_ref'))):
                raise PCWError('Assessment rule text availability does not match recorded evidence')
            if 'instructions_ref' in basis and basis['instructions_ref'] != event.get('instructions_ref'):
                raise PCWError('Assessment instruction reference does not match recorded evidence')
        covered.update(ids)
    if covered != set(event_ids):
        raise PCWError('Assessment instruction basis must cover every cited event')
    normalized = {key: value for key, value in filters.items() if value is not None}
    if explicit_ids is not None:
        normalized['event_ids'] = list(event_ids)
    return dict(scope=normalized, instruction_basis=instruction_basis, evidence_ids=list(event_ids))


def _rule_text_available(ref):
    """Availability is recorded provenance, not a claim of having read the text."""
    return isinstance(ref, dict) and ref.get('status') == 'versioned'


def _instruction_eras(events):
    eras = {}
    for event in events:
        ref = event.get('instructions_ref')
        if isinstance(ref, str):
            try:
                ref = json.loads(ref)
            except (ValueError, TypeError):
                ref = None
        if not isinstance(ref, dict):
            ref = None
        revision = event.get('instructions_revision')
        key = (revision, json.dumps(ref, sort_keys=True))
        if key not in eras:
            eras[key] = dict(event_ids=[], instructions_revision=revision,
                             instructions_ref=ref, status='recorded' if revision is not None else 'unknown',
                             rule_text_available=_rule_text_available(ref))
        eras[key]['event_ids'].append(event['id'])
    return list(eras.values())
