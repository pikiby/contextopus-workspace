import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from unittest.mock import patch

import pytest

from test_runtime import Factory
from test_workspace_v3 import create
from pcw_runtime import PCWError
from pcw_runtime.cycles import CycleRuntime
from pcw_runtime.analytics import EventStore, redact
from pcw_runtime.server import dispatch


def _record_process_events(root, worker):
    store = EventStore(root)
    for number in range(5):
        started = time.monotonic()
        event = store.begin("p", "read", {"worker": worker, "number": number})
        store.end(event, "p", started, {}, {}, result={"number": number})


def test_independent_processes_share_journal_without_lost_events(tmp_path):
    store = EventStore(tmp_path)
    store.storage_apply(store.storage_prepare()["revision"])
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(_record_process_events, tmp_path, worker) for worker in range(3)]
        for future in futures:
            future.result(timeout=45)
    store = EventStore(tmp_path)
    report = store.query()
    assert report["totals"]["calls"] == 15
    assert report["totals"]["errors"] == 0
    assert report["totals"]["unknown_outcomes"] == 0
    identities = {(store.read(event["id"])["params"]["value"]["worker"],
                   store.read(event["id"])["params"]["value"]["number"])
                  for event in report["events"]}
    assert identities == {(worker, number) for worker in range(3) for number in range(5)}


def test_dispatch_records_calls_errors_and_actual_files(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    create(runtime)
    with patch("pcw_runtime.server.runtime", return_value=runtime):
        started = dispatch(str(tmp_path), "p", "startup", {"request_id": "work"})
        cycle = started["cycle_id"]
        p = dispatch(str(tmp_path), "p", "proposal_prepare", {"cycle_id": cycle, "changes": [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "fact", "content": "# Fact\nObserved mechanism"}]})
        result = dispatch(str(tmp_path), "p", "proposal_apply", {"cycle_id": cycle, "proposal_id": p["proposal_id"], "expected_revision": p["revision"]})
        event = runtime.analytics_read(result["analytics"]["event_id"])
        assert event["state"] == "completed"
        assert event["duration_ms"] >= 0
        assert event["versions"] == []
        assert event["result"]["value"]["history"]["status"] == "committed"
        assert event["operation_id"] == p["proposal_id"]
        path = runtime.engine.root / result["materials"][0]["path"]
        path.write_text(path.read_text().replace("Observed mechanism", "Outside MCP edit"))
        read = dispatch(str(tmp_path), "p", "read", {"material_id": p["created_ids"]["fact"]})
        observed = runtime.analytics_read(read["analytics"]["event_id"])
        assert observed["references"][0]["status"] == "unversioned"
        assert observed["versions"] == []
        assert runtime.analytics_reference_read(read["analytics"]["event_id"])["status"] == "unavailable"
        with pytest.raises(PCWError):
            dispatch(str(tmp_path), "p", "read", {"material_id": "unknown.md"})
    report = runtime.analytics_query()
    assert report["totals"]["calls"] == 5
    assert report["totals"]["errors"] == 1
    assert report["tokens"] is None


def test_redaction_and_started_outcome_and_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "environment-secret-123")
    store = EventStore(tmp_path)
    store.storage_apply(store.storage_prepare()["revision"])
    raw = {"api_key": "key-123", "content": "password: hidden-password\nBearer hidden-auth\nvalue environment-secret-123"}
    ident = store.begin("p", "tmp_write", raw)
    event = store.read(ident)
    assert event["state"] == "started"
    assert event["params"]["redactions"] >= 3
    serialized = json.dumps(event)
    for value in ("key-123", "hidden-password", "hidden-auth", "environment-secret-123"):
        assert value not in serialized
    def record(i):
        start = time.monotonic()
        event = store.begin("p", "read", {"number": i})
        store.end(event, "p", start, {}, {}, result={"number": i})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(12)))
    assert store.query()["totals"]["calls"] == 13
    assert store.query()["totals"]["unknown_outcomes"] == 1


def test_import_another_agent_session_and_keep_evaluation_separate(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    store = EventStore(tmp_path)
    store.storage_apply(store.storage_prepare()["revision"])
    path = tmp_path / "another-session.jsonl"
    path.write_text('\n'.join(json.dumps(value) for value in [
        {"type": "session_meta", "payload": {"id": "other-agent"}},
        {"type": "response_item", "timestamp": "2026-09-30T00:00:00Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Read the context"}]}},
        {"type": "response_item", "timestamp": "2026-09-30T00:00:01Z", "payload": {"type": "function_call", "name": "pcw_read", "arguments": "{}"}},
    ]))
    result = runtime.analytics_import(str(path), "codex-jsonl", "Analyze this other agent's session")
    assert result["session_id"] == "other-agent"
    assert result["imported_events"] == 2
    assert runtime.analytics_import(str(path), "codex-jsonl", "Retry")["imported_events"] == 0
    report = runtime.analytics_query(import_id=result["import_id"])
    assert report["totals"]["calls"] == 2
    event_id = report["events"][0]["id"]
    runtime.analytics_evaluate("Read before write", "Insufficient observations", [event_id])
    assert runtime.analytics_query(import_id=result["import_id"])["totals"]["calls"] == 2
    assert runtime.analytics_query()["totals"]["calls"] == 0  # old import is outside the default week
    assert not runtime.engine.root.exists()


def test_large_result_is_retrievable_and_failed_ack_is_not_success(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    create(runtime)
    large = "Observed source text.\n" * 12000
    store = EventStore(tmp_path)
    event = store.begin("p", "large_read", {})
    store.end(event, "p", time.monotonic(), {}, {}, result={"content": large})
    body = store.read(event)["result"]
    assert body["truncated"]
    first = store.blob_read(body["blob_id"], max_chars=1000)
    assert first["truncated"] and first["next_offset"] == 1000
    assert "Observed source text" in first["text"]
    with patch("pcw_runtime.server.runtime", return_value=runtime):
        cycle = dispatch(str(tmp_path), "p", "startup", {"request_id": "work"})["cycle_id"]
        p = runtime.proposal_prepare(cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "fact", "content": "Durable fact"}])
        with patch.object(EventStore, "end", side_effect=OSError("lost journal acknowledgement")):
            result = dispatch(str(tmp_path), "p", "proposal_apply", {"cycle_id": cycle, "proposal_id": p["proposal_id"], "expected_revision": p["revision"]})
        assert result["status"] == "applied"
        assert result["analytics"]["status"] == "completion_unrecorded"
        assert store.read(result["analytics"]["event_id"])["state"] == "started"
        assert runtime.read(material_id=p["created_ids"]["fact"])["content"] == "Durable fact"


def test_malformed_and_unsupported_import_leave_no_false_records(tmp_path):
    runtime = CycleRuntime(tmp_path, "p", provider_factory=Factory())
    path = tmp_path / "transcript.jsonl"
    path.write_text('{"kind":"message","timestamp":"now","data":"valid first record"}\nnot-json')
    with pytest.raises(PCWError, match="line 2"):
        runtime.analytics_import(str(path), "pcw-session-v1", "Inspect this session", "other")
    with pytest.raises(PCWError, match="Supported"):
        runtime.analytics_import(str(path), "unknown-host", "Inspect", "other")
    assert not runtime._events().path.exists()
