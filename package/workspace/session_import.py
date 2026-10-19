"""Optional transcript adapters. Core event recording does not depend on a host."""

from __future__ import annotations

import json

from .runtime import PCWError


def read_session(path, adapter, session_id=None):
    if adapter not in {"pcw-session-v1", "codex-jsonl"}:
        raise PCWError("Supported transcript adapters: pcw-session-v1, codex-jsonl")
    records, ignored = [], 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PCWError(f"Invalid transcript JSON at line {number}") from exc
        if not isinstance(item, dict):
            raise PCWError(f"Transcript record at line {number} must be an object")
        if adapter == "pcw-session-v1":
            if not {"kind", "timestamp", "data"} <= item.keys():
                raise PCWError(
                    f"Normalized record {number} requires kind, timestamp and data"
                )
            records.append(
                {
                    "id": str(item.get("id", number)),
                    "kind": item["kind"],
                    "timestamp": item["timestamp"],
                    "data": item["data"],
                    "line": number,
                }
            )
            continue
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            ignored += 1
            continue
        if item.get("type") == "session_meta":
            actual = payload.get("id")
            if session_id and actual and session_id != actual:
                raise PCWError("Declared session does not match transcript metadata")
            session_id = actual or session_id
        elif item.get("type") == "response_item" and payload.get("type") in {
            "message",
            "function_call",
            "function_call_output",
            "custom_tool_call",
            "custom_tool_call_output",
        }:
            if (
                payload.get("role") in {"system", "developer"}
                or payload.get("channel") == "analysis"
            ):
                ignored += 1
                continue
            records.append(
                {
                    "id": str(number),
                    "kind": payload["type"],
                    "timestamp": item.get("timestamp"),
                    "data": payload,
                    "line": number,
                }
            )
        else:
            ignored += 1
    if not isinstance(session_id, str) or not session_id.strip():
        raise PCWError(
            "Session ID must come from transcript metadata or be explicitly supplied for normalized input"
        )
    return {"session_id": session_id, "records": records, "ignored": ignored}
