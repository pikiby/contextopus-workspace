"""Measure an explicitly selected whole native task from local rollout JSONL.

No tool execution, model calls, payload export or inferred success. Include the
primary and each child's exact log; interval endpoints must cover the user task.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return parsed.timestamp()


def measure(paths, start, end):
    start_ts, end_ts = stamp(start), stamp(end)
    if end_ts <= start_ts:
        raise ValueError("end must follow start")
    responses, tools, threads, calls = set(), set(), {}, []
    for path in paths:
        for line in Path(path).open(encoding="utf-8"):
            event = json.loads(line)
            if not start_ts <= stamp(event["timestamp"]) <= end_ts:
                continue
            payload = event.get("payload", {})
            if event["type"] == "token_usage_record":
                ident = (payload["thread_id"], payload["response_id"])
                if ident in responses:
                    continue
                responses.add(ident)
                thread = threads.setdefault(ident[0], {"responses": 0, "tokens": Counter(), "input_context_range": [None, None]})
                thread["responses"] += 1
                usage = payload["usage"]
                thread["tokens"].update(usage)
                size = usage["input_tokens"]
                low, high = thread["input_context_range"]
                thread["input_context_range"] = [size if low is None else min(low, size), size if high is None else max(high, size)]
            item = payload.get("item", {})
            if payload.get("type") == "item_completed" and item.get("type") == "McpToolCall":
                key = (payload.get("thread_id"), item["id"])
                if key in tools:
                    continue
                tools.add(key)
                duration = item.get("duration", {})
                call = {"server": item["server"], "tool": item["tool"], "status": item.get("status"),
                        "runtime_seconds": duration.get("secs", 0) + duration.get("nanos", 0) / 1e9}
                if "started_at_ms" in payload and "completed_at_ms" in payload:
                    call["host_seconds"] = (payload["completed_at_ms"] - payload["started_at_ms"]) / 1000
                calls.append(call)
    for thread in threads.values():
        thread["tokens"]["uncached_input_tokens"] = thread["tokens"]["input_tokens"] - thread["tokens"]["cached_input_tokens"]
    return {"start": start, "end": end, "elapsed_seconds": end_ts - start_ts,
            "threads": threads, "model_responses": len(responses), "mcp_calls": len(calls),
            "mcp_by_tool": dict(Counter(f'{c["server"]}.{c["tool"]}' for c in calls)),
            "mcp_failures": sum(c["status"] != "completed" for c in calls),
            "mcp_runtime_seconds_sum": sum(c["runtime_seconds"] for c in calls),
            "mcp_host_seconds_sum": sum(c.get("host_seconds", 0) for c in calls),
            "mcp_missing_host_intervals": sum("host_seconds" not in c for c in calls),
            "limits": ["Token sums include repeated/cached request input, not simultaneous context size.",
                       "Summed tool intervals may overlap; do not subtract them from elapsed time as a latency breakdown.",
                       "Host intervals include review/queue/transport; unexplained delay is not automatically approval time.",
                       "This report does not verify the factual outcome or instruction compliance."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(json.dumps(measure(args.logs, args.start, args.end), ensure_ascii=False, indent=2))
