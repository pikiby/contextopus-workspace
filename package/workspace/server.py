"""Independent reads and policy-governed work cycles over host-neutral MCP."""

from __future__ import annotations
import atexit
import inspect
import json
from pathlib import Path
import threading
import time
from typing import get_type_hints
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, create_model
from .cycles import CycleRuntime
from .control import RecoveryRequired
from .analytics import EventStore
from .analytics_observation import Observation

mcp = FastMCP(
    "persistent-context-workspace",
    instructions=(
        "PCW exposes pcw_query for reading and pcw_execute for changes. Put the operation's parameters in arguments. "
        "For an unknown signature, use pcw_query(operation='describe', arguments={'operation': '<name>'}); "
        "describe with empty arguments lists operation names and their entrypoints. "
        "Keep the context selected by the user; ask if ambiguous. Creation or switching requires clear user direction. "
        "Follow the relevant Workspace procedure through w. Read Context.md and all instructions bound by its YAML mapping as the authoritative workspace rules, "
        "then the project record and relevant typed records. Independent reading needs no cycle. On format incompatibility "
        "immediately use the upgrade procedure, then resume; do not create replacement storage or wait for another migration request. "
        "Contexts under one memory root share an index. Search categories or all_categories without changing active rules. "
        "Material IDs are stable YAML values, independent of descriptive filenames. On every receipt of a sources block, "
        "freshly obtain each reference according to its instruction; prior checks never substitute, even when authoring is disabled. "
        "Work cycles follow workspace invocation and approval settings. Keep durable progress in typed task records; use optional .tmp for shared inputs, intermediate results or handoff. "
        "Review results, prepare changes or a grounded no-change conclusion, show actual changed passages and links in chat, "
        "apply under the current policy, then finish. Never replace the review with a file link. "
        "Instructions changes require the user's direct request; current instructions_approval_required controls approval. Tools bind revisions but cannot "
        "authenticate chat origin. For operation analysis use w-analyze and the canonical analyze procedure: scope the period "
        "or task/cycle/import, inspect recorded historical rules and Git references, and report evidence gaps. PCW does not "
        "control agents; external actions retain their authorization requirements."
    ),
)
_runtimes: dict[tuple[str, str], CycleRuntime] = {}
_cache_lock = threading.Lock()


def runtime(work_root: str, workspace: str, provider_factory=None, instruction_reconcile=None) -> CycleRuntime:
    from .layout import WorkspaceLayout as StorageLayout

    key = (str(StorageLayout.resolve(work_root).root), workspace)
    with _cache_lock:
        if key not in _runtimes:
            _runtimes[key] = CycleRuntime(
                work_root,
                workspace,
                **({"provider_factory": provider_factory} if provider_factory else {}),
            )
        if instruction_reconcile is not None:
            _runtimes[key].instruction_reconcile = instruction_reconcile
        return _runtimes[key]


def close_runtimes():
    with _cache_lock:
        for instance in _runtimes.values():
            instance.close()
        _runtimes.clear()


atexit.register(close_runtimes)

from .operations import (
    _DESCRIPTIONS,
    _QUERY_OPERATIONS,
    _EXECUTE_OPERATIONS,
    _ARGUMENT_MODELS,
    _DESCRIBE_MODEL,
)


def dispatch(
    work_root: str,
    workspace: str,
    method_name: str,
    kwargs: dict,
    provider_factory=None,
    instruction_reconcile=None,
):
    """Automatic observation at the common MCP execution boundary."""
    instance = runtime(
        work_root,
        workspace,
        **({"provider_factory": provider_factory} if provider_factory else {}),
        instruction_reconcile=instruction_reconcile,
    )
    events = EventStore(instance.engine.work_root)
    with instance.control.guard():
        observation = Observation(instance, method_name, kwargs)
        cycle = instance._records()["cycles"].get(kwargs.get("cycle_id"), {})
        mode = kwargs.get("purpose", cycle.get("purpose", "read"))
        started = time.monotonic()
        operation = events.begin(
            workspace, method_name, observation.payload(kwargs), mode, cycle.get("task_id"),
            metadata={**observation.metadata, "operation_id": kwargs.get("proposal_id")},
        )
        try:
            try:
                result = getattr(instance, method_name)(**kwargs)
            except RecoveryRequired as exc:
                result = exc.details
        except Exception as exc:
            try:
                events.end(
                    operation, workspace, started, error=str(exc), references=observation.references,
                )
            except Exception:
                pass  # The start remains unresolved; never invent a successful completion.
            raise
        try:
            events.end(
                operation,
                workspace,
                started,
                result=observation.historical_payload(result),
                references=observation.references,
                result_bytes=len(json.dumps(result, ensure_ascii=False).encode()),
            )
            return {
                **result,
                "analytics": {"event_id": operation, "status": "recorded"},
            }
        except Exception:
            return {
                **result,
                "analytics": {
                    "event_id": operation,
                    "status": "completion_unrecorded",
                    "reason": "Result recording failed; inspect file revisions before retrying the action",
                },
            }


def _describe(arguments: dict) -> dict:
    name = _DESCRIBE_MODEL.model_validate(arguments).operation
    if name is None:
        return {
            "operations": {
                "pcw_query": sorted(_QUERY_OPERATIONS),
                "pcw_execute": sorted(_EXECUTE_OPERATIONS),
            }
        }
    if name not in _DESCRIPTIONS:
        raise ValueError(
            "Unknown PCW operation; use pcw_query(operation='describe') to list operations"
        )
    return {
        "operation": name,
        "tool": "pcw_query" if name in _QUERY_OPERATIONS else "pcw_execute",
        "description": _DESCRIPTIONS[name],
        "arguments_schema": _ARGUMENT_MODELS[name].model_json_schema(),
    }


def _route(
    operation: str, work_root: str, workspace: str, arguments: dict, allowed: frozenset
) -> dict:
    if operation not in allowed:
        if operation in _DESCRIPTIONS:
            target = "pcw_query" if operation in _QUERY_OPERATIONS else "pcw_execute"
            raise ValueError(f"Operation {operation!r} requires {target}")
        raise ValueError(
            "Unknown PCW operation; use pcw_query(operation='describe') to list operations"
        )
    # Reject invalid input before opening storage or writing an analytics event.
    _ARGUMENT_MODELS[operation].model_validate(arguments)
    if not work_root or not workspace:
        raise ValueError(
            "work_root and workspace are required for workspace operations"
        )
    from types import SimpleNamespace
    from contextopus.core.operations import OperationRegistry
    from .operations import register_operations

    registry = OperationRegistry()
    register_operations(registry)
    kind = "query" if operation in _QUERY_OPERATIONS else "execute"
    return registry.dispatch(
        kind,
        "workspace." + operation,
        arguments,
        SimpleNamespace(root=Path(work_root), workspace=workspace),
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def pcw_query(
    operation: str,
    work_root: str | None = None,
    workspace: str | None = None,
    arguments: dict | None = None,
) -> dict:
    """Read the selected PCW workspace. Pass operation parameters in arguments, coordinates separately.

    Use operation='describe', arguments={'operation':'workspace_create'} for one operation's
    description, entrypoint and JSON parameter schema; empty arguments list names only.
    Describe needs no workspace and accesses no storage. Other reads require both coordinates.
    Read operations do not create contexts or change knowledge; observed calls are journaled.
    """
    if operation == "describe":
        return _describe({} if arguments is None else arguments)
    return _route(
        operation,
        work_root,
        workspace,
        {} if arguments is None else arguments,
        _QUERY_OPERATIONS,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def pcw_execute(
    operation: str, work_root: str, workspace: str, arguments: dict | None = None
) -> dict:
    """Execute a PCW change with the existing authorization, revision and approval checks.

    Pass operation parameters in arguments, coordinates separately. For its exact schema use
    pcw_query(operation='describe', arguments={'operation':'<name>'}). Creation needs an explicit
    request; startup never creates. Present prepared changes and follow workspace approval rules.
    Instructions approval follows instructions_approval_required in current rules. Calls are journaled under their operation names.
    """
    return _route(
        operation,
        work_root,
        workspace,
        {} if arguments is None else arguments,
        _EXECUTE_OPERATIONS,
    )
