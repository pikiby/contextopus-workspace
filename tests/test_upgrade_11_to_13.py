"""Installed 1.1/1.2 writers create fixtures; current source migrates their copies.

Set CONTEXTOPUS_V11_PYTHON and/or CONTEXTOPUS_V12_PYTHON to isolated installed
interpreters. CONTEXTOPUS_OLD_PYTHON with CONTEXTOPUS_OLD_VERSION is an alternative.
This test never reads or writes the user's Contextopus root or downloads a model.
"""

import asyncio
import atexit
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from contextopus.core.resources import ProcessResources
from contextopus.interfaces.mcp import server
from workspace.analytics import EventStore
from workspace.cycles import CycleRuntime
from workspace.runtime import _parse
from workspace.workspaces import FORMAT_VERSION
from test_runtime import Factory


OLD_WRITER = r'''
import base64, hashlib, importlib.metadata, json, math, pathlib, subprocess, sys, tempfile
from contextopus.modules.workspace.cycles import CycleRuntime
from contextopus.modules.workspace import cycles
from contextopus.modules.workspace.server import dispatch, close_runtimes
from contextopus.modules.workspace.analytics import EventStore

expected_version = sys.argv[3]
assert importlib.metadata.version("contextopus-core") == expected_version
assert "site-packages" in cycles.__file__, cycles.__file__
distribution = importlib.metadata.distribution("contextopus-core")
direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
assert not direct_url.get("dir_info", {}).get("editable", False), direct_url
root, manifest_path = map(pathlib.Path, sys.argv[1:3])
assert root.resolve().is_relative_to(pathlib.Path(tempfile.gettempdir()).resolve())
(root / "modules/workspace/workspace_contexts").mkdir(parents=True)

class Tokenizer:
    model_max_length = 32768
    def __call__(self, text, add_special_tokens=False, **kwargs):
        if isinstance(text, list):
            return {"input_ids": [list(range(len(value))) for value in text]}
        return {"input_ids": list(range(len(text))) + ([999] if add_special_tokens else []),
                "offset_mapping": [(i, i + 1) for i in range(len(text))]}

class Provider:
    def __init__(self, config): self.config = config
    def tokenizer(self): return Tokenizer()
    def embed(self, texts, purpose="document"):
        result = []
        for text in texts:
            angle = (sum((i + 1) * ord(c) for i, c in enumerate(text)) % 360) * math.pi / 180
            result.append([math.cos(angle), math.sin(angle)] + [0.0] * (self.config["profile"]["dimensions"] - 2))
        return result
    def close(self): pass

settings = {"allow_implicit_updates": False, "knowledge_approval_required": True,
            "instructions_approval_required": False, "git_history_enabled": True,
            "material_max_tokens": 4096, "create_required_sources": True}
custom = "custom: 'Сохранить авторские условия' # untouched user metadata\r\n"
body = "# Авторские правила\r\nPreserve evidence limits and task scope.\r\nDelegated workers report completion and unresolved causes.\r\nRead every declared source afresh on every receipt.\r\n"
flags = "".join(key + ": " + str(value).lower() + "\r\n" for key, value in settings.items())
rules = "---\r\n" + custom + flags + "---\r\n" + body
expected_rules = "---\r\n" + custom + "---\r\n" + body

alpha = CycleRuntime(root, "alpha", provider_factory=Provider)
beta = CycleRuntime(root, "beta", provider_factory=Provider)
for runtime in (alpha, beta):
    created = runtime.workspace_create("create-" + runtime.engine.workspace, rules,
        "---\ndescription: Explains the fixture project purpose and verification boundaries.\n---\n# Project\nPreserve upgrade evidence and author decisions.\n",
        "Create this isolated compatibility fixture")
    assert created["status"] == "created", created

cycle = alpha.startup("seed-alpha")["cycle_id"]
guide_body = "# Повторяемый заголовок\nVerified retention conditions apply to the archive route only.\n```yaml\nsources:\n- reference: fixture:retention-query\n  instruction: Execute the bounded fixture query and verify retention equals seven days.\n```\n"
seed = alpha.proposal_prepare(cycle, [
    {"key": "guide", "type": "guide", "content": guide_body,
     "description": "Explains verified archive retention conditions and their mandatory fresh source check.", "links": ["@task"]},
    {"key": "task", "type": "task", "status": "blocked", "content": "# Archive task\nRetention is verified; the remaining blocker is consumer ownership.\n",
     "description": "Records the verified archive state, outstanding ownership blocker and next verification step.", "links": ["@guide"]},
    {"key": "gap", "type": "knowledge", "content": "# Legacy description gap\nA verified old fact without an authored description.\n"},
], summary="Create isolated compatibility knowledge")
assert alpha.proposal_apply(cycle, seed["proposal_id"], seed["revision"], "Approve this exact fixture seed")["status"] == "applied"
assert alpha.finish(cycle)["status"] == "closed"
ids = dict(seed["created_ids"])

cycle = beta.startup("seed-beta")["cycle_id"]
decision = beta.proposal_prepare(cycle, [{"key": "decision", "type": "decision", "status": "accepted",
    "content": "# Повторяемый заголовок\nUse the verified alpha retention conditions for beta archives.\n",
    "description": "Records the accepted beta archive decision and the alpha retention conditions it depends on.", "links": [ids["guide"]]}])
assert beta.proposal_apply(cycle, decision["proposal_id"], decision["revision"], "Approve this exact fixture decision")["status"] == "applied"
assert beta.finish(cycle)["status"] == "closed"
ids.update(decision["created_ids"])

cycle = alpha.startup("bidirectional-links")["cycle_id"]
guide = alpha.read(material_id=ids["guide"])
linked = alpha.proposal_prepare(cycle, [{"material_id": guide["id"], "expected_revision": guide["revision"],
    "links": [ids["task"], ids["decision"]]}])
assert alpha.proposal_apply(cycle, linked["proposal_id"], linked["revision"], "Approve reciprocal fixture navigation")["status"] == "applied"
assert alpha.finish(cycle)["status"] == "closed"

pending_cycle = alpha.startup("unfinished-work")["cycle_id"]
temporary = alpha.tmp_write(pending_cycle, "# Незавершённый результат\r\nKeep exact bytes and revision; user review is pending.\r\n")
task = alpha.read(material_id=ids["task"])
pending = alpha.proposal_prepare(pending_cycle, [
    {"material_id": task["id"], "expected_revision": task["revision"], "status": "completed",
     "content": "# Archive task\nRetention and consumer ownership are verified; the fixture task is complete.\n",
     "description": "Records verified retention and ownership after the pending review is approved."},
    {"key": "new", "type": "procedure", "content": "# Pending route\nUse the verified retention and ownership checks together.\n",
     "description": "Explains when both verified archive conditions must be applied together.", "links": [ids["guide"], ids["task"]]},
], summary="Complete the isolated archive verification after approval")
before = {ident: alpha.read(material_id=ident) for ident in ids.values()}
pending_files = [alpha._plan_path(pending_cycle, pending["proposal_id"]),
                 alpha._plan_path(pending_cycle, pending["proposal_id"]).with_suffix(".md"),
                 alpha.engine.root / temporary["path"]]
pending_bytes = {str(p.relative_to(root)): base64.b64encode(p.read_bytes()).decode() for p in pending_files}
format_version = alpha._format()
search_metadata = None
if expected_version == "1.2.0":
    from contextopus.core.settings import Settings
    from contextopus.core.filtering import AlgorithmCatalog, registry_for_layout
    from contextopus.core.resources import ProcessResources
    from contextopus.core.rag import RagIndex
    configuration = Settings(alpha.control.layout)
    layers = [
        ({"include": ["knowledge/**"], "exclude": [".git/**", "knowledge/Hidden-*.md"],
          "searchN": 11, "distance": 0.61,
          "selection": {"algorithm": "nearest_n", "parameters": {"N": 5}}}, {}),
        ({"include_archived": True, "searchN": 13,
          "selection": {"parameters": {"N": 6}}}, {"module": "workspace"}),
        ({"searchN": 17, "distance": 0.73, "selection": {"parameters": {"N": 3}}},
         {"module": "workspace", "context": "alpha"}),
        ({"searchN": 19, "selection": {"parameters": {"N": 4}}},
         {"module": "workspace", "context": "beta"}),
    ]
    for changes, scope in layers:
        preview = configuration.preview(changes, **scope)
        configuration.apply(preview["preview_revision"])
    fields = ["include", "exclude", "include_archived", "object_type", "searchN", "distance", "selection"]
    origins = fields + ["selection.algorithm", "selection.parameters.N"]
    effective = {}
    for name in ("alpha", "beta"):
        snapshot = configuration.snapshot(module="workspace", context=name)
        effective[name] = {"values": {key: snapshot["values"][key] for key in fields},
                           "sources": {key: snapshot["sources"][key] for key in origins}}
    saved_files = {str(configuration._scope_path(**scope).relative_to(root)):
                   base64.b64encode(configuration._scope_path(**scope).read_bytes()).decode()
                   for _, scope in layers}
    resources = ProcessResources(Provider)
    index = RagIndex(alpha.control.layout, resources)
    registry = registry_for_layout(alpha.control.layout)
    catalog = AlgorithmCatalog(registry, index)
    refreshed = catalog.refresh()
    catalog_rows = index.read_rows(index.table,
        ["object_id", "part_index", "revision", "metadata", "text"],
        "module='core' AND collection='search_algorithm'")
    catalog_rows.sort(key=lambda row: (row["object_id"], row["part_index"]))
    search_metadata = {"effective": effective, "saved_files": saved_files,
                       "catalog_descriptions": registry.catalog(),
                       "catalog_revisions": index.vector_revisions("core", "search_algorithm"),
                       "catalog_rows": catalog_rows, "catalog_refresh": refreshed}
    index.table = index.db = None
    resources.close()
commits = {r.engine.workspace: subprocess.check_output(["git", "-C", str(r.engine.root), "rev-list", "--all"], text=True).splitlines()
           for r in (alpha, beta)}
alpha.close(); beta.close()
observed = dispatch(str(root), "alpha", "read", {"material_id": ids["guide"]}, provider_factory=Provider)
assert observed["analytics"]["status"] == "recorded", observed
old_event = observed["analytics"]["event_id"]
close_runtimes()
manifest = dict(writer_version=importlib.metadata.version("contextopus-core"), writer_module=cycles.__file__,
    format_version=format_version, settings=settings, rules=rules, expected_rules=expected_rules,
    ids=ids, records=before, pending_cycle=pending_cycle, temporary=temporary,
    pending=pending, pending_bytes=pending_bytes, git_commits=commits, old_event=old_event,
    search_metadata=search_metadata, writer_direct_url=direct_url)
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
print(json.dumps({"writer_version": manifest["writer_version"], "format_version": format_version, "records": len(before), "pending_revision": pending["revision"]}))
'''


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def result_value(result):
    if isinstance(result, tuple):
        return result[1]
    if isinstance(result, dict):
        return result
    return json.loads(''.join(item.text for item in result if hasattr(item, 'text')))


@pytest.mark.parametrize(('old_version', 'python_variable'), [
    ('1.1.0', 'CONTEXTOPUS_V11_PYTHON'), ('1.2.0', 'CONTEXTOPUS_V12_PYTHON')], ids=['1.1', '1.2'])
def test_installed_writer_to_current_runtime_and_mcp_copy(tmp_path, monkeypatch, old_version, python_variable):
    configured_python = os.environ.get(python_variable)
    if os.environ.get('CONTEXTOPUS_OLD_VERSION', '1.1.0') == old_version:
        configured_python = os.environ.get('CONTEXTOPUS_OLD_PYTHON') or configured_python
    if not configured_python:
        pytest.skip(f'Set {python_variable} to an isolated installed Contextopus {old_version} Python')
    old_python = Path(configured_python)
    if not old_python.is_file():
        pytest.skip(f'Set {python_variable} to an installed Contextopus {old_version} Python')
    source, migrated = tmp_path / ('source-' + old_version), tmp_path / 'migrated-current'
    writer, manifest_path = tmp_path / 'old_writer.py', tmp_path / 'writer_manifest.json'
    writer.write_text(OLD_WRITER)
    completed = subprocess.run([str(old_python), '-I', str(writer), str(source), str(manifest_path), old_version],
        capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    old = json.loads(manifest_path.read_text())
    assert old['writer_version'] == old_version and old['format_version'] == 3
    assert 'site-packages/contextopus/' in old['writer_module']
    source_before = tree_hashes(source)
    shutil.copytree(source, migrated)

    resources = ProcessResources(Factory())
    original_owner = server.ServerOwner
    owners = []
    def make_owner(root):
        owner = original_owner(root)
        owner.resources.close()
        owner.resources = resources
        owners.append(owner)
        return owner
    monkeypatch.setattr(server, 'ServerOwner', make_owner)
    from workspace_support import install_workspace
    install_workspace(migrated)
    mcp = server.create_server(migrated)
    async def call(tool, operation, arguments=None, context='alpha', detail='full'):
        return result_value(await mcp.call_tool(tool, dict(operation=operation, arguments=arguments or {},
            context=context, work_root=str(migrated), detail=detail)))
    def invoke(tool, operation, arguments=None, **kwargs):
        return asyncio.run(call(tool, operation, arguments, **kwargs))
    migrations = {}
    temporary_paths = {}
    try:
        assert {tool.name for tool in asyncio.run(mcp.list_tools())} == {'guide', 'read', 'write', 'finish'}
        guide = result_value(asyncio.run(mcp.call_tool('guide', {'procedure': 'upgrade', 'context': 'alpha'})))
        assert guide
        for name in ('alpha', 'beta'):
            plan = invoke('read', 'workspace.upgrade_plan', context=name)
            assert plan['ready'] and plan['from_version'] == 3 and plan['to_version'] == FORMAT_VERSION, plan
            applied = invoke('write', 'workspace.upgrade_apply', {
                'expected_revision': plan['revision'], 'user_request': f'Upgrade only this isolated {old_version} fixture copy'}, context=name)
            assert applied['status'] == 'complete' and applied['format_version'] == FORMAT_VERSION, applied
            migrations[name] = {'revision': plan['revision'], 'result': applied}
            workspace_prefix = Path('modules/workspace/workspace_contexts') / name
            for before, transformed in plan['temporary_transforms'].items():
                temporary_paths[(workspace_prefix / before).as_posix()] = (
                    workspace_prefix / transformed['path']).as_posix()
            policies = invoke('read', 'workspace.settings_read', {'fields': list(old['settings'])}, context=name)
            assert policies['values'] == old['settings']
            rules = invoke('read', 'workspace.instructions_read', context=name)
            assert rules['content'].encode() == old['expected_rules'].encode()
            replay = invoke('write', 'workspace.upgrade_apply', {
                'expected_revision': plan['revision'], 'user_request': 'Replay the completed isolated upgrade'}, context=name)
            assert replay['status'] == 'complete' and replay['replayed']
            assert invoke('read', 'workspace.upgrade_plan', context=name)['compatible']
        preserved_search = None
        if old.get('search_metadata'):
            from contextopus.core.settings import Settings
            from contextopus.core.storage import StorageLayout
            from contextopus.core.filtering import AlgorithmCatalog, registry_for_layout
            from contextopus.core.rag import RagIndex
            layout = StorageLayout(migrated)
            for name, before in old['search_metadata']['effective'].items():
                current = invoke('read', 'core.settings', {'module': 'workspace', 'context': name}, context=name)
                assert {key: current['values'][key] for key in before['values']} == before['values']
                assert {key: current['sources'][key] for key in before['sources']} == before['sources']
                assert current['values']['selection']['algorithm'] == 'nearest_n'
                searched = invoke('read', 'workspace.search', {'query': 'retention ownership', 'all_categories': True}, context=name)
                assert searched['diagnostics']['effective_settings'] == before['values']['selection']
                assert searched['diagnostics']['algorithm'] == 'nearest_n'
            for relative, encoded in old['search_metadata']['saved_files'].items():
                before_bytes = base64.b64decode(encoded)
                before, after = json.loads(before_bytes), json.loads((migrated / relative).read_bytes())
                assert {key: after[key] for key in before} == before
                if '/contexts/' not in relative:
                    assert (migrated / relative).read_bytes() == before_bytes
            catalog_index = RagIndex(layout, resources)
            recorded = catalog_index.vector_revisions('core', 'search_algorithm',
                object_ids=list(old['search_metadata']['catalog_revisions']))
            assert not recorded['missing'] and not recorded['conflicts']
            recorded_revisions = recorded['revisions']
            assert recorded_revisions == old['search_metadata']['catalog_revisions']
            rows = catalog_index.read_rows(catalog_index.table,
                ['object_id', 'part_index', 'revision', 'metadata', 'text'],
                "module='core' AND collection='search_algorithm'")
            rows.sort(key=lambda row: (row['object_id'], row['part_index']))
            assert rows == old['search_metadata']['catalog_rows']
            registry = registry_for_layout(layout)
            current_descriptions = registry.catalog()
            assert {item['id'] for item in old['search_metadata']['catalog_descriptions']} <= {
                item['id'] for item in current_descriptions}
            catalog_state = AlgorithmCatalog(registry, catalog_index).search('nearest candidates', semantic=True)
            assert catalog_state['catalog_state'] == 'stale'
            assert catalog_state['next_action'] == 'core.algorithms_refresh'
            preserved_search = {'effective': old['search_metadata']['effective'],
                'stored_catalog_revisions': recorded_revisions,
                'stored_catalog_versions': {item['id']: item['version'] for item in old['search_metadata']['catalog_descriptions']},
                'runtime_catalog_versions': {item['id']: item['version'] for item in current_descriptions},
                'catalog_state': catalog_state['catalog_state'], 'catalog_next_action': catalog_state['next_action'],
                'catalog_rows_preserved': len(rows), 'selected_algorithm': 'nearest_n'}
            catalog_index.table = catalog_index.db = None
        pending_after_migration = {}
        for relative, data in old['pending_bytes'].items():
            original = base64.b64decode(data)
            destination = temporary_paths[relative]
            assert '/.tmp/' in destination and not (migrated / relative).exists()
            actual = (migrated / destination).read_bytes()
            legacy_relative = Path(relative).relative_to('modules/workspace/workspace_contexts/alpha')
            assert (Path(migrations['alpha']['result']['backup']) / legacy_relative).read_bytes() == original
            if Path(relative).parent.name == 'proposals':
                # Only location changes for immutable proposal/review evidence.
                assert actual == original
            else:
                # Format 5 adds readable YAML identity without changing the
                # authored temporary body, including its original CRLF bytes.
                metadata, body = _parse(actual.decode('utf-8'))
                assert metadata == {'id': old['temporary']['id'], 'type': 'knowledge', 'links': []}
                assert body.encode('utf-8') == original
                assert Path(destination).name == 'Незавершённый результат.md'
            pending_after_migration[destination] = actual
        for ident, before in old['records'].items():
            read = invoke('read', 'workspace.read', {'material_id': ident})
            for key in ('id', 'type', 'content', 'links', 'description'):
                assert read.get(key) == before.get(key), (key, before, read)
            if before.get('status'):
                assert read['status'] == before['status']
            assert read.get('sources') == before.get('sources')
            physical = migrated / 'modules/workspace/workspace_contexts' / read['workspace'] / read['path']
            metadata, _ = _parse(physical.read_bytes().decode())
            original_physical = source / 'modules/workspace/workspace_contexts' / before['workspace'] / before['path']
            original_metadata, original_body = _parse(original_physical.read_bytes().decode('utf-8'))
            # Authoring revisions include metadata. Format 5 deliberately adds
            # the owner tag; decoded links and all authored data stay unchanged.
            expected_metadata = {**original_metadata, 'tags': ['context/' + read['workspace']]}
            assert {**metadata, 'links': read['links']} == expected_metadata
            assert before['content'] == original_body
            expected_revision = hashlib.sha256((json.dumps(expected_metadata, sort_keys=True,
                ensure_ascii=False) + '\n' + original_body).encode('utf-8')).hexdigest()
            assert read['revision'] == expected_revision
            for link in metadata.get('links', []):
                assert link.startswith('[[') and (migrated / link[2:-2].split('|', 1)[0]).is_file()
        search = invoke('read', 'workspace.search', {'query': 'retention ownership', 'all_categories': True,
            'selection': {'algorithm': 'none', 'parameters': {'page_size': 100}}}, detail='compact')
        assert set(old['ids'].values()) <= {row['id'] for row in search['results']}
        assert all('excerpt' not in row for row in search['results'])
        audit = invoke('read', 'workspace.audit')
        assert any(row['material_id'] == old['ids']['gap'] and any(issue['problem'] == 'missing_description'
            for issue in row['issues']) for row in audit['materials'])
        pending_args = {'cycle_id': old['pending_cycle'], 'proposal_id': old['pending']['proposal_id']}
        reviewed = invoke('read', 'workspace.proposal_read', pending_args)
        assert reviewed['revision'] == old['pending']['revision'] and reviewed['approval_required']
        temporary = invoke('read', 'workspace.tmp_read', {'cycle_id': old['pending_cycle'], 'material_id': old['temporary']['id']})
        old_temporary_path = next(path for path in old['pending_bytes'] if Path(path).parent.name == 'materials')
        current_temporary = pending_after_migration[temporary_paths[old_temporary_path]]
        assert temporary['id'] == old['temporary']['id'] and temporary['temporary'] is True
        assert temporary['revision'] == hashlib.sha256(current_temporary).hexdigest()
        assert temporary['revision'] != old['temporary']['revision']
        assert temporary['content'].encode('utf-8') == base64.b64decode(old['pending_bytes'][old_temporary_path])
        apply_args = {**pending_args, 'expected_revision': old['pending']['revision']}
        permanent = migrated / 'modules/workspace/workspace_contexts/alpha/knowledge'
        before_denied = tree_hashes(permanent)
        with pytest.raises(Exception, match='confirmation'):
            invoke('write', 'workspace.proposal_apply', apply_args)
        assert tree_hashes(permanent) == before_denied
        applied_pending = invoke('write', 'workspace.proposal_apply', {
            **apply_args, 'confirmation': f'Approve only the exact proposal authored by installed {old_version}'})
        assert applied_pending['status'] == 'applied', applied_pending
        task = invoke('read', 'workspace.read', {'material_id': old['ids']['task']})
        assert task['status'] == 'completed' and 'consumer ownership are verified' in task['content']
        created = invoke('read', 'workspace.read', {'material_id': old['pending']['created_ids']['new']})
        assert created['links'] == [old['ids']['guide'], old['ids']['task']]
        # Original proposal evidence and migrated temporary cards survive apply.
        for relative, content in pending_after_migration.items():
            assert (migrated / relative).read_bytes() == content
        for name, commits in old['git_commits'].items():
            workspace = migrated / 'modules/workspace/workspace_contexts' / name
            after = subprocess.check_output(['git', '-C', str(workspace), 'rev-list', '--all'], text=True).splitlines()
            assert set(commits) <= set(after)
        with EventStore(migrated).connect() as db:
            assert db.execute('SELECT id FROM events WHERE id=?', (old['old_event'],)).fetchone()
        assert tree_hashes(source) == source_before
        evidence = {'old_writer': {'version': old['writer_version'], 'module': old['writer_module'], 'format': old['format_version']},
            'new_reader_module': str(Path(__import__('workspace.cycles', fromlist=['x']).__file__).resolve()),
            'source_version': tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())['project']['version'],
            'source_root_unchanged': True, 'migrations': migrations, 'graph': {ident: data['links'] for ident, data in old['records'].items()},
            'pending_revision_preserved': old['pending']['revision'],
            'pending_bytes_preserved_in_backup': list(old['pending_bytes']),
            'proposal_bytes_preserved': [path for path in old['pending_bytes'] if Path(path).parent.name == 'proposals'],
            'temporary_body_bytes_preserved': True,
            'temporary_paths': temporary_paths, 'temporary_revision': temporary['revision'],
            'settings_preserved': old['settings'], 'old_git_commits_preserved': old['git_commits'],
            'old_analytics_event_preserved': old['old_event'], 'mcp_tools': ['guide', 'read', 'write', 'finish'],
            'pending_apply': applied_pending['status'], 'network_embedding_calls': 0}
        evidence['search_metadata_preserved'] = preserved_search
        (tmp_path / 'upgrade-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        evidence_directory = os.environ.get('CONTEXTOPUS_UPGRADE_EVIDENCE_DIR')
        if evidence_directory:
            destination = Path(evidence_directory)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp_path / 'upgrade-evidence.json', destination / ('upgrade-' + old_version + '-evidence.json'))
            shutil.copy2(manifest_path, destination / ('writer-' + old_version + '-manifest.json'))
    finally:
        for owner in owners:
            owner.close()
            atexit.unregister(owner.close)
