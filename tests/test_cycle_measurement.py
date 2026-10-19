import json

from measure_native_cycle import measure


def test_cycle_measurement_deduplicates_response_records_and_separates_time(tmp_path):
    token = {"timestamp": "2026-09-16T23:45:20Z", "type": "token_usage_record", "payload": {
        "thread_id": "primary", "response_id": "response-1", "usage": {
            "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 10}}}
    call = {"timestamp": "2026-09-16T23:45:21Z", "type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": "primary", "started_at_ms": 1000, "completed_at_ms": 6000,
        "item": {"type": "McpToolCall", "id": "call-1", "server": "pcw", "tool": "pcw_startup",
                 "status": "completed", "duration": {"secs": 1, "nanos": 500000000}}}}
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in [token, token, call, call]))
    measured = measure([path], "2026-09-16T23:45:18Z", "2026-09-16T23:45:28Z")
    assert measured["elapsed_seconds"] == 10
    assert measured["model_responses"] == 1 and measured["mcp_calls"] == 1
    assert measured["threads"]["primary"]["tokens"]["uncached_input_tokens"] == 20
    assert measured["mcp_runtime_seconds_sum"] == 1.5 and measured["mcp_host_seconds_sum"] == 5
