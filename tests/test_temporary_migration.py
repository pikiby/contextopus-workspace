"""Synthetic fixtures never open or migrate the user's live workspace."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from workspace.migration import FormatMigrationMixin
from workspace.temporary_migration import temporary_transforms, permanent_temporary_blockers
from workspace.runtime import PCWError, _parse
from workspace.context_identity import assert_unique_context_names


def fixture_runtime(root):
    knowledge = root / 'knowledge'
    knowledge.mkdir()
    return SimpleNamespace(engine=SimpleNamespace(root=root, knowledge=knowledge, _assert_safe=lambda p: None))


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_temporary_tree_preserves_pending_proposal_and_normalizes_cards(tmp_path, newline):
    runtime = fixture_runtime(tmp_path)
    material = tmp_path / 'tmp/cycle/materials/123.md'
    material.parent.mkdir(parents=True)
    original = newline.join(['# Ongoing investigation', 'Unfinished evidence.', ''])
    material.write_text(original, encoding='utf-8', newline='')
    proposal = tmp_path / 'tmp/cycle/proposals/p.json'
    proposal.parent.mkdir()
    immutable = json.dumps({'revision': 'approved-digest', 'pending': True})
    proposal.write_text(immutable, encoding='utf-8', newline='')
    review = 'Reviewed original text' + newline
    proposal.with_suffix('.md').write_text(review, encoding='utf-8', newline='')
    first = temporary_transforms(runtime)
    assert temporary_transforms(runtime) == first
    card = first['tmp/cycle/materials/123.md']
    assert card['path'] == '.tmp/cycle/materials/Ongoing investigation.md'
    assert _parse(card['text'])[0]['id'] == '123.md'
    assert _parse(card['text'])[1] == original
    assert material.read_bytes() == original.encode('utf-8')
    assert first['tmp/cycle/proposals/p.json']['text'] == immutable
    assert first['tmp/cycle/proposals/p.md']['text'] == review
    assert material.is_file() and proposal.is_file()  # planning has no writes


def test_permanent_to_temporary_dependency_blocks_without_removing_text(tmp_path):
    runtime = fixture_runtime(tmp_path)
    material = tmp_path / 'tmp/cycle/materials/123.md'
    material.parent.mkdir(parents=True)
    material.write_text('# Evidence\n', encoding='utf-8', newline='')
    permanent = runtime.engine.knowledge / 'Fact.md'
    original = '---\nid: fact\ntype: knowledge\nlinks: [123.md]\n---\nUses [[tmp/cycle/materials/123.md]].\n'
    permanent.write_text(original, encoding='utf-8', newline='')
    blockers = permanent_temporary_blockers(runtime, temporary_transforms(runtime))
    assert len(blockers) == 2
    assert permanent.read_text() == original


def test_existing_normalized_context_duplicates_are_rejected(tmp_path):
    (tmp_path / 'Alpha').mkdir()
    (tmp_path / 'Ａlpha').mkdir()
    with pytest.raises(PCWError, match='collision'):
        assert_unique_context_names(SimpleNamespace(workspace_base=tmp_path))


def test_v4_settings_upgrade_preserves_permissions_and_imports_new_default(monkeypatch, tmp_path):
    from workspace import migration
    from workspace.configuration import WORKSPACE_POLICY_DEFAULTS
    runtime = FormatMigrationMixin()
    runtime.engine = SimpleNamespace(root=tmp_path, workspace='alpha')
    runtime.control = SimpleNamespace(layout=object())
    (tmp_path / 'INSTRUCTIONS.md').write_text('# Author rules\nRetain exactly.\n', encoding='utf-8', newline='')
    legacy = {key: default for key, default in WORKSPACE_POLICY_DEFAULTS.items() if key != 'cleanup_approval_required'}
    legacy['knowledge_approval_required'] = False
    monkeypatch.setattr(migration, 'configuration_text', lambda *args: json.dumps(legacy))
    configuration, instructions = runtime._settings_transition(4, {})
    migrated = json.loads(configuration['after'])
    assert all(migrated[key] == value for key, value in legacy.items())
    assert migrated.get('cleanup_approval_required', True) is True
    assert instructions['after'] == '# Author rules\nRetain exactly.\n'


@pytest.mark.parametrize("interrupt_kind", ["first-file", "proposal-json", "proposal-review"])
def test_v4_migration_retains_cycle_approvals_and_recovers_file_interruption(tmp_path, monkeypatch, interrupt_kind):
    from contextlib import nullcontext
    from workspace.cycles import CycleRuntime, _json
    from workspace.runtime import _revision
    from workspace.settings import save_configuration
    from workspace.workspaces import encode
    from workspace.configuration import WORKSPACE_POLICY_DEFAULTS
    runtime = CycleRuntime(tmp_path, 'alpha')
    root = runtime.engine.root
    root.mkdir(parents=True)
    runtime.engine.knowledge.mkdir()
    rules = '# Existing rules\nRespect the reviewed approval.\n'
    (root / 'INSTRUCTIONS.md').write_text(rules, encoding='utf-8', newline='')
    runtime.control.root.mkdir(parents=True)
    runtime.control.write(runtime._format_path(), encode({'format_version': 4}))
    config = {k: v for k, v in WORKSPACE_POLICY_DEFAULTS.items() if k != 'cleanup_approval_required'}
    config['git_history_enabled'] = False
    save_configuration(runtime.control.layout, 'alpha', encode(config))
    runtime.engine.state.mkdir(parents=True, exist_ok=True)
    (runtime.engine.state / 'lancedb').mkdir()
    (runtime.engine.state / 'active.json').write_text('{}', encoding='utf-8', newline='')
    material = root / 'tmp/cycle/materials/old.md'
    material.parent.mkdir(parents=True)
    original_material = '# Pending investigation\nUnfinished results.\n'
    material.write_text(original_material, encoding='utf-8', newline='')
    text = '---\nid: fact\ntype: knowledge\nlinks: []\n---\n# Proposed fact\nKeep approval binding.\n'
    plan = {'cycle_id': 'cycle', 'proposal_id': 'proposal', 'kind': 'knowledge',
            'created_ids': {'new': 'fact'}, 'entries': {'fact': {'before': None,
            'after': {'path': 'knowledge/Proposed-fact.md', 'text': text, 'fingerprint': _revision(text), 'link_ids': []}}}}
    serialized = _json(plan)
    revision = _revision(serialized)
    records = {'starts': {'original-request': 'cycle'}, 'cycles': {'cycle': {'state': 'active',
               'purpose': 'work', 'continuation': 'Do not lose this continuation',
               'proposals': {'proposal': {'revision': revision, 'state': 'prepared'}}}}}
    runtime.control.write(runtime.record_path, _json(records))
    proposal = root / 'tmp/cycle/proposals/proposal.json'
    proposal.parent.mkdir()
    proposal.write_text(serialized, encoding='utf-8', newline='')
    proposal.with_suffix('.md').write_text(runtime._review(plan), encoding='utf-8', newline='')
    monkeypatch.setattr(runtime.engine, '_guard', lambda: nullcontext())
    monkeypatch.setattr(runtime.engine, 'reconcile', lambda: {'pending_count': 0})
    monkeypatch.setattr(runtime, '_history_record', lambda *args, **kwargs: None)
    reviewed = runtime.upgrade_plan()
    assert reviewed['ready'], reviewed['blockers']
    original_write = runtime.engine._write
    interrupted = False
    def fail_once(path, text):
        nonlocal interrupted
        target = Path(path)
        selected = (interrupt_kind == 'first-file' or
                    interrupt_kind == 'proposal-json' and target.name == 'proposal.json' or
                    interrupt_kind == 'proposal-review' and target.name == 'proposal.md')
        if '.tmp' in target.parts and selected and not interrupted:
            interrupted = True
            raise OSError('simulated interruption')
        return original_write(path, text)
    monkeypatch.setattr(runtime.engine, '_write', fail_once)
    with pytest.raises(OSError, match='simulated'):
        runtime.upgrade_apply(reviewed['revision'], 'Migrate this synthetic context')
    monkeypatch.setattr(runtime.engine, '_write', original_write)
    recovery = runtime.upgrade_plan()
    assert recovery['ready'], recovery['blockers']
    assert recovery['resume_revision'] == reviewed['revision']
    result = runtime.upgrade_apply(reviewed['revision'], 'Resume this exact migration')
    assert result['status'] == 'complete'
    assert runtime._records() == records
    assert (root / '.tmp/cycle/proposals/proposal.json').read_text() == serialized
    assert (root / '.tmp/cycle/proposals/proposal.md').read_text() == runtime._review(plan)
    card = root / '.tmp/cycle/materials/Pending investigation.md'
    assert _parse(card.read_text())[0]['id'] == 'old.md'
    assert _parse(card.read_text())[1] == original_material
    assert not (root / 'tmp').exists()
    assert (Path(result['backup']) / 'tmp/cycle/materials/old.md').read_text() == original_material
    assert runtime._execution_plan(plan)['entries']['fact']['after']['text'] != text
    assert runtime._load_plan('cycle', 'proposal', records['cycles']['cycle']['proposals']['proposal']) == plan
    assert runtime.upgrade_apply(reviewed['revision'], 'Replay')['replayed']


def test_pending_review_applies_original_revision_after_real_migration(tmp_path):
    from workspace.cycles import CycleRuntime
    from workspace.workspaces import encode
    from test_runtime import Factory
    from test_workspace_v3 import create
    runtime = CycleRuntime(tmp_path, 'alpha', provider_factory=Factory())
    try:
        create(runtime, git_history_enabled=False)
        cycle = runtime.startup('original-user-request')['cycle_id']
        runtime.tmp_write(cycle, '# Working investigation\nKeep unfinished evidence.')
        proposal = runtime.proposal_prepare(cycle, expected_task_revision=runtime.status(cycle)['task_revision'], changes=[{'key': 'fact', 'content': '# Reviewed fact\nVerified evidence.',
            'description': 'Records the reviewed fact from a synthetic migration fixture'}])
        # The source fixture represents a release without edit reservations.
        with runtime._cycle(cycle) as (records, state):
            entry = state['proposals'][proposal['proposal_id']]
            old_plan = runtime._load_plan(cycle, proposal['proposal_id'], entry)
            runtime._edits().release(cycle)
            old_plan.pop('edit_reservations', None)
            proposal = runtime._save_plan(records, state, old_plan, 'prepared')
        original_path = runtime._plan_path(cycle, proposal['proposal_id'])
        original_bytes = original_path.read_bytes()
        review_bytes = original_path.with_suffix('.md').read_bytes()
        runtime.control.write(runtime._format_path(), encode({'format_version': 4}))
        (runtime.engine.root / '.tmp').rename(runtime.engine.root / 'tmp')
        planned = runtime.upgrade_plan()
        assert planned['ready'], planned['blockers']
        result = runtime.upgrade_apply(planned['revision'], 'Migrate this isolated test')
        assert result['status'] == 'complete'
        migrated_path = runtime._plan_path(cycle, proposal['proposal_id'])
        assert migrated_path.read_bytes() == original_bytes
        assert migrated_path.with_suffix('.md').read_bytes() == review_bytes
        applied = runtime.proposal_apply(cycle, proposal['proposal_id'], proposal['revision'], confirmation='I approve the original reviewed proposal')
        assert applied['status'] == 'applied'
        material = runtime.read(material_id=proposal['created_ids']['fact'])
        assert material['content'] == '# Reviewed fact\nVerified evidence.'
        assert 'context/alpha' in _parse((runtime.engine.root / material['path']).read_text())[0]['tags']
        assert runtime.tmp_list(cycle)['total'] == 1
    finally:
        runtime.close()


def test_working_card_links_follow_both_temporary_and_permanent_paths(tmp_path):
    from workspace.temporary_migration import retarget_temporary
    runtime = fixture_runtime(tmp_path)
    source = tmp_path / 'tmp/cycle/materials/first.md'
    source.parent.mkdir(parents=True)
    source.write_text('# First\nSee [[tmp/cycle/materials/second.md]] and [[knowledge/old.md|Source]].\n', encoding='utf-8', newline='')
    source.with_name('second.md').write_text('# Second\nStill working.\n', encoding='utf-8', newline='')
    transforms = temporary_transforms(runtime)
    retarget_temporary(transforms, {'knowledge/old.md': {'path': 'knowledge/Readable.md'}}, {})
    text = transforms['tmp/cycle/materials/first.md']['text']
    assert '[[.tmp/cycle/materials/Second.md]]' in text
    assert '[[knowledge/Readable.md|Source]]' in text
    assert source.read_text().startswith('# First')


def test_source_paths_move_without_rewriting_author_conditions(tmp_path):
    from workspace.temporary_migration import retarget_temporary
    runtime = fixture_runtime(tmp_path)
    source = tmp_path / 'tmp/cycle/materials/first.md'
    source.parent.mkdir(parents=True)
    source.write_text('# First\n```yaml\nsources:\n- reference: tmp/cycle/materials/second.md # retain comment\n  instruction: Verify the exact approved conditions.\n```\n', encoding='utf-8', newline='')
    source.with_name('second.md').write_text('# Second\n', encoding='utf-8', newline='')
    transforms = temporary_transforms(runtime)
    retarget_temporary(transforms, {}, {})
    text = transforms['tmp/cycle/materials/first.md']['text']
    assert 'reference: ".tmp/cycle/materials/Second.md" # retain comment' in text
    assert 'instruction: Verify the exact approved conditions.' in text


def test_pending_permanent_text_cannot_depend_on_temporary_sources(tmp_path):
    from workspace.temporary_migration import permanent_temporary_references
    runtime = fixture_runtime(tmp_path)
    source = tmp_path / 'tmp/cycle/materials/first.md'
    source.parent.mkdir(parents=True)
    source.write_text('# First\n', encoding='utf-8', newline='')
    text = '# Permanent draft\n```yaml\nsources:\n- reference: tmp/cycle/materials/first.md\n  instruction: Retain this unfinished source condition.\n```\n'
    assert permanent_temporary_references(text, temporary_transforms(runtime)) == ['tmp/cycle/materials/first.md']


@pytest.mark.parametrize('reference', ['[[Evidence.md|Alias]]', '[evidence](<.tmp/cycle/materials/Evidence.md>)'])
def test_native_alias_and_angle_markdown_cannot_hide_transient_target(reference):
    from workspace.temporary_migration import permanent_temporary_references
    transforms = {'tmp/cycle/materials/opaque.md': {'path': '.tmp/cycle/materials/Evidence.md',
                  'text': '---\nid: stable\ntype: knowledge\n---\n# Evidence\n'}}
    assert permanent_temporary_references('# Permanent\n' + reference, transforms)


@pytest.mark.parametrize('field,key', [('sources', 'reference'), ('required_sources', 'url')])
def test_metadata_source_paths_block_and_custom_fields_survive_retarget(tmp_path, field, key):
    from workspace.temporary_migration import permanent_temporary_references, retarget_temporary
    from workspace.records import record_text
    runtime = fixture_runtime(tmp_path)
    source = tmp_path / 'tmp/cycle/materials/first.md'
    source.parent.mkdir(parents=True)
    metadata = {'id': 'first-id', 'type': 'knowledge', 'links': ['[[tmp/cycle/materials/second.md|Author alias]]'],
                'custom': {'nested': ['verbatim condition', 42]},
                field: [{key: 'tmp/cycle/materials/second.md', 'instruction': 'Preserve exactly', 'custom_source': True}]}
    source.write_text(record_text(metadata, '# First\n'), encoding='utf-8', newline='')
    source.with_name('second.md').write_text('# Second\n', encoding='utf-8', newline='')
    transforms = temporary_transforms(runtime)
    assert permanent_temporary_references(source.read_text(), transforms)
    retarget_temporary(transforms, {}, {})
    migrated, body = _parse(transforms['tmp/cycle/materials/first.md']['text'])
    assert migrated['id'] == metadata['id']
    assert migrated['custom'] == metadata['custom']
    assert migrated['links'] == ['[[.tmp/cycle/materials/Second.md|Author alias]]']
    assert migrated[field][0][key] == '.tmp/cycle/materials/Second.md'
    assert migrated[field][0]['instruction'] == 'Preserve exactly'
    assert migrated[field][0]['custom_source'] is True
    assert body == '# First\n'


def test_cross_context_permanent_reference_blocks_migration(tmp_path):
    from workspace.cycles import CycleRuntime
    from workspace.workspaces import encode
    from workspace.records import record_text
    from test_runtime import Factory
    from test_workspace_v3 import create
    a = CycleRuntime(tmp_path, 'alpha', provider_factory=Factory())
    b = CycleRuntime(tmp_path, 'beta', provider_factory=Factory())
    try:
        create(a, git_history_enabled=False)
        create(b, git_history_enabled=False)
        cb = b.startup('foreign-work')['cycle_id']
        b.tmp_write(cb, '# Foreign evidence\nTransient results.')
        (a.engine.knowledge / 'Foreign dependency.md').write_text(record_text(
            {'id': 'foreign-dependent', 'type': 'knowledge', 'links': [],
             'sources': [{'reference': 'Foreign evidence.md', 'instruction': 'Retain source condition'}]}, '# Permanent\n'), encoding='utf-8', newline='')
        a.control.write(a._format_path(), encode({'format_version': 4}))
        planned = a.upgrade_plan()
        assert not planned['ready']
        assert any(blocker.get('target') == 'Foreign evidence.md' for blocker in planned['blockers'])
        assert (b.engine.root / '.tmp').is_dir()
    finally:
        a.close(); b.close()


def test_embedded_frontmatter_in_reviewed_body_does_not_hide_temporary_source():
    from workspace.temporary_migration import permanent_temporary_references
    from workspace.records import record_text
    body = '---\nsources:\n- reference: .tmp/cycle/materials/Evidence.md\n  instruction: Preserve condition\n---\n# Proposed permanent body\n'
    snapshot = record_text({'id': 'permanent', 'type': 'knowledge', 'links': []}, body)
    assert permanent_temporary_references(snapshot, {}) == ['.tmp/cycle/materials/Evidence.md']


def test_migration_readable_names_share_portable_device_and_unicode_rules(tmp_path):
    from workspace.records import record_text
    from workspace.temporary_materials import card_name_key
    runtime = fixture_runtime(tmp_path)
    directory = tmp_path / 'tmp/cycle/materials'
    directory.mkdir(parents=True)
    sources = {}
    for name, ident, body in [('CON.MD', 'device-id', '# Device finding\n'),
                              ('evidence.md', 'ascii-id', '# First finding\n'),
                              ('ＥＶＩＤＥＮＣＥ.md', 'unicode-id', '# Other finding\n')]:
        text = record_text({'id': ident, 'type': 'knowledge', 'links': [], 'custom': 'Keep this field'}, body)
        (directory / name).write_text(text, encoding='utf-8', newline='')
        sources['tmp/cycle/materials/' + name] = text
    transforms = temporary_transforms(runtime)
    assert transforms['tmp/cycle/materials/CON.MD']['path'] == '.tmp/cycle/materials/Note - CON.md'
    destinations = [item['path'] for item in transforms.values()]
    assert len(set(map(card_name_key, destinations))) == 3
    assert any('unicode-id' in destination for destination in destinations)
    for old, item in transforms.items():
        assert item['before'] == sources[old]
        assert _parse(item['text']) == _parse(sources[old])
        assert (tmp_path / old).read_text() == sources[old]


def test_malformed_foreign_draft_is_preserved_with_scoped_source_diagnostic(tmp_path):
    from workspace.cycles import CycleRuntime
    from workspace.workspaces import encode
    from test_runtime import Factory
    from test_workspace_v3 import create
    a = CycleRuntime(tmp_path, 'alpha', provider_factory=Factory())
    b = CycleRuntime(tmp_path, 'beta', provider_factory=Factory())
    body = '# Unfinished draft\n```yaml\nrequired_sources:\n- url: https://example.invalid/evidence\n  extract: Preserve conditions: this unfinished scalar needs quotes.\n```\n'
    try:
        create(a, git_history_enabled=False)
        create(b, git_history_enabled=False)
        cycle = b.startup('unfinished-source')['cycle_id']
        path = b._tmp(cycle) / 'materials' / 'legacy.md'
        b.engine._write(path, body)
        b.control.write(b._format_path(), encode({'format_version': 4}))
        unrelated = a.upgrade_plan()
        assert unrelated['ready'], unrelated['blockers']
        assert unrelated['temporary_source_diagnostics'] == []
        plan = b.upgrade_plan()
        assert plan['ready'], plan['blockers']
        diagnostic = plan['temporary_source_diagnostics'][0]
        assert diagnostic['path'].endswith('/materials/legacy.md')
        assert diagnostic['status'] == 'needs_correction'
        assert 'malformed' in diagnostic['reason']
        result = b.upgrade_apply(plan['revision'], 'Preserve and migrate unfinished work')
        assert result['status'] == 'complete'
        migrated = b.tmp_read(cycle, 'legacy.md')
        assert migrated['content'] == body
        assert migrated['sources_status'] == 'needs_correction'
        assert migrated['sources'] == []
        assert migrated['source_diagnostics']
        assert (Path(result['backup']) / path.relative_to(b.engine.root)).read_text() == body
    finally:
        a.close(); b.close()


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_malformed_draft_keeps_condition_while_exact_source_address_moves(tmp_path, newline):
    from workspace.temporary_migration import retarget_temporary, permanent_temporary_references
    runtime = fixture_runtime(tmp_path)
    directory = tmp_path / 'tmp/cycle/materials'
    directory.mkdir(parents=True)
    body = '# First\n```yaml\nrequired_sources:\n- url: tmp/cycle/materials/second.md # retain address comment\n  extract: Keep condition: malformed until explicitly corrected.\n```\n'
    body = body.replace('\n', newline)
    (directory / 'first.md').write_text(body, encoding='utf-8', newline='')
    (directory / 'second.md').write_text('# Second' + newline, encoding='utf-8', newline='')
    transforms = temporary_transforms(runtime)
    retarget_temporary(transforms, {}, {})
    item = transforms['tmp/cycle/materials/first.md']
    assert item['source_diagnostic']['status'] == 'needs_correction'
    assert 'url: ".tmp/cycle/materials/Second.md" # retain address comment' in item['text']
    assert 'extract: Keep condition: malformed until explicitly corrected.' in item['text']
    assert 'required_sources:' in item['text']
    assert _parse(item['text'])[1] == body.replace('url: tmp/cycle/materials/second.md',
        'url: ".tmp/cycle/materials/Second.md"')
    assert (directory / 'first.md').read_bytes() == body.encode('utf-8')
    assert permanent_temporary_references(item['text'], transforms, allow_malformed=True)


def test_v4_upgrade_retains_mixed_instruction_blocker_without_blocking_format(tmp_path):
    from workspace.cycles import CycleRuntime
    from workspace.runtime import _revision
    from workspace.workspaces import encode
    from workspace.settings import configuration_text
    from test_runtime import Factory
    from test_workspace_v3 import create
    r = CycleRuntime(tmp_path, 'alpha', provider_factory=Factory())
    try:
        create(r, git_history_enabled=False)
        cycle = r.startup('existing-mixed-review', purpose='instructions')['cycle_id']
        rules = r.instructions_read()['content'].encode('utf-8')
        settings = json.loads(configuration_text(r.control.layout, 'alpha'))
        before = '---\nknowledge_approval_required: true\n---\n# Previous human rules\n'
        after = before.replace('true', 'false')
        plan = {'cycle_id': cycle, 'proposal_id': 'mixed', 'kind': 'instructions', 'created_ids': {},
                'entries': {'INSTRUCTIONS.md': {'before': {'path': 'INSTRUCTIONS.md', 'text': before, 'fingerprint': _revision(before)},
                                              'after': {'path': 'INSTRUCTIONS.md', 'text': after, 'fingerprint': _revision(after)}}}}
        records = r._records()
        saved = r._save_plan(records, records['cycles'][cycle], plan, 'prepared')
        original = r._plan_path(cycle, 'mixed').read_bytes()
        review = r._plan_path(cycle, 'mixed').with_suffix('.md').read_bytes()
        blocked = {'revision': saved['revision'], 'blocked': 'Legacy mixed settings approval must be reviewed separately'}
        r.control.write(r.control.root / 'migration-v4.json', encode({'status': 'complete', 'identity_map': [],
                        'overlays': {cycle + '/mixed': blocked}}))
        r.control.write(r._format_path(), encode({'format_version': 4}))
        (r.engine.root / '.tmp').rename(r.engine.root / 'tmp')
        proposed = r.upgrade_plan()
        assert proposed['ready'], proposed['blockers']
        assert proposed['preserved_proposal_blockers'] == [{'proposal': cycle + '/mixed',
                    'revision': saved['revision'], 'reason': blocked['blocked']}]
        r.upgrade_apply(proposed['revision'], 'Upgrade format while retaining old approval limits')
        assert r._plan_path(cycle, 'mixed').read_bytes() == original
        assert r._plan_path(cycle, 'mixed').with_suffix('.md').read_bytes() == review
        assert r.instructions_read()['content'].encode('utf-8') == rules
        assert json.loads(configuration_text(r.control.layout, 'alpha')) == settings
        receipt = json.loads(r._migration_receipt_path().read_text())
        assert receipt['overlays'][cycle + '/mixed'] == blocked
        with pytest.raises(PCWError, match='mixed settings approval'):
            r.instructions_apply(cycle, 'mixed', saved['revision'], confirmation='Original approval')
        assert r.instructions_read()['content'].encode('utf-8') == rules
        # Earlier conversion paths retain their existing strict execution rule.
        with pytest.raises(PCWError, match='mixed settings approval'):
            r._proposal_overlays({}, {})
    finally:
        r.close()


def test_project_relative_tmp_sources_need_contextopus_evidence():
    from workspace.temporary_migration import permanent_temporary_references
    from workspace.records import record_text
    references = ['deploy/compose.yml', 'docs/diagnosis.md',
        'tmp/20260817T172901Z-persistent-context-workspace-restart/findings/R1-source-vm-debezium.md',
        'tmp/20260817T172901Z-persistent-context-workspace-restart/work/W1-diagnostic-foundation.md']
    text = record_text({'id': 'contract', 'type': 'knowledge', 'sources': references}, '# Contract\n')
    assert permanent_temporary_references(text, {}, known_cycles={'other-active-cycle'}) == []
    evidence = {'20260817T172901Z-persistent-context-workspace-restart'}
    assert permanent_temporary_references(text, {}, known_cycles=evidence) == references[2:]
    actual = {references[2]: {"path": "." + references[2], "text": "Opaque retained resource"}}
    assert permanent_temporary_references(text, actual) == [references[2]]
    for path in ['.tmp/future/materials/Evidence.md',
                 'modules/workspace/workspace_contexts/alpha/tmp/legacy/Evidence.md',
                 '.pcw/alpha/tmp/legacy/Evidence.md']:
        assert permanent_temporary_references(record_text({'id': 'contract', 'type': 'knowledge', 'sources': [path]}, '# Contract\n'), {})


def test_external_same_tail_is_not_an_actual_temporary_inventory_path(tmp_path):
    from workspace.temporary_migration import permanent_temporary_references
    runtime = fixture_runtime(tmp_path)
    path = tmp_path / 'tmp/cycle/materials/Evidence.md'
    path.parent.mkdir(parents=True)
    path.write_text('# Evidence\n', encoding='utf-8', newline='')
    transforms = temporary_transforms(runtime)
    external = '/unrelated/project/tmp/cycle/materials/Evidence.md'
    assert permanent_temporary_references('# Permanent\n[source](' + external + ')', transforms) == []
    assert permanent_temporary_references('# Permanent\n[source](' + str(path) + ')', transforms)
    note = runtime.engine.knowledge / 'Fact.md'
    assert permanent_temporary_references('# Permanent\n[source](../tmp/cycle/materials/Evidence.md)', transforms, source_path=note)


def test_dependency_scan_reuses_temporary_yaml_inventory(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    import workspace.temporary_migration as migration
    root = tmp_path / 'context'
    knowledge = root / 'knowledge'
    knowledge.mkdir(parents=True)
    for index in range(12):
        (knowledge / f'{index}.md').write_text('---\nid: permanent-' + str(index) + '\nlinks: ["temporary-id"]\n---\nVerified permanent fact.\n')
    transforms = {'tmp/cycle/materials/old.md': {'path': '.tmp/cycle/materials/Friendly.md',
        'text': '---\nid: temporary-id\ntype: knowledge\nlinks: []\n---\nWorking observation.\n'}}
    runtime = SimpleNamespace(engine=SimpleNamespace(root=root, _assert_safe=lambda path: None))
    original = migration._parse
    temporary_parses = []
    def observe(text):
        if text == transforms['tmp/cycle/materials/old.md']['text']:
            temporary_parses.append(text)
        return original(text)
    with patch.object(migration, '_parse', observe):
        blockers = migration.permanent_temporary_blockers(runtime, transforms)
    assert len(blockers) == 12 and all(item['target'] == 'temporary-id' for item in blockers)
    assert len(temporary_parses) == 1
