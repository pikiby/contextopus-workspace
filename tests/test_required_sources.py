"""Source-link policy and lossless body transport, not an agent-compliance claim."""
import json
from unittest.mock import patch

import pytest

from pcw_runtime import PCWError
from pcw_runtime.cycles import CycleRuntime
from pcw_runtime.policies import policies
from pcw_runtime.server import dispatch
from test_runtime import Factory
from test_workspace_v3 import create
from workspace.policies import extract_settings
from workspace.runtime import _revision
from workspace.workspaces import encode


BODY = '''# Version support

```yaml
sources:
  - reference: https://www.postgresql.org/support/versioning/
    instruction: Identify supported major versions and end-of-support dates.
```

Ordinary citation: https://www.postgresql.org/about/
'''


def legacy_instructions():
    return ('---\nallow_implicit_updates: false\nknowledge_approval_required: false\n'
            'instructions_approval_required: true\ngit_history_enabled: true\n'
            'material_max_tokens: 1024\n---\n# Existing rules\nRead every declared source afresh.\n')


@pytest.mark.parametrize("enabled", [False, True])
def test_creation_choice_and_repeated_read_roundtrip(tmp_path, enabled):
    factory = Factory()
    runtime = CycleRuntime(tmp_path, "p", provider_factory=factory)
    try:
        created = create(runtime, create_required_sources=enabled)
        assert created["settings"]["create_required_sources"] is enabled
        assert runtime.settings_read(fields=["create_required_sources"])["values"]["create_required_sources"] is enabled
        cycle = runtime.startup("write")["cycle_id"]
        proposal = runtime.proposal_prepare(cycle, [{"key": "support", "content": BODY,
            "description": "Explains how to obtain supported PostgreSQL versions and their end-of-support dates from the required authoritative source."}])
        runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
        ident = proposal["created_ids"]["support"]
        calls = len(factory.calls)
        first = runtime.read(material_id=ident)
        second = runtime.read_many(material_ids=[ident])["materials"][0]
        third = runtime.read(material_id=ident)
        assert all(item["content"] == BODY for item in (first, second, third))
        assert all(item["links"] == [] for item in (first, second, third))
        assert len(factory.calls) == calls  # Repeated reading does not re-embed.
        cleanup = runtime.status(cycle)
        assert runtime.finish(cycle, expected_revision=cleanup["cleanup_revision"],
                              confirmation="Delete the isolated source-test drafts")["status"] == "closed"
        assert runtime.read(material_id=ident)["content"] == BODY
    finally:
        runtime.close()


def test_new_creation_requires_source_choice_but_legacy_read_does_not(tmp_path):
    old = legacy_instructions()
    assert policies(old)["create_required_sources"] is False
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    try:
        with pytest.raises(PCWError, match="create_required_sources"):
            runtime.workspace_create("new", old, "# Project", "Create")
        assert not runtime.engine.knowledge.exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("value", ['"false"', 'null', '1', '[true]', 'yes'])
def test_source_setting_requires_boolean(value):
    with pytest.raises(PCWError, match="create_required_sources"):
        policies(f"---\ncreate_required_sources: {value}\n---\nRules")


@pytest.mark.parametrize("interrupted", [False, True])
def test_accepted_old_creation_remains_retryable(tmp_path, interrupted):
    old = legacy_instructions()
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    args = ("old-request", old, "# Existing project", "Create selected context")
    try:
        # A genuine retained creation receipt is the old writer's acceptance
        # boundary. Resume its exact original signature without revalidating it
        # as a newly requested context or inventing a project description.
        receipt = runtime.control.root / "creation.json"
        receipt.parent.mkdir(parents=True)
        runtime.control.write(receipt, encode({"signature": _revision(encode(list(args))), "status": "creating"}))
        runtime.workspace_create(*args)
        if interrupted:
            data = json.loads(receipt.read_text())
            data["status"] = "creating"
            receipt.write_text(json.dumps(data))
        result = runtime.workspace_create(*args)
        assert result["status"] == "created"
        assert runtime.instructions_read()["content"] == extract_settings(old)[1]
        assert runtime.settings_read(fields=["create_required_sources"])["values"]["create_required_sources"] is False
        assert not runtime.project_read().get("description")
        with pytest.raises(PCWError, match="different creation"):
            runtime.workspace_create("other-request", old, "# Changed", "Create")
    finally:
        runtime.close()


def test_usage_analysis_can_observe_reads_links_errors_without_claiming_usage(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    try:
        create(runtime)
        target = runtime.engine.create(BODY, description="Explains the required authoritative source for current PostgreSQL version support.")
        source = runtime.engine.create("# Navigation", links=[target["id"]],
            description="Provides navigation to the PostgreSQL version-support source and its fresh retrieval conditions.")
        runtime._history_record("usage-fixture")
        with patch("pcw_runtime.server.runtime", return_value=runtime):
            dispatch(str(tmp_path), "p", "read_many", {"material_ids": [source["id"], target["id"]]})
            read = dispatch(str(tmp_path), "p", "read", {"material_id": target["id"], "from_id": source["id"]})
            with pytest.raises(PCWError):
                dispatch(str(tmp_path), "p", "read", {"material_id": "missing.md"})
        report = runtime.analytics_query(target_workspace="p")
        assert report["totals"]["calls"] == 3
        assert report["totals"]["errors"] == 1
        assert report["tokens"] is None and report["cost"] is None
        event = runtime.analytics_read(read["analytics"]["event_id"])
        assert event["params"]["value"]["from_id"] == source["id"]
        assert event["result"]["value"]["content"]["retained"] is False
        assert runtime.analytics_reference_read(read["analytics"]["event_id"])["text"] == BODY
        assert event["cycle_id"] is None and event["task_id"] is None
        assert runtime.list()["total"] == 3
    finally:
        runtime.close()
