---
operations: ["workspace.analytics_blob_read", "workspace.analytics_evaluate", "workspace.analytics_import", "workspace.analytics_migrate", "workspace.analytics_query", "workspace.analytics_read", "workspace.analytics_reference_read", "workspace.analytics_report", "workspace.history_changes", "workspace.history_diff", "workspace.history_index", "workspace.history_lineage", "workspace.history_list", "workspace.history_read", "workspace.history_retry", "workspace.history_search", "workspace.read", "workspace.search"]
---

# History and analytics

## Capture and provenance

Git is local to the context. The standard creation settings enable `git_history_enabled`; existing settings and restrictive legacy defaults remain unchanged. Creation records the initial snapshot. Each applied proposal captures one logical change set of permanent typed records and INSTRUCTIONS under the context lock. Tmp, vector indexes and analytics are excluded. Save, indexing and Git outcomes are independent; no push is implied.

Give knowledge proposals a concise one-line `summary` describing the actual change and its reason. Declare semantic transformations through `transformations`: `merge` has at least two source IDs and one target; `split` has one source and at least two targets; `replace` has one distinct source and target. Targets may be `@keys` during preparation. Retained originals belong in the targets too. Sources must exist in the reviewed before state, targets in the reviewed after state. Correct navigation with `replacements` separately. An older client's replacements are recorded as replacements, never guessed merges. IDs, paths, file fingerprints, operation/cycle and declarations enter standard Git commit trailers together with the exact snapshot. The declaration is agent-supplied provenance, not proof of semantic correctness; review it with the text. Text similarity cannot reliably reconstruct rewritten or summarized content.

Preparation resolves IDs and includes summary/transformations in its immutable revision and review. Changes to them require a revised proposal and renewed approval when policy requires it. One successful application makes one commit when the tree changes. No-change results make none. `history_retry(limit=100)` replays a bounded batch of original captured text and provenance in order; it cannot pull later files or a rewritten description into an earlier operation. Legacy pending snapshots remain retryable. Once the complete captured tree is verified in Git, retry bodies become small receipts containing the commit reference; unsuccessful writes retain their recovery snapshots.

## When to inspect history

Follow the selected INSTRUCTIONS. When history is enabled, inspect it for a missing former instruction/condition/link, an unexplained conflict with verified earlier state, regression after an instruction change, or a user request about evolution, provenance or restoration. Do not traverse the whole history on every ordinary task. Stop when the scoped question is answered; broaden the search only when needed. An old commit is evidence, not active guidance or authority to restore it.

## Bounded native Git queries

Use the existing MCP query entrypoint with the following operations; no ad hoc scripts, checkout or working-tree changes are needed.

| Operation | Use |
| --- | --- |
| `history_list(material_id?, paths?, query?, since?, until?, offset?, limit?, ref?)` | Find commits by declared stable ID, exact paths, message text or ISO date. Returns parents and transformation links. |
| `history_search(text, paths?, offset?, limit?, ref?)` | Native `git log -S`: locate additions/removals of exact text, including deleted passages. This searches changes in occurrence counts, not meaning or every occurrence. |
| `history_changes(commit, offset?, limit?)` | Read changed filenames and declared record transformations, operation/cycle and before/after paths without loading file bodies. For merge commits the file comparison uses the first parent. |
| `history_diff(before, after, paths?, offset?, max_chars?)` | Read a bounded diff for selected files. |
| `history_read(commit, material_id? or paths?, offset?, max_chars?)` | Read the record at that revision even if its filename changed or it is now deleted; exact paths also support INSTRUCTIONS. |

Start with a small list/search, inspect one relevant commit, then request only its needed diff or historical record. Use the returned `head` as `ref` on subsequent list/search pages so new commits do not shift pagination. Text offsets count characters; honor returned `next_offset` and `truncated` rather than treating a partial response as complete.

For C formed from A and B, find C's history, read the merge declaration, then read A and B at the preceding commit. The stored before fingerprints describe the reviewed source state; an externally edited uncommitted source may not match that commit. Compare the evidence before claiming an exact prior version. For a missing instruction, search its remembered wording, inspect the removal commit and relevant earlier text. For project evolution, use the project record ID and date bounds.

Older/external commits report `provenance: unrecorded`; absence of a declared ID match does not mean absence of history. Fall back to path/text queries and inspect the diff. Do not fabricate merge/split ancestry. Restore through an ordinary shown proposal and the current policy; INSTRUCTIONS uses its instructions procedure under the current independent approval policy. Historical data never enters ordinary vector search.

## Derived version index and lineage

Call `history_index` through the execute entrypoint; `history_lineage` uses query. Neither changes the permanent records or Git history.

`history_index(ref="HEAD", cursor?, limit=100)` incrementally indexes bounded pages of Git commit metadata, stable record versions, blob identifiers and declared transformations in SQLite. It returns the pinned `head`, continuation and coverage. Reuse that immutable head as `ref`; a changed/replaced head invalidates an incompatible continuation. This is a rebuildable metadata index, not another content authority. Stop indexing once the scoped evidence is sufficient; do not automatically exhaust years of history.

`history_lineage(material_id, direction="ancestors", ref="HEAD", since?, until?, max_depth=5, limit=50, start_commit?)` traverses indexed, context-qualified record versions and declared transformation edges. `direction` also accepts `descendants`. Inspect coverage and truncation: an absent edge does not establish absent history. Extend indexing only where needed, and confirm selected versions with native Git reads/diffs. Agent-supplied merge/split/replace declarations establish recorded provenance, not semantic correctness.

## Operation analytics

The Workspace operation journal is `<work_root>/core/analytics/events.sqlite` (legacy: `<work_root>/.pcw/.analytics/events.sqlite`); Core also has its separate operation journal. SQLite records parameters, result/error metadata, durations and content references. Git is the permanent-content version authority. New observations reference an exact Git commit/blob only when captured content matches; dirty or unversioned content is labelled as such and may not permit exact replay. Do not assume every call has a full file snapshot. Existing historical blobs and unmatched evidence remain available; they must not be deleted merely because newer evidence uses Git references. Drafts and imported host messages remain separate evidence.

`analytics_query` supports selected `workspaces`, task/cycle/import, `tool`, finite `since`/`until`, `instruction_revision`, `runtime_version`, `procedures_revision`, and bounded `limit`/`cursor` pages. Dates are UTC when no offset is supplied; `since` is inclusive and `until` exclusive. The cursor freezes filters and an insertion boundary. With no dates or task/cycle/import, the runtime defaults to the last seven days and freezes the upper bound. Report unknown timestamps and coverage; do not treat a first page as complete.

`analytics_report(max_events=1000, cursor?, ...scope)` summarizes one bounded page with operation/outcome/context/instruction/runtime groups and recorded context-use counts. Groups and usage describe that page; `totals` describe the complete selected scope. Partial/coverage fields disclose limits. Use the same frozen scope, narrow to relevant events, then inspect exact `analytics_read` evidence. `analytics_reference_read(event_id, reference_index=0, offset=0, max_chars=16000)` replays an exact Git-backed reference, respecting its original source-file/body/offset projection. `analytics_reference_read` with `reference_index=-1` reads the recorded instructions. `analytics_read` paginates reference/legacy-version metadata with offset/limit. `analytics_blob_read(offset?, max_chars?)` reads a referenced retained legacy body for unmatched historical evidence. A `started` event without a terminal record has unknown outcome. Compare file revisions before retrying a possibly completed action.

A historical assessment needs the instruction/procedure revisions actually recorded for that period and the recorded runtime/plugin version. Unknown revisions stay unknown: current rules cannot establish a historical violation. A procedure fingerprint does not itself retain procedure text; use the matching release/source or mark that evidence unavailable. Comparing eras is a different question from evaluating compliance within each era. Reading a record does not establish that the model used it, and a recorded declared link does not prove reasoning quality.

In legacy snapshot events only, `during_call` changes were observed across that MCP call under the plugin lock. `observed_before_call` changes were already present and have no established author; a first observation can be a baseline. External writers need not honor the lock, so observation is not universal proof of authorship. Git shows revisions, not every outside action. Missing token/cost measurements stay null. MCP validation failures before dispatch are outside the journal boundary.

Stored analytical values are sanitized and mark redactions. Credential-key, labelled-text, bearer, private-key and known environment-value filters cannot reliably recognize arbitrary unlabelled secrets. Never supply credentials as knowledge or analytical input. Analytics responses use measured byte counts to avoid recursive copies. Contextopus durations are not total task time; bytes are not tokens. Host usage and comparable control runs are required for claims about token savings, cost or causal improvement.

Import only a user-selected absolute file with `analytics_import`. `codex-jsonl` recognizes session metadata and public message/tool records; system/developer and analysis messages are excluded. Portable `pcw-session-v1` accepts JSON objects with `kind`, `timestamp`, `data`, optional `id`, and an explicit `session_id` argument. Other hosts can export this format without changing the core. Malformed/unsupported input reports an error. Imports retain source coordinates and stay unmatched unless independent evidence establishes a relation; timestamps do not identify actors.

`analytics_evaluate` stores an explicitly requested assessment, criterion, evidence event IDs, scope and instruction basis separately from objective events and knowledge. Analytics never enters vector retrieval. Analysis itself creates no authority to repair knowledge or instructions.

`analytics_migrate(limit=50, cursor?, apply=false, git_limit=50)` inspects a bounded batch of legacy snapshots. Application replaces only exact hash-and-redacted-text matches with Git references; it deletes a verified blob only when no event or other snapshot references it. Unmatched snapshots and legacy request/result/error bodies stay retained. This maintenance call is separate from ordinary analysis.
