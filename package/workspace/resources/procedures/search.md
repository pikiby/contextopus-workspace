---
operations: ["core.algorithms", "core.algorithms_refresh", "core.index_stats", "core.search_event", "workspace.read", "workspace.read_many", "workspace.search"]
---

# Search and read context

Obtain the ordinary `workspace` procedure if it is not already available; its context selection, full-rule reading/rereading and required-source steps apply. Do not reload it between phases. Never act without an applicable procedure. This search procedure adds retrieval details; independent reading starts no work cycle and creates no memory-write obligation.

Use `read(operation="workspace.search", arguments={"query":...}, work_root=..., context=...)`, then `workspace.read` / `workspace.read_many` for selected IDs. Inherit the selected context's algorithm and parameters. Default scope is that context; `categories` or `all_categories` expand evidence while retaining the active rules and write destination. Describe only an unknown signature through `guide(operation=...)`.

Assess diagnostics before selecting reads or changing the query: scope, effective algorithm/limits, eligible/considered fragments and distinct documents, relevance boundary, result cap, technical candidate cap and pending/unindexed knowledge. Material grouping precedes boundaries and limits for every algorithm. Explicit `output="fragments"` uses fragment units; `none` pages the requested units. Raising N cannot restore documents omitted by `candidate_limit`. `no_jump`, insufficient data and an N cap are different outcomes. Use default compact diagnostics for ordinary sufficiency assessment. Fetch a full candidate snapshot through `core.search_event` or `detail="full"` only when a specific diagnostic detail is missing for the task.

Choose reading from the actual task, author description, type/status and applicability conditions. A completed historical task can answer a historical question. Distance does not establish confidence or freshness. Read the full material and check its sources anew on every receipt before relying on dependent facts. Follow useful links without loading all neighbors. Missing/old/unindexed records and a small result are not proof of absence; broaden only for a specific coverage problem.

`core.algorithms` gives installed descriptions/defaults/schemas by name; semantic catalog lookup uses a fixed selector and does not change settings. Missing/stale derivative cards require explicit `core.algorithms_refresh`, not a silent search-time write. `core.index_stats` counts indexed objects/parts without vectors. One-request selection changes stay in `selection`; persistent machine settings follow the `instructions` procedure. Supply purpose/task ID only when actually known. An analysis request uses `guide(procedure="analyze")`; an ordinary lookup does not trigger an audit.

Answer when this independent lookup is complete. Omit cycle_id and do not call startup/tmp/proposal/apply/finish. A question during unfinished authorized work does not cancel that work.
