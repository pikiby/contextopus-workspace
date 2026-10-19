"""Scope and continuation invariants independent of the runtime provider."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from workspace.analytics_queries import analytics_report, query_events
from workspace.runtime import PCWError


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE events (id TEXT, workspace TEXT, tool TEXT, cycle_id TEXT,
            task_id TEXT, started_at TEXT, state TEXT, params TEXT, result TEXT,
            error TEXT, instructions_revision TEXT, instructions_ref TEXT,
            runtime_version TEXT, operation_id TEXT, import_id TEXT,
            procedures_revision TEXT, duration_ms REAL, result_bytes INTEGER);
          CREATE TABLE blobs (id TEXT, text TEXT);
        ''')

    @contextmanager
    def connect(self):
        yield self.db

    def add(self, when, **kwargs):
        values = dict(id=str(self.db.execute('SELECT count(*) FROM events').fetchone()[0]),
                      workspace='p', tool='read', state='completed', started_at=when)
        values.update(kwargs)
        self.db.execute('INSERT INTO events (' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', list(values.values()))


def test_utc_bounds_and_invalid_dates():
    store = Store()
    store.add('2026-10-15T01:00:00+02:00')
    store.add('2026-10-15T00:00:00Z')
    store.add('2026-10-16T00:00:00Z')
    page = query_events(store, since='2026-10-15', until='2026-10-16')
    assert [e['id'] for e in page['events']] == ['1']
    assert page['scope']['since'] == '2026-10-15T00:00:00+00:00'
    for bounds in ({'since':'nonsense'}, {'since':'2026-10-16', 'until':'2026-10-15'}):
        with pytest.raises(PCWError): query_events(store, **bounds)


def test_default_recent_window_and_explicit_cycle():
    store = Store()
    store.add('2000-01-01', cycle_id='old')
    store.add(datetime.now(timezone.utc).isoformat())
    assert query_events(store)['total_events'] == 1
    assert query_events(store, cycle_id='old')['total_events'] == 1
    assert query_events(store)['scope']['until'] is not None


def test_cursor_freezes_period_and_insert_boundary():
    store = Store()
    for _ in range(3): store.add('2026-10-15')
    first = query_events(store, since='2026-10-15', until='2026-10-16', limit=1)
    store.add('2026-10-15')
    second = query_events(store, cursor=first['next_cursor'], limit=2)
    assert [e['id'] for e in second['events']] == ['1', '2']
    assert second['total_events'] == 3
    assert second['scope'] == first['scope']
    assert second['next_cursor'] is None
    with pytest.raises(PCWError): query_events(store, cursor=first['next_cursor'], tool='write')


def test_unknown_timestamp_coverage_and_instruction_eras():
    store = Store()
    store.add('2026-10-15', instructions_revision='a', runtime_version='v1')
    store.add('2026-10-15', instructions_revision='b', runtime_version='v2')
    store.add('2026-10-15')
    store.add('unknown')
    result = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert result['groups']['instruction_revision'] == {'a':1, 'b':1, 'unknown':1}
    assert result['coverage']['unknown_timestamp_events'] == 1
    assert result['coverage']['unknown_timestamps_excluded']
    assert not result['partial']
    assert query_events(store, since='2026-10-15', until='2026-10-16', instruction_revision='a')['total_events'] == 1


def test_report_caps_and_labels_page_counts():
    store = Store()
    for _ in range(1002): store.add('2026-10-15')
    report = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert report['selected_scope_events'] == 1002
    assert report['analyzed_events'] == 1000
    assert report['partial']
    assert report['statistics_scope'] == 'returned_page'
    assert report['groups']['operation'] == {'read':1000}
    tail = analytics_report(store, cursor=report['next_cursor'])
    assert tail['partial'] and tail['analyzed_events'] == 2


def test_observed_usage_counts():
    store = Store()
    for ident, value in [('params', {'from_id':'a'}), ('read', {'material_id':'b'}), ('search', {'results':[{'id':'b'}, {'id':'c'}]})]:
        store.db.execute('INSERT INTO blobs VALUES (?,?)', (ident, json.dumps(value)))
    store.add('2026-10-15', params='params', result='read')
    store.add('2026-10-15', result='read')
    store.add('2026-10-15', tool='search', result='search')
    report = analytics_report(store, since='2026-10-15', until='2026-10-16')
    usage = report['context_usage']
    assert usage['read_materials'] == 2 and usage['unique_read_materials'] == 1
    assert usage['returned_matches'] == 2 and usage['declared_from_id_link_reads'] == 1


def test_metadata_only_and_report_blob_budget():
    store = Store()
    store.db.execute('INSERT INTO blobs VALUES (?,?)', ('large', json.dumps({'content':'x' * 5_000_000})))
    store.add('2026-10-15', result='large')
    page = query_events(store, since='2026-10-15', until='2026-10-16')
    assert page['events'][0]['result'] == 'large'
    assert len(json.dumps(page)) < 5000
    report = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert report['coverage']['metadata_blobs_skipped'] == 1
    assert not report['coverage']['usage_metadata_complete']
    assert report['coverage']['metadata_blob_bytes_read'] == 0


def test_scope_totals_include_errors_beyond_page_and_failed_reads():
    store = Store()
    store.db.execute('INSERT INTO blobs VALUES (?,?)', ('body', json.dumps({'material_id':'b'})))
    store.add('2026-10-15', state='completed', duration_ms=2, result_bytes=10)
    store.add('2026-10-15', state='error', result='body', error='missing', duration_ms=3)
    store.add('2026-10-15', state='started')
    report = analytics_report(store, since='2026-10-15', until='2026-10-16', max_events=1)
    assert report['totals'] == {'calls':3, 'errors':1, 'unknown_outcomes':1, 'duration_ms':5, 'result_bytes':10}
    assert report['context_usage']['errors'] == 0
    full = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert full['context_usage']['errors'] == 1
    assert full['context_usage']['read_materials'] == 0


def test_cursor_rejects_scope_types_and_boolean_anchor():
    import base64
    store = Store()
    for _ in range(2): store.add('2026-10-15')
    page = query_events(store, since='2026-10-15', until='2026-10-16', limit=1)
    token = json.loads(base64.urlsafe_b64decode(page['next_cursor']))
    for key, value in [('anchor', True), ('scope', {**token['scope'], 'tool':['read']})]:
        bad = dict(token)
        bad[key] = value
        cursor = base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
        with pytest.raises(PCWError): query_events(store, cursor=cursor)


def test_period_uses_date_index_before_python_normalization(monkeypatch):
    from workspace import analytics_queries
    store = Store()
    for _ in range(200): store.add('2000-01-01')
    store.add('2026-10-15T01:02:03.123456+00:00')
    original = analytics_queries._sql_time
    normalized = []
    def normalize(value):
        normalized.append(value)
        return original(value)
    monkeypatch.setattr(analytics_queries, '_sql_time', normalize)
    page = query_events(store, since='2026-10-15T01:02:03.123455Z', until='2026-10-15T01:02:03.123457Z')
    assert page['total_events'] == 1
    assert not any(value.startswith('2000') for value in normalized)
    assert len(normalized) <= 8


def test_operation_group_uses_tool_and_cross_context_owners():
    store = Store()
    store.db.execute('INSERT INTO blobs VALUES (?,?)', ('body', json.dumps({'materials':[
        {'material_id':'same', 'workspace':'owner-a'},
        {'material_id':'same', 'workspace':'owner-b'}]})))
    store.add('2026-10-15', tool='read_many', operation_id='unique-proposal-id', result='body')
    report = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert report['groups']['operation'] == {'read_many':1}
    assert report['context_usage']['unique_read_materials'] == 2


def test_imported_outcome_is_separate_coverage():
    store = Store()
    store.add('2026-10-15', state='imported', import_id='import-1')
    report = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert report['totals']['unknown_outcomes'] == 0
    assert report['coverage']['imported_events'] == 1


def test_assessment_rejects_wrong_era_and_outside_scope():
    from workspace.analytics_queries import validate_assessment_scope
    store = Store()
    store.add('2026-10-15', instructions_revision='old', cycle_id='cycle-1', task_id='task-1', import_id='import-1')
    bounds = dict(workspaces=['p'], since='2026-10-15', until='2026-10-16')
    with pytest.raises(PCWError, match='revision does not match'):
        validate_assessment_scope(store, ['0'], bounds, [{'event_ids':['0'], 'instructions_revision':'today', 'status':'recorded'}])
    for changed in ({'workspaces':['other']}, {'since':'2026-10-16', 'until':'2026-10-17'}, {'cycle_id':'cycle-2'}, {'task_id':'task-2'}, {'import_id':'import-2'}):
        with pytest.raises(PCWError, match='outside'):
            validate_assessment_scope(store, ['0'], {**bounds, **changed}, [{'event_ids':['0'], 'instructions_revision':None, 'status':'unknown'}])
    valid = validate_assessment_scope(store, ['0'], bounds, [{'event_ids':['0'], 'instructions_revision':'old', 'status':'recorded'}])
    assert valid['scope']['since'] == '2026-10-15T00:00:00+00:00'
    assert valid['evidence_ids'] == ['0']


def test_assessment_explicit_unknown_and_exact_ids():
    from workspace.analytics_queries import validate_assessment_scope
    store = Store()
    store.add('2000-01-01', instructions_revision=None)
    store.add('2001-01-01', instructions_revision='known')
    basis = [{'event_ids':['0'], 'instructions_revision':None, 'status':'unknown'},
             {'event_ids':['1'], 'instructions_revision':'known', 'status':'recorded'}]
    result = validate_assessment_scope(store, ['0','1'], {'event_ids':['0','1']}, basis)
    assert result['instruction_basis'] == basis
    explicit_unknown = [{'event_ids':['0'], 'instructions_revision':None, 'status':'unknown'}]
    assert validate_assessment_scope(store, ['0'], {'event_ids':['0']}, explicit_unknown)['instruction_basis'] == explicit_unknown
    default = validate_assessment_scope(store, ['0','1'])
    assert default['scope'] == {'event_ids':['0','1']}
    assert [b['status'] for b in default['instruction_basis']] == ['unknown', 'recorded']
    with pytest.raises(PCWError): validate_assessment_scope(store, ['0'], {}, explicit_unknown)
    with pytest.raises(PCWError): validate_assessment_scope(store, ['0'], {'workspaces':['p']}, explicit_unknown)
    with pytest.raises(PCWError): validate_assessment_scope(store, ['0'], {'event_ids':['0','1']}, explicit_unknown)


def test_sqlite_json_instruction_refs_are_structured_and_text_availability():
    from workspace.analytics_queries import validate_assessment_scope
    store = Store()
    reference = {'status':'versioned', 'commit':'abc', 'path':'AGENTS.md'}
    store.add('2026-10-15', instructions_revision='rev', instructions_ref=json.dumps(reference))
    copied = [{'event_ids':['0'], 'instructions_revision':'rev', 'status':'recorded', 'instructions_ref':reference}]
    assert validate_assessment_scope(store, ['0'], instruction_basis=copied)['instruction_basis'] == copied
    auto = validate_assessment_scope(store, ['0'])['instruction_basis'][0]
    assert auto['instructions_ref'] == reference and auto['rule_text_available']
    store.add('2026-10-15', instructions_revision='unversioned-rev', instructions_ref=json.dumps({'status':'unversioned'}))
    report = analytics_report(store, since='2026-10-15', until='2026-10-16')
    assert report['instruction_basis'][0]['instructions_ref'] == reference
    assert report['instruction_basis'][0]['rule_text_available']
    assert report['instruction_basis'][1]['instructions_revision'] == 'unversioned-rev'
    assert not report['instruction_basis'][1]['rule_text_available']


def test_implicit_upper_bound_includes_same_clock_tick_but_explicit_stays_exclusive(monkeypatch):
    from workspace import analytics_queries
    fixed = datetime(2026, 10, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is not None else fixed.replace(tzinfo=None)
    monkeypatch.setattr(analytics_queries, 'datetime', FixedClock)
    store = Store()
    store.add(fixed.isoformat())
    implicit = query_events(store)
    assert implicit['total_events'] == 1
    assert implicit['scope']['until'] == '2026-10-15T12:00:00.123457+00:00'
    assert query_events(store, since='2026-10-15')['total_events'] == 1
    assert query_events(store, since='2026-10-15', until=fixed.isoformat())['total_events'] == 0
