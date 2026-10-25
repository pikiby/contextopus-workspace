"""Real isolated roots verify canonical source, shared rules and migration."""
import json
from contextlib import closing
from pathlib import Path
import pytest
from pcw_runtime.cycles import CycleRuntime
from test_runtime import Factory
from test_workspace_v3 import create
from workspace.runtime import PCWError, _parse
from workspace.records import record_text
from workspace.context_document import library


def runtime(tmp_path):
    result = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    create(result, cleanup_approval_required=False)
    return closing(result)


def downgrade(r):
    """Represent real format5 sources without changing their active YAML identity."""
    path = r.engine.root / "Context.md"
    (r.engine.root / "INSTRUCTIONS.md").write_text(r.instructions_read()["content"])
    metadata, body = _parse(path.read_text())
    metadata.pop("instructions")
    old = r.engine.knowledge / "Descriptive-project-name.md"
    old.write_text(record_text(metadata, body))
    path.unlink()
    r.control.write(r._format_path(), json.dumps({"format_version": 5}))
    (library(r).root / "workspace.project.md").unlink()
    return metadata["id"], old


def test_context_root_index_update_history_and_full_required_rules(tmp_path):
    with runtime(tmp_path) as r:
        project = r.project_read()
        assert project["path"] == "Context.md" and project["type"] == "project"
        assert not list(r.engine.knowledge.glob("*Project*"))
        assert any(m.path == "Context.md" for m in r.engine.materials.values())
        rules = r.instructions_read()
        store = library(r)
        store.save("agents.md", {"id": "agent-rules", "type": "instruction"}, "Full agent rules.\n")
        path = r.engine.root / "Context.md"
        metadata, body = _parse(path.read_text())
        metadata["instructions"]["agents"] = "[[agents]]"
        path.write_text(record_text(metadata, body))
        opened = r.startup("work")
        assert [d["content"] for d in opened["instructions"]["documents"]][-1] == "Full agent rules.\n"
        cycle = opened["cycle_id"]
        project = r.project_read()
        reservation = r.reserve(cycle, [project["id"]], "Update purpose")
        p = r.proposal_prepare(cycle, [{"material_id": project["id"], "expected_revision": project["revision"],
            "content": "# Revised purpose\n", "description": "Revised context scope"}])
        assert r.proposal_apply(cycle, p["proposal_id"], p["revision"])["status"] == "applied"
        assert r.project_read()["path"] == "Context.md"
        assert r.history_read(material_id=project["id"])["materials"][0]["text"]
        assert "Full agent rules." in r._permanent_files()[".contextopus-instructions/agents.md"]
        assert r.instructions_read()["content"] == rules["content"]


def test_explicit_upgrade_preserves_id_body_rules_unknown_files_and_retries(tmp_path):
    with runtime(tmp_path) as r:
        expected_rules = r.instructions_read()["content"]
        ident, old = downgrade(r)
        unknown = r.engine.root / "user.bin"
        unknown.write_bytes(b"\x00user\xff")
        before = old.read_bytes()
        with pytest.raises(PCWError, match="pcw-upgrade"):
            r.startup("no-migration")
        assert old.read_bytes() == before and not (r.engine.root / "Context.md").exists()
        plan = r.upgrade_plan()
        assert plan["ready"], plan["blockers"]
        assert any(row["after"] == "Context.md" for row in plan["renames"])
        result = r.upgrade_apply(plan["revision"], "Explicitly migrate this isolated context")
        assert result["status"] == "complete"
        project = r.project_read()
        assert project["id"] == ident and project["path"] == "Context.md"
        assert project["content"] == _parse(before.decode())[1]
        assert r.instructions_read()["content"] == expected_rules
        assert not old.exists() and unknown.read_bytes() == b"\x00user\xff"
        assert (Path(result["backup"]) / "knowledge" / old.name).read_bytes() == before
        assert r.upgrade_apply(plan["revision"], "Retry")["replayed"]


def test_occupied_context_and_conflicting_shared_rules_block_before_write(tmp_path):
    with runtime(tmp_path) as r:
        _, old = downgrade(r)
        store = library(r)
        store.save("workspace.project.md", {"id": "workspace.project", "type": "instruction"}, "Different user rules")
        plan = r.upgrade_plan()
        assert not plan["ready"] and old.exists()
        assert any("different" in b["reason"] for b in plan["blockers"])


def test_dependency_change_invalidates_knowledge_review(tmp_path):
    with runtime(tmp_path) as r:
        store = library(r)
        store.save("agents.md", {"id": "agent-rules", "type": "instruction"}, "First agent rules")
        path = r.engine.root / "Context.md"
        metadata, body = _parse(path.read_text())
        metadata["instructions"]["agents"] = "[[agents]]"
        path.write_text(record_text(metadata, body))
        cycle = r.startup("work")["cycle_id"]
        proposal = r.proposal_prepare(cycle, [{"key": "fact", "content": "Verified fact", "description": "A verified fact"}])
        doc = store.read("agents")
        store.save(doc["path"], doc["metadata"], "New agent rules", doc["revision"])
        with pytest.raises(PCWError, match="Instructions changed"):
            r.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])


def test_instruction_role_mapping_uses_normal_review_and_independent_approval(tmp_path):
    with runtime(tmp_path) as r:
        store = library(r)
        store.save("agents.md", {"id": "agent-rules", "type": "instruction"}, "Complete agent operation rules")
        opened = r.startup("bind", user_request="Bind the Agents rules to this context")
        cycle = opened["cycle_id"]
        project = r.project_read()
        r.reserve(cycle, [project["id"]], "Bind the requested Agents instruction")
        metadata = _parse((r.engine.root / "Context.md").read_text())[0]
        refs = {**metadata["instructions"], "agents": "[[agents]]"}
        proposal = r.proposal_prepare(cycle, [{"material_id": project["id"],
            "expected_revision": project["revision"], "instructions": refs}])
        assert proposal["approval_required"] is True
        assert proposal["review_changes"][0]["metadata_changes"]["instructions"]["after"] == refs
        with pytest.raises(PCWError, match="confirmation"):
            r.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
        assert r.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"], "Apply this exact binding")["status"] == "applied"
        assert any(doc["id"] == "agent-rules" for doc in r.startup("next")["instructions"]["documents"])


def test_native_wiki_markdown_and_source_wrappers_follow_project_move(tmp_path):
    from workspace.links import LinkCodec, retarget_references
    from workspace.runtime import Material
    old = {"project-id": Material("project-id", "knowledge/Friendly.md", {"links": []}, "Project"),
           "source-id": Material("source-id", "knowledge/Source.md", {"links": []}, "Source")}
    new = {**old, "project-id": Material("project-id", "Context.md", {"links": []}, "Project")}
    owners = {ident: "project" for ident in old}
    before = LinkCodec(tmp_path, old, owners)
    after = LinkCodec(tmp_path, new, owners)
    body = '[[Friendly|Project label]] [scope](Friendly.md#scope)\n```yaml\nsources:\n  - reference: "[[Friendly]]"\n    instruction: "Read complete scope"\n```\n'
    changed = retarget_references(body, "source-id", before, after)
    assert "Friendly" not in changed
    assert "../Context.md#scope" in changed and "|Project label]]" in changed
    assert 'instruction: "Read complete scope"' in changed


def test_historical_proposal_codec_uses_exact_receipt_identity_only(tmp_path):
    from workspace.migration_identity import MigrationIdentity
    from workspace.records import record_text
    with runtime(tmp_path) as r:
        item = r.engine.create('# New target\nCurrent verified facts.', description='Current target')
        historical = 'knowledge/Old-target.md'
        r.control.write(r.control.root / 'migration-v5.json', json.dumps({'status': 'complete',
            'identity_map': [], 'transforms': {historical: {'id': item['id'], 'path': historical}}}))
        identity = MigrationIdentity(r)
        value = '[[' + (r.engine.root / historical).relative_to(tmp_path).as_posix() + '|Original label]]'
        strict = identity.codec()
        own = r.project_read()['id']
        with pytest.raises(PCWError, match='Unresolved'):
            strict.decode([value], own)
        historical_codec = identity.codec().enable_historical_paths()
        assert historical_codec.decode([value], own) == [item['id']]
        assert item['path'] in historical_codec.encode([item['id']])[0]
        with pytest.raises(PCWError, match='Unresolved'):
            identity.codec().decode([value], own)
        assert (r.engine.root / item['path']).is_file()


def test_instruction_payload_contains_each_body_once(tmp_path):
    with runtime(tmp_path) as r:
        rules = r.instructions_read()
        assert rules['content']
        assert 'content' not in rules['primary']
        assert all(document['id'] != rules['primary']['id'] for document in rules['documents'])
        assert rules['documents'] == []
