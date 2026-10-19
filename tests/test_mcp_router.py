"""The small public surface preserves the existing operations and their boundaries."""
import asyncio
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pcw_runtime import PCWError
from pcw_runtime import server
from pcw_runtime.cycles import CycleRuntime
from test_runtime import Factory
from test_workspace_v3 import create


def test_two_small_tools_and_storage_free_discovery(tmp_path):
    with patch.object(server, "runtime", side_effect=AssertionError("metadata must not open storage")):
        tools = asyncio.run(server.mcp.list_tools())
        assert {tool.name for tool in tools} == {"pcw_query", "pcw_execute"}
        for tool in tools:
            assert set(tool.inputSchema["properties"]) == {"operation", "work_root", "workspace", "arguments"}
        catalog = server.pcw_query("describe")["operations"]
        assert len(catalog["pcw_query"]) + len(catalog["pcw_execute"]) == 50
        assert not set(catalog["pcw_query"]) & set(catalog["pcw_execute"])
        assert set(catalog["pcw_query"] + catalog["pcw_execute"]) == server._DESCRIPTIONS.keys()
        for tool, names in catalog.items():
            for name in names:
                help = server.pcw_query("describe", arguments={"operation": name})
                assert help["tool"] == tool
                assert help["description"]
                assert help["arguments_schema"]["additionalProperties"] is False
        creation = server.pcw_query("describe", arguments={"operation": "workspace_create"})
        assert set(creation["arguments_schema"]["required"]) == {
            "request_id", "instructions_content", "project_content", "user_request"}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("entry,operation,arguments,error", [
    (server.pcw_query, "workspace_create", {}, ValueError),
    (server.pcw_execute, "read", {}, ValueError),
    (server.pcw_execute, "close", {}, ValueError),
    (server.pcw_query, "__init__", {}, ValueError),
    (server.pcw_execute, "describe", {}, ValueError),
    (server.pcw_query, "describe", {"operation": "__class__"}, ValueError),
    (server.pcw_query, "describe", {"operations": ["read"]}, ValidationError),
    (server.pcw_execute, "workspace_create", {}, ValidationError),
    (server.pcw_execute, "finish", {"cycle_id": "c", "cancel": "false"}, ValidationError),
    (server.pcw_query, "read_many", {"material_ids": "one.md"}, ValidationError),
    (server.pcw_query, "list", {"limit": True}, ValidationError),
    (server.pcw_query, "search", {"N": "3"}, ValidationError),
    (server.pcw_query, "search", {"work_root": "/another"}, ValidationError),
    (server.pcw_query, "read", {"workspace": "another"}, ValidationError),
    (server.pcw_execute, "proposal_apply", {"cycle_id": "c", "proposal_id": "p", "expected_revision": "r",
                                            "knowledge_approval_required": False}, ValidationError),
])
def test_bad_input_never_opens_storage(tmp_path, entry, operation, arguments, error):
    with patch.object(server, "runtime", side_effect=AssertionError("invalid call opened storage")):
        with pytest.raises(error):
            entry(operation, str(tmp_path), "missing", arguments)
    assert not list(tmp_path.iterdir())


def test_workspace_coordinates_are_required_before_dispatch():
    with patch.object(server, "runtime", side_effect=AssertionError("missing route opened storage")):
        with pytest.raises(ValueError, match="work_root and workspace"):
            server.pcw_query("list")


def test_router_preserves_approval_conflicts_and_operation_journal(tmp_path):
    r = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    try:
        create(r, knowledge_approval_required=True)
        def execute(operation, **arguments):
            return server.pcw_execute(operation, str(tmp_path), "p", arguments)
        def query(operation, **arguments):
            return server.pcw_query(operation, str(tmp_path), "p", arguments)
        with patch.object(server, "runtime", return_value=r):
            cycle = execute("startup", request_id="router-work")["cycle_id"]
            proposal = execute("proposal_prepare", cycle_id=cycle,
                               changes=[{"key": "fact", "content": "# Fact\nVerified information",
                                         "description": "Verified fact used to test the router approval boundary."}])
            params = dict(cycle_id=cycle, proposal_id=proposal["proposal_id"], expected_revision=proposal["revision"])
            with pytest.raises(PCWError, match="confirmation"):
                execute("proposal_apply", **params)
            with pytest.raises(PCWError, match="exact presented"):
                execute("proposal_apply", **{**params, "expected_revision": "stale", "confirmation": "Approved"})
            assert query("list")["total"] == 1
            assert execute("finish", cycle_id=cycle)["status"] == "blocked"
            saved = execute("proposal_apply", **params, confirmation="Approve the shown test fact")
            material = query("read", material_id=saved["materials"][0]["id"])
            assert material["content"] == "# Fact\nVerified information"
            assert material["analytics"]["status"] == "recorded"
            events = query("analytics_query", tool="proposal_apply")
            assert events["events"]  # Successful and failed calls retain the actual operation name.
            event = query("analytics_read", event_id=material["analytics"]["event_id"])
            assert event["tool"] == "read"
            assert {item["state"] for item in events["events"]} == {"completed", "error"}
            cleanup = query("status", cycle_id=cycle)
            assert execute("finish", cycle_id=cycle, expected_revision=cleanup["cleanup_revision"],
                           confirmation="Delete the reviewed fixture temporary files")["status"] == "closed"
    finally:
        r.close()
