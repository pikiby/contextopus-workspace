"""Temporary cards are optional, revision-checked and dependency-aware."""
import pytest
from test_cycles import start
from test_runtime import Factory
from test_workspace_v3 import create
from pcw_runtime.cycles import CycleRuntime


@pytest.fixture
def workspace(tmp_path):
    factory = Factory()
    runtime = CycleRuntime(tmp_path, "project", provider_factory=factory)
    create(runtime, cleanup_approval_required=True)  # Explicit conservative policy for deletion approval tests.
    yield runtime, factory
    runtime.close()
from pcw_runtime import PCWError


def test_optional_cards_and_verbatim_request(workspace):
    runtime, _ = workspace
    text = '  Original user request\nwith exact whitespace  '
    cycle = start(runtime, user_request=text)
    assert not (runtime.engine.root / '.tmp').exists()
    assert runtime.cycles_list()['cycles'][0]['user_request'] == text
    clarification = '\nKeep THIS verbatim. '
    runtime.clarification_add(cycle, clarification)
    assert runtime.status(cycle)['clarifications'] == [clarification]
    assert runtime.finish(cycle)['status'] == 'closed'


def test_card_metadata_links_revisions_and_dependencies(workspace):
    runtime, _ = workspace
    a, b = start(runtime, 'one'), start(runtime, 'two')
    first = runtime.tmp_write(a, '# A readable finding\n\nEvidence', description='Work in progress')
    assert first['path'].endswith('/A readable finding.md')
    assert first['metadata']['id'] == first['id']
    assert 'context/project' in first['metadata']['tags']
    other = runtime.tmp_write(b, '# Other work', links=[first['id']])
    with pytest.raises(PCWError, match='incoming'):
        runtime.tmp_delete(a, first['id'], first['revision'])
    updated = runtime.tmp_write(a, 'New evidence', first['id'], first['revision'])
    assert updated['id'] == first['id']
    with pytest.raises(PCWError, match='stale'):
        runtime.tmp_write(a, 'Lost update', first['id'], first['revision'])
    runtime.tmp_delete(b, other['id'], other['revision'], confirmation='Delete this card')
    runtime.tmp_delete(a, updated['id'], updated['revision'], confirmation='Delete this card')


def test_cleanup_requires_exact_revision_and_preserves_other_work(workspace):
    runtime, _ = workspace
    a, b = start(runtime, 'one'), start(runtime, 'two')
    card = runtime.tmp_write(a, '# Draft')
    runtime.tmp_write(b, '# Independent')
    snapshot = runtime.finish(a)
    assert snapshot['status'] == 'blocked'
    runtime.tmp_write(a, 'Changed', card['id'], card['revision'])
    assert runtime.finish(a, confirmation='Delete', expected_revision=snapshot['cleanup_revision'])['status'] == 'blocked'
    snapshot = runtime.status(a)
    assert runtime.finish(a, confirmation='Delete', expected_revision=snapshot['cleanup_revision'])['status'] == 'closed'
    assert runtime.tmp_list(b)['total'] == 1


def test_helper_shared_sources_and_reference_dependencies(tmp_path):
    from workspace.temporary_materials import temporary_card, temporary_dependencies, card_name
    from workspace.records import record_text
    target = {'id': 'stable', 'path': '.tmp/work/materials/Finding.md'}
    examples = ['[[Finding]]', '[finding](.tmp/work/materials/Finding.md)',
                '[finding](<.tmp/work/materials/Finding.md>)',
                '[finding](.tmp/work/materials/Finding.md \"Finding title\")',
                '```yaml\nsources:\n  - reference: stable\n    instruction: Read the evidence\n```',
                '```yaml\nrequired_sources:\n  - url: stable\n    extract: Read the evidence\n```']
    for body in examples:
        assert 'stable' in temporary_dependencies({'content': body}, [(None, target)])
    note = tmp_path / card_name(None, '# Readable name\nEvidence')
    note.write_text(record_text({'id': 'stable', 'type': 'knowledge', 'links': []}, examples[-2]), encoding='utf-8', newline='')
    result = temporary_card(note, tmp_path)
    assert result['id'] == 'stable'
    assert result['sources'][0]['reference'] == 'stable'


def test_delete_cleanup_policy_and_rename(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    card = runtime.tmp_write(cycle, '# Original', description='A readable draft')
    assert runtime.tmp_delete(cycle, card['id'], card['revision'])['status'] == 'blocked'
    renamed = runtime.tmp_write(cycle, '# Renamed', card['id'], card['revision'], name='New readable name')
    assert renamed['id'] == card['id']
    assert renamed['path'].endswith('/New readable name.md')
    assert renamed['title'] == 'Renamed'
    assert renamed['description'] == 'A readable draft'
    assert not (runtime.engine.root / card['path']).exists()
    preview = runtime.settings_preview({'cleanup_approval_required': False}, 'Automatic cleanup')
    runtime.settings_apply(preview['preview_revision'], 'Approve cleanup policy')
    assert runtime.tmp_delete(cycle, renamed['id'], renamed['revision'])['deleted']


def test_readable_link_targets_and_required_source_guards(workspace):
    runtime, _ = workspace
    a, b = start(runtime, 'one'), start(runtime, 'two')
    card = runtime.tmp_write(a, '# Evidence', description='What was observed')
    target = runtime.tmp_write(b, '# Related', links=[card['id']])
    read = runtime.tmp_read(b, target['id'])
    assert read['links'] == [card['id']]
    assert read['link_targets'][0]['title'] == 'Evidence'
    with pytest.raises(PCWError, match='incoming'):
        runtime.tmp_write(a, '# Evidence', card['id'], card['revision'], name='Moved')
    for body in ['[[Evidence]]', f"[finding]({card['path']})",
                 f"```yaml\nsources:\n  - reference: {card['id']}\n    instruction: Read this evidence\n```"]:
        runtime.tmp_write(b, body, target['id'], runtime.tmp_read(b, target['id'])['revision'])
        with pytest.raises(PCWError, match='incoming'):
            runtime.tmp_delete(a, card['id'], card['revision'], confirmation='Delete')
        with pytest.raises(PCWError, match='cannot depend'):
            runtime.proposal_prepare(a, [{'key': 'bad', 'content': body, 'description': 'Must remain temporary'}])


def test_helper_external_urls_and_system_tmp_are_not_memory_dependencies():
    from workspace.temporary_materials import temporary_dependencies
    for body in ['[external](https://example.org/tmp/page)', '[system](/tmp/unrelated.log)',
                 'A plain prose discussion of .tmp/ and /tmp directories.']:
        assert not temporary_dependencies({'content': body}, [])


def test_automatic_cleanup_still_rejects_supplied_stale_revision(workspace):
    runtime, _ = workspace
    preview = runtime.settings_preview({'cleanup_approval_required': False}, 'Allow automatic cleanup')
    runtime.settings_apply(preview['preview_revision'], 'Approve this cleanup policy')
    cycle = start(runtime)
    card = runtime.tmp_write(cycle, '# Initial evidence')
    original = runtime.status(cycle)['cleanup_revision']
    runtime.tmp_write(cycle, '# Changed evidence', card['id'], card['revision'])
    blocked = runtime.finish(cycle, expected_revision=original)
    assert blocked['status'] == 'blocked'
    assert 'stale' in blocked['reason']
    assert runtime.tmp_read(cycle, card['id'])['title'] == 'Changed evidence'
    assert runtime.finish(cycle, expected_revision=blocked['cleanup_revision'])['status'] == 'closed'


def test_apply_rechecks_temporary_targets_created_after_preparation(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    proposal = runtime.proposal_prepare(cycle, [{'key': 'fact', 'content': '# Permanent\nSee [[Later]].',
                                               'description': 'Approval timing evidence'}])
    runtime.tmp_write(cycle, '# Later\nTransient observations')
    with pytest.raises(PCWError, match='cannot depend'):
        runtime.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'], confirmation='Approve')
    assert runtime.status(cycle)['materials'] == 1


@pytest.mark.parametrize('field', ['sources', 'required_sources'])
def test_metadata_sources_protect_deletion_and_permanent_creation(workspace, field):
    from workspace.records import record_text
    runtime, _ = workspace
    cycle = start(runtime)
    card = runtime.tmp_write(cycle, '# Evidence')
    reference_key = 'reference' if field == 'sources' else 'url'
    metadata = {field: [{reference_key: card['id'], 'instruction': 'Read the evidence'}]}
    body = record_text({'type': 'knowledge', **metadata}, '# Dependent draft')
    runtime.tmp_write(cycle, body)
    with pytest.raises(PCWError, match='incoming'):
        runtime.tmp_delete(cycle, card['id'], card['revision'], confirmation='Delete')
    with pytest.raises(PCWError, match='cannot depend'):
        runtime.proposal_prepare(cycle, [{'key': 'bad', 'content': body, 'description': 'Must stay temporary'}])


def test_cross_context_temporary_links_and_manual_permanent_dependencies(workspace):
    from workspace.records import record_text
    runtime, _ = workspace
    other = CycleRuntime(runtime.engine.work_root, 'other', provider_factory=Factory())
    create(other)
    try:
        cycle = start(runtime)
        foreign_cycle = start(other, 'foreign')
        target = other.tmp_write(foreign_cycle, '# Foreign draft')
        with pytest.raises(PCWError, match='cannot depend'):
            runtime.proposal_prepare(cycle, [{'key': 'bad', 'content': 'See [[Foreign draft]].',
                                             'description': 'Cannot depend on another context draft'}])
        linked = runtime.tmp_write(cycle, '# Working dependency', links=[target['id']])
        assert runtime.tmp_read(cycle, linked['id'])['link_targets'][0]['workspace'] == 'other'
        with pytest.raises(PCWError, match='incoming'):
            other.tmp_delete(foreign_cycle, target['id'], target['revision'], confirmation='Delete')
        runtime.tmp_delete(cycle, linked['id'], linked['revision'], confirmation='Delete')
        # Simulate a preexisting/manual permanent dependency without using managed proposals.
        permanent = runtime.engine.knowledge / 'Manual legacy dependency.md'
        permanent.write_text(record_text({'id': 'manual-legacy', 'type': 'knowledge', 'links': [],
                                          'sources': [{'reference': target['id'], 'instruction': 'Read evidence'}]},
                                         '# Legacy dependency'), encoding='utf-8', newline='')
        with pytest.raises(PCWError, match='Permanent knowledge depends'):
            other.tmp_delete(foreign_cycle, target['id'], target['revision'], confirmation='Delete')
        snapshot = other.status(foreign_cycle)
        blocked = other.finish(foreign_cycle, confirmation='Delete', expected_revision=snapshot['cleanup_revision'])
        assert blocked['status'] == 'blocked'
        assert 'Permanent knowledge depends' in blocked['reason']
        assert other.tmp_read(foreign_cycle, target['id'])
    finally:
        other.close()


def test_temporary_creation_cannot_activate_unresolved_permanent_dependency(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    proposal = runtime.proposal_prepare(cycle, [{'key': 'fact', 'content': '# Permanent\nSee [[Future draft]].',
                                               'description': 'An unresolved ordinary link'}])
    runtime.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'], confirmation='Approve')
    with pytest.raises(PCWError, match='Permanent knowledge would depend'):
        runtime.tmp_write(cycle, '# Future draft')
    assert runtime.tmp_list(cycle)['total'] == 0


def test_cleanup_snapshots_opaque_binary_attachments_with_exact_cas(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    attachment = runtime._tmp(cycle) / 'resources' / 'opaque-result.bin'
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b'\xff\x00\x80opaque')
    original = runtime.status(cycle)
    assert attachment.relative_to(runtime.engine.root).as_posix() in original['temporary_files']
    attachment.write_bytes(b'\xfe\x00\x80changed')
    blocked = runtime.finish(cycle, confirmation='Delete attachments', expected_revision=original['cleanup_revision'])
    assert blocked['status'] == 'blocked'
    assert attachment.read_bytes() == b'\xfe\x00\x80changed'
    assert runtime.finish(cycle, confirmation='Delete attachments', expected_revision=blocked['cleanup_revision'])['status'] == 'closed'
    assert not attachment.exists()


@pytest.mark.parametrize('name', ['CON', 'aux.md', 'PrN.notes.md', 'NUL', 'COM1', 'LPT9', 'ＣＯＭ１'])
def test_helper_portable_card_names_disambiguate_device_basenames(name):
    from workspace.temporary_materials import card_name
    result = card_name(name, '')
    assert result.startswith('Note - ')
    assert result.endswith('.md')


def test_explicit_temporary_names_reject_portable_case_and_unicode_collisions(workspace):
    runtime, _ = workspace
    cycle = start(runtime)
    original = runtime.tmp_write(cycle, '# First', name='Summary')
    for name in ['summary', 'SUMMARY.MD', 'Ｓｕｍｍａｒｙ']:
        with pytest.raises(PCWError, match='name already exists'):
            runtime.tmp_write(cycle, '# Conflicting', name=name)
    other = runtime.tmp_write(cycle, '# Another', name='Another')
    with pytest.raises(PCWError, match='name already exists'):
        runtime.tmp_write(cycle, '# Another', other['id'], other['revision'], name='SUMMARY')
    assert runtime.tmp_read(cycle, original['id'])['title'] == 'First'
    assert runtime.tmp_list(cycle)['total'] == 2


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_foreign_historical_source_excerpt_does_not_poison_inventory(workspace, newline):
    from workspace.records import record_text
    runtime, _ = workspace
    other = CycleRuntime(runtime.engine.work_root, 'historical', provider_factory=Factory())
    create(other)
    try:
        foreign_cycle = start(other, 'old-draft')
        path = other._tmp(foreign_cycle) / 'materials' / 'old-draft.md'
        path.parent.mkdir(parents=True)
        malformed = '# Historical draft\n```yaml\nrequired_sources:\n  - url: https://example.org/data\n    extract: muninn: IP and other examples\n```\n'
        malformed = malformed.replace('\n', newline)
        path.write_text(record_text({'id': 'old-draft', 'type': 'knowledge', 'links': []}, malformed), encoding='utf-8', newline='')
        cycle = start(runtime)
        current = runtime.tmp_write(cycle, '# Current work')
        assert runtime.tmp_list(cycle)['total'] == 1
        assert runtime.tmp_read(cycle, current['id'])['title'] == 'Current work'
        selected = other.tmp_read(foreign_cycle, 'old-draft')
        assert selected['content'] == malformed
        assert selected['source_diagnostics'][0]['path'].endswith('/old-draft.md')
        assert runtime.tmp_delete(cycle, current['id'], current['revision'], confirmation='Delete')['deleted']
    finally:
        other.close()


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_helper_malformed_draft_sources_retain_identifiable_dependencies(newline):
    from workspace.temporary_materials import temporary_dependencies
    target = {'id': 'evidence', 'path': '.tmp/work/materials/Evidence.md'}
    body = '```yaml\nrequired_sources:\n  - url: evidence\n    extract: bad: YAML example\n```'
    body = body.replace('\n', newline)
    assert 'evidence' in temporary_dependencies({'content': body}, [(None, target)])


@pytest.mark.parametrize('actual', [
    r'C:\memory\modules\workspace\workspace_contexts\context\.tmp\work\materials\Finding.md',
    r'\\server\share\memory\modules\workspace\workspace_contexts\context\.tmp\work\materials\Finding.md',
    r'\memory\modules\workspace\workspace_contexts\context\.tmp\work\materials\Finding.md',
])
def test_windows_absolute_dependencies_match_only_the_inventoried_path(actual):
    from pathlib import PureWindowsPath
    from workspace.temporary_materials import temporary_dependencies

    actual = PureWindowsPath(actual)
    target = {'id': 'finding', 'path': '.tmp/work/materials/Finding.md'}
    for reference in (str(actual), actual.as_posix(), str(actual).upper()):
        assert 'finding' in temporary_dependencies({'metadata': {'sources': [reference]}}, [(actual, target)])
    external = PureWindowsPath(r'C:\unrelated\project\.tmp\work\materials\Finding.md')
    for reference in (str(external), external.as_posix()):
        assert temporary_dependencies({'metadata': {'sources': [reference]}}, [(actual, target)]) == set()


def test_helper_alfa_historical_project_sources_are_not_managed_temporary_paths():
    from workspace.temporary_materials import temporary_dependencies, is_contextopus_temporary_reference
    prefix = 'tmp/20260817T172901Z-persistent-context-workspace-restart/'
    sources = [prefix + name for name in ['findings/R1-source-vm-debezium.md',
               'findings/R2-nats-clickhouse.md', 'findings/R3-px701-runbook-failures.md',
               'work/W1-diagnostic-foundation.md']]
    assert temporary_dependencies({'metadata': {'sources': sources}, 'content': '# Diagnostic contract'}, []) == set()
    for value in sources + ['/tmp/external.log', 'project/tmp/build.md', '/project/.tmp/cache.md']:
        assert not is_contextopus_temporary_reference(value)
    for value in ['.tmp/future/materials/not-created.md', '.pcw/other/tmp/future/note.md',
                  '/memory/modules/workspace/workspace_contexts/other/.tmp/future/note.md']:
        assert is_contextopus_temporary_reference(value)
    assert is_contextopus_temporary_reference('tmp/known-cycle/findings/absent.md', known_cycles={'known-cycle'})
    from pathlib import Path
    target = {'id': 'finding', 'path': '.tmp/work/materials/Finding.md'}
    actual = Path('/memory/modules/workspace/workspace_contexts/context/.tmp/work/materials/Finding.md')
    for source in ['/project/.tmp/work/materials/Finding.md', 'project/.tmp/work/materials/Finding.md']:
        assert temporary_dependencies({'metadata': {'sources': [source]}}, [(actual, target)]) == set()
    assert 'finding' in temporary_dependencies({'metadata': {'sources': [str(actual)]}}, [(actual, target)])


def test_historical_project_sources_preserved_but_managed_absent_targets_rejected(workspace):
    from workspace.records import record_text
    runtime, _ = workspace
    cycle = start(runtime)
    prefix = 'tmp/20260817T172901Z-persistent-context-workspace-restart/'
    sources = [prefix + name for name in ['findings/R1-source-vm-debezium.md',
               'findings/R2-nats-clickhouse.md', 'findings/R3-px701-runbook-failures.md',
               'work/W1-diagnostic-foundation.md']]
    content = record_text({'type': 'knowledge', 'sources': sources}, '# Diagnostic contract')
    proposal = runtime.proposal_prepare(cycle, [{'key': 'diagnostic', 'content': content,
                                                'description': 'Preserved historical repository evidence paths'}])
    assert runtime.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'], confirmation='Approve')['status'] == 'applied'
    assert runtime.read(material_id=proposal['created_ids']['diagnostic'])['content'] == content
    for source in ['.tmp/not-yet-created/materials/absent.md', f'tmp/{cycle}/findings/absent.md']:
        body = record_text({'type': 'knowledge', 'sources': [source]}, '# Unsafe dependency')
        with pytest.raises(PCWError, match='cannot depend'):
            runtime.proposal_prepare(cycle, [{'key': 'unsafe', 'content': body,
                                             'description': 'An absent but explicitly managed temporary source'}])
