# Primary-agent memory implementation assessment

Historical prerelease check for 0.7.0, dated **2026-09-16**. Personal paths and trace/material IDs are anonymized; measurements remain unchanged.

## Accepted boundary

The user explicitly requested an **assignment-level prohibition**. It is neither tool/filesystem isolation nor a guarantee of model compliance. The primary reads and changes knowledge, Current, links, consolidation, control state and authorized memory history. An executor receives a self-contained task with needed facts and permitted sources, without memory coordinates, material IDs, journal or PCW-control instructions. Launch uses no inherited primary conversation; host-level general rules/tools remain available.

MCP-generated text forbids executor PCW tools and searching/reading/changing `.pcw` through files, shell or scripts. Executor no longer needs pcw_assignment_get; the primary passes only handoff.launch_message and binds the actual session itself.

## Implementation

- Primary saves the original substantive return without rewriting. MCP creates filename-ID, UTC saved_at and actual executor_session_id. Each substantive assignment/continuation gets its own ID. Conflicting actual/saved returns block acceptance; identical delivery is idempotent.
- Boundary helper receives positioned source text and returns only block endings. Primary calls pcw_submit_boundaries; MCP checks revision/order/full coverage, reconstructs exact parts and indexes. Helper return does not become knowledge. Preparation technical state/errors are control records, without recursively indexing indexing reports.
- Primary updates Current from registered goal/constraints/completion criteria. Explicit task corrections are revision-checked and preserve unfinished assignments.
- Intermediate/final factual-check plan, reached checks and unresolved discrepancies persist. A checker is not automatically assigned for every return.
- Missing checkpoint authority is not_authorized. Required/forbidden policies need reasons. Finish checks actual obligations.

Historical updated files were skill/reference procedures, PLAN, README and the local plugin-update-design governing record. Consolidation provenance preservation was not added as a requirement; older reports were marked historical.

## Automated checks

Final full run: **147 passed,6 skipped,73 subtests**,18.37 s, seven LanceDB deprecation warnings; `/tmp/pcw-final-tests.log`. Covered exact original text/CRLF/IDs/metadata, conflicting returns, continuations, technical errors, invalid/stale boundaries, Current/task consistency, check plan, unauthorized checkpoint and actual MCP schema; existing search/recovery tests also passed. Temporary directories/test vectors only. Six opt-in installed-host/Qwen/extended-native checks skipped. No full installed-plugin acceptance or Qwen load. LanceDB ran outside the sandbox with timeout because its background connection hung inside. Skill/plugin validators and git diff --check passed.

## Real native MCP cycle

Driver `tests/native_primary_memory_probe.py` ran current-checkout stdio MCP with deterministic vectors and synthetic demo-stage facts rather than a live cluster.

| Stage | Observation |
| --- | --- |
| Description | Clean native session trace-redacted-04, Luna low, returned substantive Markdown on cluster purpose and three Pod roles. |
| Save/read | Primary saved exact return, read and checked facts. Material `00000000000000000000000000000003.md`, saved_at `2026-09-16T20:40:08.328521Z`, actual author session retained. |
| Boundaries | Clean trace-redacted-05, Luna low, returned `[3,7]` for seven source units; trace contained no tool calls. |
| Apply/finish | Primary submitted boundaries, updated Current and checked state: finish.ready=true, one knowledge material, index_status=indexed, pending_count0; no technical materials/checkpoint. |

**Observed limit:** despite the test's extra “no tools” instruction, description author made one exec call containing only text(...) as a progress message. It accessed neither PCW/files nor nested tools. This supports the content result and no observed memory access, but not complete compliance with the prohibition of all tools. Boundary helper used no tools. Automated success does not erase this limit.

Artifacts: `/tmp/pcw-primary-native-20260916/{start-mcp.json,accept-mcp.json,finish-mcp.json,author-return.md,boundaries.json,boundary-evidence.json}`. Native traces used the IDs above under anonymized `/home/example/.codex/sessions/2026/09/16/`. Assignment bodies in native traces are encrypted, so open-text byte comparison of delivery is unavailable; generated assignments remain in MCP responses.

## Limits

Only local implementation and the bounded native cycle were verified. User memory was neither migrated nor cleaned; existing technical materials were not deleted. No origin publication or release installation in this cycle. This is not proof that subagents technically cannot access memory or that every host behaves identically.
