# Search evidence and analysis

Use `w-analyze` to investigate a specific question over a bounded period. The skill loads the installed analysis procedure. It evaluates both compliance with historical instructions and whether the selected algorithm and parameters helped the task. A search call or file read alone does not prove that an agent used the result correctly.

## What is retained

Core's `core/analytics/operations.sqlite3` records operation metadata. Its `search_evidence` table adds search-specific evidence linked by the same event ID:

- algorithm ID/version, effective parameters and their origins;
- scope, candidate/fragment/material counts, cut reasons and boundary distances;
- up to 1,000 candidate IDs, revisions, part numbers, distances and selection flags;
- query hash, supplied task ID/purpose, and selected context's instruction fingerprint;
- a Workspace event ID when that module supplied its own observation.

Search text and source bodies are not copied into this table. Candidate metadata has one owner; the generic operation result links to it. Git remains the source of historical permanent text. Workspace events can link the search to exact Git versions; a fingerprint alone cannot recover missing historical instructions. Imports of a user-selected host transcript can supply the actual question, final answer, external work and token usage that Core does not observe.

Compact responses omit the candidate snapshot; `core.search_event` exposes the full retained evidence separately. The evidence format and shared selection contract are versioned independently of the package and algorithm. The response distinguishes the retrieval candidate limit from the evidence cap. An event can retain the full considered pool while retrieval itself was capped, or retain only part of a larger pool. Neither case supports a claim about all potentially relevant records.

## Inspect a bounded episode

The following are `read` tool arguments. The selected root can be supplied as `work_root`.

```json
{"operation":"core.search_events","arguments":{"context":"example","task_id":"task-id","limit":20}}
```

Without dates, the operation freezes the last seven days and returns the exact `since` and `until`. Reuse those boundaries and `next_offset` for subsequent pages. You can filter by the historical instruction fingerprint to avoid judging work under different rules as one era.

```json
{"operation":"core.search_event","arguments":{"event_id":"returned-event-id"}}
```

Inspect only the relevant events. Match their returned IDs and revisions to the user's task, answers and historical rules. For Workspace, follow `workspace_event_id` through `workspace.analytics_read` and the existing history/reference operations. Use `describe` only for an unknown signature.

## Compare a selection

```json
{"operation":"core.search_replay","arguments":{"event_id":"returned-event-id","selection":{"algorithm":"nearest_n","parameters":{"N":8}}}}
```

Replay applies the **current implementation** of the requested algorithm to the recorded candidate pool. It does not generate embeddings or query today's corpus. The response reports original and alternative algorithm versions/settings, added/removed materials (or distinct fragments), and whether the snapshot or original retrieval was truncated. It cannot reconstruct an older algorithm implementation merely from its version string. `replay_kind: "current_counterfactual"` and `original_execution_reproduced: false` make this explicit. Recorded original versions, boundaries and grouping order are preserved. A missing historical selection contract stays unknown rather than being labelled as contract 2.

`candidate_snapshot_truncated` and `retrieval_truncated` distinguish evidence loss from the original candidate cap; compatibility `snapshot_truncated` is true for either. `original_selection_complete` checks whether all originally returned units are represented by selected flags in the retained snapshot (null if the old selected count is unavailable). Original results outside the retained snapshot cannot appear in the removed list. `same_implementation` compares recorded algorithm version and shared selection contract; it does not establish result quality or recreate the historical execution. Even a complete retained snapshot describes only the structurally eligible indexed pool at the time.

Optional `relevant_keys` is a list of caller-labelled relevant material keys, for example `[["workspace","example","record-id"]]`. Replay then reports precision and recall against those labels. State who supplied the labels and what they cover. When original selections are missing or their completeness is unknown, the original score is null; `original_recorded_subset` scores only observed selected flags. These are not automatic measurements of answer correctness or global recall. Fragment-mode replay reports changes but does not accept material-level relevance labels.

## Interpret the evidence

| Question | Required evidence |
|---|---|
| Was selection too narrow? | Relevant excluded candidates or missing task coverage; a broader retrieval if the old pool was capped |
| Was it too broad? | Irrelevant returned/read records measured against the actual question |
| Did changing parameters help? | Same-pool replay plus task relevance, or explicitly separate current-corpus observations |
| Were the instructions followed? | Applicable historical rules and observed operations; missing evidence stays unknown |
| Were tokens or task time saved? | Host usage and comparable control runs; Core duration/bytes alone cannot answer this |

Keep findings tied to event IDs and distinguish evidence from inference. The analysis procedure does not change project rules or records automatically. `workspace.analytics_evaluate` retains an assessment only when the user asks to retain it.

See [search settings](search-selection.md) for live tuning and [architecture](architecture.md#operation-evidence-and-analysis) for the Git/SQLite separation.
