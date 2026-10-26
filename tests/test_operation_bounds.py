"""Published Workspace limits reject invalid input before runtime execution."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contextopus.core.operations import OperationInputError, OperationRegistry
from contextopus.core.procedure_response import enrich_procedure
from contextopus.startup import Application
from workspace import discovery, operations, server
from workspace.edit_reservations import EditReservations
from workspace.history_queries import excerpt, page
from workspace.runtime import PCWError


@pytest.fixture
def contract(monkeypatch, tmp_path):
    calls = []

    def dispatch(root, context, operation, arguments, **options):
        calls.append((operation, arguments))
        return arguments

    monkeypatch.setattr(server, "dispatch", dispatch)
    registry = OperationRegistry()
    operations.register_operations(registry)
    context = SimpleNamespace(root=tmp_path, workspace="test")
    return registry, context, calls


# Keep different handler budgets explicit: analytics queries allow 1000 events,
# while read/list pages and history indexing allow at most 100 entries.
BOUNDS = [
    ("list", {}, "limit", 1, 100),
    ("list", {}, "offset", 0, None),
    ("cycles_list", {}, "limit", 1, 100),
    ("audit", {}, "limit", 1, 100),
    ("tmp_list", {"cycle_id": "cycle"}, "limit", 1, 100),
    ("reservations", {}, "limit", 1, 100),
    ("reserve", {"cycle_id": "cycle", "material_ids": ["card"], "reason": "Edit"}, "ttl_seconds", 1, 3600),
    ("reservation_renew", {"cycle_id": "cycle", "reservation_id": "reservation"}, "ttl_seconds", 1, 3600),
    ("history_list", {}, "limit", 1, 100),
    ("history_search", {"text": "query"}, "limit", 1, 100),
    ("history_changes", {}, "limit", 1, 100),
    ("history_retry", {}, "limit", 1, 100),
    ("history_index", {}, "limit", 1, 100),
    ("history_lineage", {"material_id": "card"}, "max_depth", 0, 20),
    ("history_diff", {"before": "old", "after": "new"}, "max_chars", 1, 100000),
    ("history_read", {"commit": "HEAD"}, "max_chars", 1, 100000),
    ("analytics_query", {}, "limit", 1, 1000),
    ("analytics_report", {}, "max_events", 1, 1000),
    ("analytics_report", {}, "max_blob_bytes", 0, 16000000),
    ("analytics_read", {"event_id": "event"}, "limit", 1, 100),
    ("analytics_read", {"event_id": "event"}, "max_chars", 1, 100000),
    ("analytics_blob_read", {"blob_id": "blob"}, "max_chars", 1, 100000),
    ("analytics_reference_read", {"event_id": "event"}, "reference_index", -1, None),
    ("analytics_reference_read", {"event_id": "event"}, "max_chars", 1, 100000),
    ("analytics_migrate", {}, "limit", 1, 100),
    ("analytics_migrate", {}, "git_limit", 1, 100),
]


@pytest.mark.parametrize("name,arguments,field,minimum,maximum", BOUNDS)
def test_bounds_published_and_rejected_before_dispatch(contract, name, arguments, field, minimum, maximum):
    registry, context, calls = contract
    operation = "workspace." + name
    definition = registry.describe(operation)
    schema = definition["arguments_schema"]["properties"][field]
    assert schema["minimum"] == minimum
    if maximum is not None:
        assert schema["maximum"] == maximum
    invalid = [minimum - 1, True, "PRIVATE_INPUT"]
    if maximum is not None:
        invalid.append(maximum + 1)
    for value in invalid:
        with pytest.raises(OperationInputError) as caught:
            registry.dispatch(definition["kind"], operation, {**arguments, field: value}, context)
        error = caught.value.payload["error"]
        assert error["execution_started"] is False
        assert error["definition"] == definition
        assert any(issue["path"] == [field] for issue in error["issues"])
        assert "PRIVATE_INPUT" not in json.dumps(error)
    assert calls == []
    for value in {minimum, maximum} - {None}:
        result = registry.dispatch(definition["kind"], operation, {**arguments, field: value}, context)
        assert result[field] == value


@pytest.mark.parametrize("name,arguments", [
    ("read_many", {}),
    ("reserve", {"cycle_id": "cycle", "reason": "Edit"}),
])
def test_material_batch_sizes_and_nullable_read_many(contract, name, arguments):
    registry, context, calls = contract
    operation = "workspace." + name
    definition = registry.describe(operation)
    for values in ([], [str(i) for i in range(101)]):
        with pytest.raises(OperationInputError):
            registry.dispatch(definition["kind"], operation, {**arguments, "material_ids": values}, context)
    assert calls == []
    for count in (1, 100):
        values = [str(i) for i in range(count)]
        result = registry.dispatch(definition["kind"], operation, {**arguments, "material_ids": values}, context)
        assert result["material_ids"] == values
    if name == "read_many":
        # Preserve the existing nullable signature; the runtime owns whether a
        # null list can be used for the requested read.
        assert registry.dispatch("query", operation, {}, context)["material_ids"] is None
        assert registry.dispatch("query", operation, {"material_ids": None}, context)["material_ids"] is None


def test_reason_limit_counts_characters_and_preserves_ttl_default(contract):
    registry, context, calls = contract
    arguments = {"cycle_id": "cycle", "material_ids": ["card"]}
    for reason in ("", "é" * 241):
        with pytest.raises(OperationInputError):
            registry.dispatch("execute", "workspace.reserve", {**arguments, "reason": reason}, context)
    assert calls == []
    result = registry.dispatch("execute", "workspace.reserve", {**arguments, "reason": "é" * 240}, context)
    assert result["reason"] == "é" * 240
    assert result["ttl_seconds"] == 900


def test_analytics_evidence_list_and_item_budgets(contract):
    registry, context, calls = contract
    arguments = {"criterion": "Coverage", "assessment": "Supported by evidence"}
    for values in ([], [str(i) for i in range(1001)], [""], ["x" * 1025]):
        with pytest.raises(OperationInputError):
            registry.dispatch("execute", "workspace.analytics_evaluate", {**arguments, "event_ids": values}, context)
    assert calls == []
    values = ["é" * 1021 + str(i) for i in range(1000)]
    result = registry.dispatch("execute", "workspace.analytics_evaluate", {**arguments, "event_ids": values}, context)
    assert result["event_ids"] == values


def test_search_static_budgets_keep_nullable_overrides(contract):
    registry, context, calls = contract
    definition = registry.describe("workspace.search")
    properties = definition["arguments_schema"]["properties"]
    candidates = next(item for item in properties["candidate_limit"]["anyOf"] if item["type"] == "integer")
    categories = next(item for item in properties["categories"]["anyOf"] if item["type"] == "array")
    assert (candidates["minimum"], candidates["maximum"]) == (1, 100000)
    assert (categories["minItems"], categories["maxItems"]) == (1, 1000)
    assert properties["output"]["enum"] == ["materials", "fragments"]
    for arguments in ({"candidate_limit": 0}, {"candidate_limit": 100001}, {"categories": []},
                      {"categories": [str(i) for i in range(1001)]}, {"output": "invented"}):
        with pytest.raises(OperationInputError):
            registry.dispatch("query", "workspace.search", {"query": "topic", **arguments}, context)
    assert calls == []
    arguments = {"query": "topic", "candidate_limit": 100000, "categories": [str(i) for i in range(1000)]}
    result = registry.dispatch("query", "workspace.search", arguments, context)
    assert result["candidate_limit"] == 100000
    assert result["categories"] == arguments["categories"]
    assert result["output"] == "materials"
    assert registry.dispatch("query", "workspace.search", {"query": "topic", "output": "fragments"}, context)["output"] == "fragments"


def test_guide_contracts_and_nullable_search_defaults(contract):
    registry, context, calls = contract
    procedure = Path(operations.__file__).parent / "resources/procedures/workspace.md"
    guide = enrich_procedure({"content": procedure.read_text(encoding="utf-8")}, registry, lambda name: name)
    listed = {item["operation"]: item for item in guide["operations"]}
    for name in ("workspace.cycles_list", "workspace.read_many", "workspace.reserve"):
        assert listed[name] == registry.describe(name)
    assert calls == []
    defaults = registry.dispatch("query", "workspace.search", {"query": "topic"}, context)
    for field in ("N", "d_max", "min_jump", "multiplier", "categories", "types", "statuses", "candidate_limit"):
        assert defaults[field] is None
    explicit = registry.dispatch("query", "workspace.search", {"query": "topic", "N": None}, context)
    assert explicit == defaults
    assert "maximum" not in registry.describe("workspace.search")["arguments_schema"]["properties"]["N"]
    assert registry.dispatch("query", "workspace.list", {}, context)["limit"] == 50


def test_discovery_contracts_before_filesystem_or_search(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(discovery, "contexts", lambda *args, **kw: calls.append(kw))
    monkeypatch.setattr(discovery, "projects_search", lambda *args, **kw: calls.append(kw))
    with Application(tmp_path, discover=False) as app:
        discovery.register(app)
        for operation, arguments, maximum in [
            ("workspace.contexts", {"limit": 101}, 100),
            ("workspace.projects_search", {"query": "topic", "limit": 1001}, 1000),
        ]:
            schema = app.registry.describe(operation)["arguments_schema"]
            assert schema["properties"]["limit"]["maximum"] == maximum
            with pytest.raises(OperationInputError):
                app.query(operation, arguments)
        with pytest.raises(OperationInputError):
            app.query("workspace.contexts", {"offset": -1})
    assert calls == []


def test_direct_python_guards_remain_active():
    for invalid in (0, 101, True):
        with pytest.raises(PCWError):
            page(0, invalid)
    with pytest.raises(PCWError):
        excerpt("text", 0, 100001)
    with pytest.raises(PCWError):
        EditReservations._ttl(3601)
    for invalid in (" ", "two\nlines", "é" * 241):
        with pytest.raises(PCWError):
            EditReservations._reason(invalid)
