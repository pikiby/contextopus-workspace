"""Complete reviews survive pagination, source edits and process restarts."""

from datetime import timedelta
import json

import pytest

from workspace.cycles import CycleRuntime
from workspace.records import record_text
from workspace.review_scopes import ReviewScopes
from workspace.runtime import PCWError
from test_runtime import Factory
from test_workspace_v3 import create


@pytest.fixture
def runtime(tmp_path):
    result = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    create(result)
    yield result
    result.close()


def sources(runtime, count):
    result = []
    for i in range(count):
        ident = f"item-{i:03}.md"
        path = runtime.engine.knowledge / f"Item-{i:03}.md"
        path.write_text(record_text({"id": ident, "type": "knowledge", "links": [],
                                     "description": f"Description {i}"}, f"# Item {i}\n\nFact {i}.\n"), encoding="utf-8")
        result.append((ident, path))
    return result


def test_complete_101_record_inventory_counts_unique_deliveries(runtime):
    sources(runtime, 100)
    first = runtime.audit(scope="all", request_id="review-101", limit=40)
    assert first["total"] == 101
    assert first["catalog_entries_returned"] == 40
    assert first["materials"][0]["path"] == "Context.md"
    assert first["bodies_remaining"] == 101
    assert first["required_instructions"]["documents"]
    assert not runtime.record_path.exists()
    assert not (runtime.engine.root / ".tmp").exists()
    replay = runtime.audit(scope="all", request_id="review-101", limit=40)
    assert replay["scope_id"] == first["scope_id"]
    assert replay["materials"] == first["materials"]
    assert replay["catalog_entries_returned"] == 40
    second = runtime.audit(cursor=first["next_cursor"], limit=40)
    again = runtime.audit(cursor=first["next_cursor"], limit=40)
    assert second["materials"] == again["materials"]
    assert again["catalog_entries_returned"] == 80
    third = runtime.audit(cursor=second["next_cursor"], limit=40)
    assert third["next_cursor"] is None
    assert third["catalog_entries_returned"] == 101
    assert third["catalog_remaining"] == 0
    assert third["full_bodies_returned"] == 0
    all_rows = first["materials"] + second["materials"] + third["materials"]
    assert len({row["path"] for row in all_rows}) == 101
    reader = ReviewScopes(runtime)
    read = runtime.read_many(material_ids=[row["id"] for row in all_rows[:100]], scope_id=first["scope_id"])
    assert read["full_bodies_returned"] == 100
    assert all(row["snapshot_match"] for row in read["materials"])
    read = runtime.read_many(material_ids=[all_rows[-1]["id"]] * 2, scope_id=first["scope_id"])
    assert read["full_bodies_returned"] == 101 and read["bodies_remaining"] == 0
    assert len(read["materials"]) == 1
    persisted = json.loads(reader._path(first["scope_id"]).read_text())
    assert "content" not in json.dumps(persisted)
    assert "Fact 99." not in json.dumps(persisted)


def test_inventory_and_pages_survive_restart(runtime, tmp_path):
    sources(runtime, 5)
    first = runtime.audit(scope="all", request_id="restart", limit=2)
    expected = runtime.audit(cursor=first["next_cursor"], limit=2)
    runtime.close()
    restarted = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    try:
        actual = restarted.audit(cursor=first["next_cursor"], limit=2)
        assert actual["materials"] == expected["materials"]
        assert actual["manifest_revision"] == first["manifest_revision"]
        assert actual["catalog_entries_returned"] == 4
    finally:
        restarted.close()


def test_uninstalled_sibling_does_not_poison_selected_inventory(runtime):
    ident, original = sources(runtime, 1)[0]
    foreign = runtime.engine.root.parent / "abandoned" / "knowledge"
    foreign.mkdir(parents=True)
    (foreign / "Copy.md").write_bytes(original.read_bytes())
    assert runtime.read(material_id=ident)["id"] == ident
    inventory = runtime.audit(scope="all", request_id="ignore-uninstalled")
    row = next(row for row in inventory["materials"] if row["id"] == ident)
    assert not any(issue["problem"] == "duplicate_id" for issue in row["issues"])
    read = runtime.read_many(material_ids=[ident], scope_id=inventory["scope_id"])
    assert read["materials"][0]["snapshot_match"] is True
    assert read["full_bodies_returned"] == 1


def test_changed_missing_added_sources_never_rewrite_original_snapshot(runtime):
    items = sources(runtime, 3)
    inventory = runtime.audit(scope="all", request_id="changes", limit=1)
    items[0][1].write_text(items[0][1].read_text().replace("Fact 0.", "Updated fact."), encoding="utf-8")
    items[1][1].unlink()
    added = runtime.engine.knowledge / "New.md"
    added.write_text(record_text({"id": "new.md", "type": "knowledge", "links": [],
                                  "description": "New record"}, "# New\nNew fact."), encoding="utf-8")
    read = runtime.read_many(material_ids=[items[0][0], items[1][0], items[2][0], "new.md"], scope_id=inventory["scope_id"])
    assert read["total"] == 4 and read["full_bodies_returned"] == 1
    changed, deleted, unchanged, outside = read["materials"]
    assert not changed["snapshot_match"] and changed["snapshot_revision"] != changed["revision"]
    assert changed["current_revision"] == changed["revision"]
    assert changed["content"].endswith("Updated fact.\n")
    assert deleted["status"] == "missing" and not deleted["snapshot_match"]
    assert unchanged["snapshot_match"]
    assert outside["status"] == "outside_scope"
    remainder = runtime.audit(cursor=inventory["next_cursor"], limit=100)
    assert {row["id"] for row in remainder["materials"]} == {item[0] for item in items}
    assert remainder["manifest_revision"] == inventory["manifest_revision"]


def test_delta_pages_are_frozen_and_refresh_cannot_replace_in_progress(runtime):
    items = sources(runtime, 3)
    first = runtime.audit(scope="all", request_id="deltas")
    items[0][1].write_text(items[0][1].read_text().replace("Fact 0.", "Changed."), encoding="utf-8")
    items[1][1].unlink()
    first_delta = runtime.audit(scope_id=first["scope_id"], delta_refresh=True, limit=1)
    assert first_delta["total"] == 4 and first_delta["delta_total"] == 2
    assert first_delta["changes"][0]["change"] == "changed"
    items[2][1].unlink()
    blocked = runtime.audit(scope_id=first["scope_id"], delta_refresh=True, limit=1)
    assert blocked["status"] == "delta_in_progress"
    assert blocked["delta_revision"] == first_delta["delta_revision"]
    second = runtime.audit(delta_cursor=first_delta["delta_cursor"], limit=1)
    assert second["delta_revision"] == first_delta["delta_revision"]
    assert second["delta_total"] == 2 and second["delta_cursor"] is None
    assert second["changes"][0]["id"] == items[1][0]
    refreshed = runtime.audit(scope_id=first["scope_id"], delta_refresh=True, limit=100)
    assert refreshed["delta_total"] == 3
    assert refreshed["delta_revision"] != first_delta["delta_revision"]
    replay = runtime.audit(delta_cursor=first_delta["delta_cursor"], limit=1)
    assert replay["changes"] == second["changes"]
    assert replay["delta_revision"] == first_delta["delta_revision"]


def test_expiry_requires_fresh_request_without_changing_sources(runtime, monkeypatch):
    from workspace import review_scopes
    sources(runtime, 2)
    now = review_scopes._now()
    monkeypatch.setattr(review_scopes, "_now", lambda: now)
    first = runtime.audit(scope="all", request_id="old", limit=1)
    files = {str(path): path.read_bytes() for path in runtime.engine.root.rglob("*.md")}
    now += timedelta(days=20)
    active = runtime.audit(cursor=first["next_cursor"], limit=1)
    assert active["expires_at"] > first["expires_at"]
    now += timedelta(days=31)
    expired = runtime.audit(cursor=active["next_cursor"], limit=1)
    assert expired["status"] == "scope_expired"
    assert expired["start_contract"]["arguments"]["request_id"] == "<fresh request ID>"
    assert runtime.audit(scope="all", request_id="old")["status"] == "scope_expired"
    new = runtime.audit(scope="all", request_id="new")
    assert new["scope_id"] != first["scope_id"]
    assert files == {str(path): path.read_bytes() for path in runtime.engine.root.rglob("*.md")}
    assert not runtime.record_path.exists()


def test_invalid_unregistered_unreadable_and_duplicate_sources_are_explicit(runtime, monkeypatch):
    items = sources(runtime, 1)
    knowledge = runtime.engine.knowledge
    (knowledge / "Unregistered.md").write_text("# No identity\n", encoding="utf-8")
    (knowledge / "Invalid.md").write_text("---\nid: [\n---\nBroken", encoding="utf-8")
    (knowledge / "Bytes.md").write_bytes(b"\xff")
    (knowledge / "Duplicate.md").write_bytes(items[0][1].read_bytes())
    unreadable = knowledge / "Unreadable.md"
    unreadable.write_text("# Inaccessible", encoding="utf-8")
    from pathlib import Path
    original_read = Path.read_bytes
    def deny_one(path):
        if path == unreadable:
            raise PermissionError("Synthetic read denial")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", deny_one)
    result = runtime.audit(scope="all", request_id="diagnostics")
    assert result["total"] == 7
    problems = {row["path"]: {issue["problem"] for issue in row["issues"]} for row in result["materials"]}
    assert "unregistered_material" in problems["knowledge/Unregistered.md"]
    assert "invalid_material" in problems["knowledge/Invalid.md"]
    assert "invalid_material" in problems["knowledge/Bytes.md"]
    assert "unreadable_material" in problems["knowledge/Unreadable.md"]
    assert "duplicate_id" in problems["knowledge/Duplicate.md"]
    read = runtime.read_many(material_ids=[items[0][0]], scope_id=result["scope_id"])
    assert read["materials"][0]["status"] == "ambiguous"
    assert read["full_bodies_returned"] == 0


def test_request_conflicts_and_cursor_bounds_are_checked(runtime):
    sources(runtime, 2)
    first = runtime.audit(scope="all", request_id="one", limit=1)
    assert runtime.list(scope="all", request_id="one")["status"] == "request_conflict"
    for arguments in ({"cursor": first["next_cursor"], "offset": 1},
                      {"cursor": first["next_cursor"], "scope": "all"},
                      {"cursor": first["next_cursor"], "request_id": "different"},
                      {"cursor": "not-a-cursor"},
                      {"scope_id": first["scope_id"]},
                      {"delta_cursor": first["next_cursor"]}):
        with pytest.raises(PCWError):
            runtime.audit(**arguments)
    with pytest.raises(PCWError, match="different operation"):
        runtime.list(cursor=first["next_cursor"])


def test_scoped_read_revision_matches_normal_authoring_revision(runtime):
    sources(runtime, 1)
    project = runtime.project_read()
    inventory = runtime.audit(scope="all", request_id="revision")
    captured = next(row for row in inventory["materials"] if row["id"] == project["id"])
    assert captured["record_revision"] == project["revision"]
    result = runtime.read_many(material_ids=[project["id"]], scope_id=inventory["scope_id"])
    assert result["materials"][0]["revision"] == project["revision"]
    assert result["materials"][0]["snapshot_match"]


def test_list_inventory_remains_available_with_unregistered_sources(runtime):
    sources(runtime, 2)
    (runtime.engine.knowledge / "Unregistered.md").write_text("# Not registered\n", encoding="utf-8")
    first = runtime.list(scope="all", request_id="full-list", limit=2)
    second = runtime.list(cursor=first["next_cursor"], limit=2)
    assert first["total"] == 4 and second["catalog_remaining"] == 0
    assert any(row["issues"][0]["problem"] == "unregistered_material" for row in second["materials"] if row["issues"])


def test_scoped_signatures_are_published_in_guide_and_registry(runtime, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from contextopus.core.operations import OperationRegistry
    from contextopus.core.procedure_response import enrich_procedure
    from workspace import operations, server
    calls = []
    monkeypatch.setattr(server, "dispatch", lambda root, context, operation, arguments, **options:
                        calls.append(operation) or getattr(runtime, operation.removeprefix("workspace."))(**arguments))
    registry = OperationRegistry()
    operations.register_operations(registry)
    context = SimpleNamespace(root=runtime.engine.work_root, workspace="project")
    for name in ("list", "audit"):
        schema = registry.describe("workspace." + name)["arguments_schema"]
        assert {"scope", "request_id", "cursor", "scope_id", "delta_refresh", "delta_cursor"} <= schema["properties"].keys()
    assert "scope_id" in registry.describe("workspace.read_many")["arguments_schema"]["properties"]
    inventory = registry.dispatch("query", "workspace.list", {"scope": "all", "request_id": "registry"}, context)
    result = registry.dispatch("query", "workspace.read_many", {"scope_id": inventory["scope_id"],
                               "material_ids": [inventory["materials"][0]["id"]]}, context)
    assert result["full_bodies_returned"] == 1
    procedure = Path(operations.__file__).parent / "resources/procedures/audit.md"
    guide = enrich_procedure({"content": procedure.read_text(encoding="utf-8")}, registry, lambda name: name)
    definitions = {item["operation"]: item for item in guide["operations"]}
    assert definitions["workspace.audit"] == registry.describe("workspace.audit")
    assert calls == ["list", "read_many"]


def test_deleted_context_is_reported_without_blocking_existing_scope(runtime):
    sources(runtime, 1)
    first = runtime.list(scope="all", request_id="context-delete", limit=1)
    ident = first["materials"][0]["id"]
    (runtime.engine.root / "Context.md").unlink()
    page = runtime.list(cursor=first["next_cursor"])
    assert page["total"] == 2 and page["catalog_remaining"] == 0
    read = runtime.read_many(material_ids=[ident], scope_id=first["scope_id"])
    assert read["materials"][0]["status"] == "missing"
    delta = runtime.list(scope_id=first["scope_id"], delta_refresh=True)
    assert delta["changes"][0]["path"] == "Context.md"
    assert delta["changes"][0]["change"] == "deleted"
    assert delta["required_instruction_changes"]["after"]["issues"]


def test_external_edit_between_capture_and_delivery_is_not_counted(runtime, monkeypatch):
    items = sources(runtime, 1)
    first = runtime.audit(scope="all", request_id="concurrent-edit")
    capture = ReviewScopes._capture
    def edit_after_capture(self):
        snapshot = capture(self)
        path = items[0][1]
        path.write_text(path.read_text().replace("Fact 0.", "Concurrent edit."), encoding="utf-8")
        return snapshot
    monkeypatch.setattr(ReviewScopes, "_capture", edit_after_capture)
    read = runtime.read_many(material_ids=[items[0][0]], scope_id=first["scope_id"])
    assert not read["materials"][0]["snapshot_match"]
    assert read["materials"][0]["status"] == "changed_during_read"
    assert read["materials"][0]["current_revision"] is None
    assert read["materials"][0]["current_file_revision"] != read["materials"][0]["file_revision"]
    assert read["full_bodies_returned"] == 0


def test_compact_presentation_preserves_review_receipts_and_full_body(runtime):
    from contextopus.interfaces.mcp.presentation import present
    sources(runtime, 1)
    first = runtime.list(scope="all", request_id="presentation", limit=1)
    compact = present(first, operation="workspace.list")
    for field in ("scope_id", "manifest_revision", "total", "catalog_entries_returned",
                  "catalog_remaining", "full_bodies_returned", "bodies_remaining", "next_cursor"):
        assert compact[field] == first[field]
    read = runtime.read_many(material_ids=[first["materials"][0]["id"]], scope_id=first["scope_id"])
    compact_read = present(read, operation="workspace.read_many")
    assert compact_read["materials"][0]["content"] == read["materials"][0]["content"]
    assert compact_read["materials"][0]["snapshot_match"] is True
    assert compact_read["full_bodies_returned"] == 1


def test_invalid_metadata_cannot_receive_valid_body_credit(runtime):
    items = sources(runtime, 1)
    path = items[0][1]
    path.write_text(path.read_text().replace("type: knowledge", "type: ''"), encoding="utf-8")
    inventory = runtime.audit(scope="all", request_id="invalid-metadata")
    result = runtime.read_many(material_ids=[items[0][0]], scope_id=inventory["scope_id"])
    assert result["materials"][0]["status"] == "invalid"
    assert not result["materials"][0]["snapshot_match"]
    assert result["full_bodies_returned"] == 0


def test_full_body_preserves_standard_required_source_declarations(runtime):
    items = sources(runtime, 1)
    path = items[0][1]
    with path.open("a", encoding="utf-8") as stream:
        stream.write('\n```yaml\nsources:\n  - reference: "https://example.test/reference"\n    instruction: "Read the complete source"\n```\n')
    inventory = runtime.audit(scope="all", request_id="sources")
    result = runtime.read_many(material_ids=[items[0][0]], scope_id=inventory["scope_id"])
    assert result["materials"][0]["sources"] == [{"reference": "https://example.test/reference",
                                                 "instruction": "Read the complete source"}]
    assert result["full_bodies_returned"] == 1


def test_only_bound_instructions_enter_manifest_and_instruction_delta(runtime):
    from workspace.context_document import library
    store = library(runtime)
    unrelated = store.save("unrelated.md", {"id": "unrelated", "type": "instruction"}, "Unrelated rules")
    first = runtime.audit(scope="all", request_id="instructions")
    required = first["required_instructions"]["documents"]
    assert [entry["id"] for entry in required] == ["workspace.project"]
    store.save(unrelated["path"], unrelated["metadata"], "Changed unrelated rules", unrelated["revision"])
    unrelated_delta = runtime.audit(scope_id=first["scope_id"], delta_refresh=True)
    assert unrelated_delta["required_instruction_changes"] is None
    bound = store.read(required[0]["id"])
    store.save(bound["path"], bound["metadata"], bound["content"] + "\nChanged bound rules.\n", bound["revision"])
    delta = runtime.audit(scope_id=first["scope_id"], delta_refresh=True)
    change = delta["required_instruction_changes"]
    assert change["before"]["documents"] == required
    assert change["after"]["documents"][0]["revision"] != required[0]["revision"]
    assert delta["total"] == first["total"]
