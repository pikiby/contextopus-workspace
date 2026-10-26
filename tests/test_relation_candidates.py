"""Direct navigation is bounded evidence, separate from reviewed source changes."""

import json
import uuid
from unittest.mock import patch

import pytest

from contextopus.interfaces.mcp.presentation import present
from workspace.cycles import CycleRuntime
from workspace.records import record_revision, record_text
from workspace.runtime import Material, PCWError, _parse
from test_runtime import Factory
from test_workspace_v3 import create


@pytest.fixture
def runtime(tmp_path):
    instance = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    create(instance, git_history_enabled=False, material_max_tokens=None)
    yield instance
    instance.close()


def seed(runtime, graph):
    """Write unindexed source fixtures without starting a work cycle."""
    ids = {name: uuid.uuid5(uuid.NAMESPACE_URL, "relation-test:" + name).hex + ".md"
           for name in graph}
    materials = {
        ids[name]: Material(ids[name], "knowledge/" + name + ".md",
            {"id": ids[name], "type": "knowledge", "description": "Purpose of " + name,
             "links": [ids[target] for target in targets]}, "# " + name + "\nVerified fact.\n")
        for name, targets in graph.items()
    }
    codec = runtime.engine.link_codec(materials, {ident: "project" for ident in materials})
    for material in materials.values():
        (runtime.engine.root / material.path).write_text(
            codec.record_text(material.metadata, material.content), encoding="utf-8", newline="")
    return ids


def change_description(runtime, path, description):
    target = runtime.engine.root / path
    metadata, body = _parse(target.read_text(encoding="utf-8"))
    metadata["description"] = description
    target.write_text(record_text(metadata, body), encoding="utf-8", newline="")


def prepare(runtime, changes):
    opened = runtime.startup("review", user_request="Review the selected changes")
    proposal = runtime.proposal_prepare(opened["cycle_id"], changes,
        expected_task_revision=opened["task_revision"])
    return opened["cycle_id"], proposal


def test_read_is_one_hop_without_cycle_or_embeddings(runtime):
    ids = seed(runtime, {"A": ["B", "B"], "B": ["C"], "C": []})
    provider = runtime.engine._factory
    calls, instances = len(provider.calls), len(provider.instances)
    middle = runtime.read(material_id=ids["B"])
    envelope = middle["relation_candidates"]
    assert {(item["id"], item["direction"]) for item in envelope["items"]} == {
        (ids["A"], "incoming"), (ids["C"], "outgoing")}
    assert envelope["coverage"] == "workspace_yaml_links"
    assert envelope["inventory_complete"] is True
    assert "cross-module" in envelope["coverage_limits"][0]
    for item in envelope["items"]:
        assert item["anchor_id"] == ids["B"]
        assert item["revision"] == record_revision(runtime.engine.catalog[item["id"]])
        assert item["revision"] != runtime.engine.catalog[item["id"]].revision
        target = item["link"][2:-2].split("|", 1)[0]
        assert (runtime.engine.work_root / target).is_file()
        assert item["in_proposal"] is False
    assert [item["id"] for item in runtime.read(material_id=ids["A"])["relation_candidates"]["items"]] == [ids["B"]]
    assert (len(provider.calls), len(provider.instances)) == (calls, instances)
    assert not runtime.record_path.exists()
    assert not (runtime.engine.root / ".tmp").exists()


def test_read_many_reuses_one_catalog_and_codec(runtime):
    ids = seed(runtime, {"A": ["B"], "B": ["C"], "C": []})
    with patch.object(runtime.engine, "_scan", wraps=runtime.engine._scan) as scan, \
         patch.object(runtime.engine, "link_codec", wraps=runtime.engine.link_codec) as codec:
        result = runtime.read_many(material_ids=list(ids.values()))
    assert scan.call_count == 1
    assert codec.call_count == 2  # One source decode and one relation decoration.
    assert [item["id"] for item in result["materials"]] == list(ids.values())
    assert all(item["relation_candidates"]["items"] for item in result["materials"])
    compact = present(result, operation="workspace.read_many")
    assert [item["relation_candidates"] for item in compact["materials"]] == [
        item["relation_candidates"] for item in result["materials"]]


def test_project_full_read_also_returns_relations(runtime):
    project = runtime.project_read()
    ids = seed(runtime, {"Source": []})
    target = runtime.engine.root / "knowledge/Source.md"
    metadata, body = _parse(target.read_text(encoding="utf-8"))
    metadata["links"] = [project["id"]]
    target.write_text(record_text(metadata, body), encoding="utf-8", newline="")
    related = runtime.project_read()["relation_candidates"]["items"]
    assert [(item["id"], item["direction"]) for item in related] == [(ids["Source"], "incoming")]


def test_missing_target_and_incompatible_context_are_explicit(runtime):
    ids = seed(runtime, {"A": []})
    target = runtime.engine.root / "knowledge/A.md"
    metadata, body = _parse(target.read_text(encoding="utf-8"))
    metadata["links"] = ["missing.md"]
    target.write_text(record_text(metadata, body), encoding="utf-8", newline="")
    (runtime.engine.root.parent / "legacy" / "knowledge").mkdir(parents=True)
    envelope = runtime.read(material_id=ids["A"])["relation_candidates"]
    assert envelope["inventory_complete"] is False
    assert envelope["unavailable_contexts"] == ["legacy"]
    missing, = envelope["items"]
    assert missing["id"] == "missing.md" and missing["missing"] is True
    assert all(missing[field] is None for field in ("workspace", "path", "link", "revision"))
    with pytest.raises(PCWError, match="Unknown material ID"):
        runtime.read(material_id="missing.md")


def test_read_pages_and_invalid_limits(runtime):
    names = ["neighbor-" + str(index) for index in range(103)]
    ids = seed(runtime, {"A": names, **{name: [] for name in names}})
    first = runtime.read(material_id=ids["A"], relation_limit=100)["relation_candidates"]
    last = runtime.read(material_id=ids["A"], relation_offset=first["next_offset"], relation_limit=100)["relation_candidates"]
    assert first["total"] == last["total"] == 103
    assert len(first["items"]) == 100 and len(last["items"]) == 3
    assert last["next_offset"] is None
    assert len({item["id"] for item in first["items"] + last["items"]}) == 103
    for offset, limit in [(-1, 1), (True, 1), (0, 0), (0, 101), (0, True)]:
        with pytest.raises(ValueError):
            runtime.read(material_id=ids["A"], relation_offset=offset, relation_limit=limit)


def test_proposal_retains_neighbors_after_metadata_changes(runtime):
    ids = seed(runtime, {"A": ["B"], "B": ["C"], "C": []})
    middle = runtime.read(material_id=ids["B"])
    cycle_id, proposal = prepare(runtime, [{"material_id": ids["B"],
        "expected_revision": middle["revision"], "description": "Updated middle purpose"}])
    captured = proposal["relation_candidates"]
    assert captured["captured_at_preparation"] is True
    assert all(not item["in_proposal"] for item in captured["items"])
    current_before = next(item for item in captured["items"] if item["id"] == ids["C"])
    body_revision = runtime.engine.catalog[ids["C"]].revision
    change_description(runtime, "knowledge/C.md", "Neighbor was independently revised")
    current = next(item for item in runtime.read(material_id=ids["B"])["relation_candidates"]["items"] if item["id"] == ids["C"])
    assert runtime.engine.catalog[ids["C"]].revision == body_revision
    assert current["revision"] != current_before["revision"]
    reviewed = runtime.proposal_read(cycle_id, proposal["proposal_id"])
    assert reviewed["revision"] == proposal["revision"]
    assert reviewed["relation_candidates"] == captured
    assert present(reviewed, operation="workspace.proposal_read")["relation_candidates"] == captured
    assert runtime.proposal_apply(cycle_id, proposal["proposal_id"], proposal["revision"])["status"] == "applied"


def test_proposal_marks_automatic_link_edits_and_keeps_ordinary_neighbors(runtime):
    ids = seed(runtime, {"A": ["B"], "B": ["C"], "C": []})
    middle = runtime.read(material_id=ids["B"])
    cycle_id, proposal = prepare(runtime, [{"material_id": ids["B"],
        "expected_revision": middle["revision"], "path": "Renamed-B.md"}])
    assert {item["id"] for item in proposal["review_changes"]} == {ids["A"], ids["B"]}
    middle_relations = {item["id"]: item for item in proposal["relation_candidates"]["items"]
                        if item["anchor_id"] == ids["B"]}
    assert middle_relations[ids["A"]]["in_proposal"] is True
    assert middle_relations[ids["C"]]["in_proposal"] is False
    rows = []
    offset = 0
    while True:
        page = runtime.proposal_read(cycle_id, proposal["proposal_id"], relation_offset=offset, relation_limit=1)["relation_candidates"]
        rows += page["items"]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert rows == proposal["relation_candidates"]["items"]


def test_new_proposal_neighbor_revision_matches_saved_record(runtime):
    cycle_id, proposal = prepare(runtime, [
        {"key": "a", "content": "# A\nFact", "description": "Purpose of A", "links": ["@b"]},
        {"key": "b", "content": "# B\nFact", "description": "Purpose of B"},
    ])
    captured = proposal["relation_candidates"]["items"]
    assert all(item["in_proposal"] for item in captured)
    runtime.proposal_apply(cycle_id, proposal["proposal_id"], proposal["revision"])
    for neighbor in captured:
        assert neighbor["revision"] == runtime.read(material_id=neighbor["id"])["revision"]


def test_proposal_captures_removed_and_added_edges(runtime):
    ids = seed(runtime, {"A": ["B"], "B": ["C"], "C": [], "D": []})
    middle = runtime.read(material_id=ids["B"])
    _, proposal = prepare(runtime, [{"material_id": ids["B"],
        "expected_revision": middle["revision"], "links": [ids["D"]]}])
    assert {(item["id"], item["direction"]) for item in proposal["relation_candidates"]["items"]} == {
        (ids["A"], "incoming"), (ids["C"], "outgoing"), (ids["D"], "outgoing")}
    assert all(not item["in_proposal"] for item in proposal["relation_candidates"]["items"])


def test_proposal_retains_every_relation_page(runtime):
    names = ["neighbor-" + str(index) for index in range(103)]
    ids = seed(runtime, {"A": names, **{name: [] for name in names}})
    item = runtime.read(material_id=ids["A"])
    cycle_id, proposal = prepare(runtime, [{"material_id": ids["A"],
        "expected_revision": item["revision"], "description": "Changed anchor purpose"}])
    initial = proposal["relation_candidates"]
    assert initial["total"] == 103 and len(initial["items"]) == 50
    assert initial["next_offset"] == 50
    first = runtime.proposal_read(cycle_id, proposal["proposal_id"], relation_limit=100)["relation_candidates"]
    last = runtime.proposal_read(cycle_id, proposal["proposal_id"], relation_offset=100, relation_limit=100)["relation_candidates"]
    assert first["total"] == last["total"] == 103
    assert len(first["items"]) == 100 and len(last["items"]) == 3
    assert len({row["id"] for row in first["items"] + last["items"]}) == 103
    assert last["next_offset"] is None


def test_captured_relations_are_in_the_proposal_digest(runtime):
    ids = seed(runtime, {"A": ["B"], "B": []})
    item = runtime.read(material_id=ids["A"])
    cycle_id, proposal = prepare(runtime, [{"material_id": ids["A"],
        "expected_revision": item["revision"], "description": "Changed purpose"}])
    path = runtime._plan_path(cycle_id, proposal["proposal_id"])
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["relation_candidates"]["items"][0]["description"] = "Unreviewed replacement"
    path.write_text(json.dumps(saved), encoding="utf-8", newline="")
    with pytest.raises(PCWError, match="Prepared proposal was changed"):
        runtime.proposal_read(cycle_id, proposal["proposal_id"])
