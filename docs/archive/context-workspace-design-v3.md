# PCW: managed project memory — target architecture

> Historical document. Personal paths and trace/material identifiers have been anonymized; measured results are preserved.

For shared RAG, categories, YAML IDs, sources and migration, see [v4](context-workspace-design-v4.md). This document records the agreed 0.11.0 design and subsequent interface clarifications, not the current Workspace contract. Implementation evidence is in the [plan](context-workspace-upgrade-plan.md), [goal](context-workspace-upgrade-goal.md), [verification](context-workspace-v3-verification.md) and [API contracts](context-workspace-v3-contracts.md). [v2](context-workspace-design-v2.md) describes 0.10.0. Editing source does not update an installed plugin.

## 1. Purpose

PCW is portable project memory accessed through MCP. It retains accurate findings, established principles, patterns, procedures and explicitly accepted decisions with their grounds. An agent retrieves accumulated context before repeating an investigation and corrects obsolete information. The intended benefit is less repeated investigation and less unnecessary context, not a report of every action. Approval of a hypothesis does not make it a fact.

PCW neither defines the agent environment nor selects or launches worker models. The assignment and host instructions govern delegation. Memory does not require a native session ID.

From 0.12.0, two MCP tools expose the operations: `pcw_query` for reads and `pcw_execute` for changes. `operation` selects the operation, `arguments` contains its parameters, and `work_root`/`workspace` remain separate. `pcw_query(operation="describe")` supplies descriptions and schemas on demand instead of publishing dozens of tools. Validation precedes storage access; dispatch retains per-operation checks and logging. This interface change does not change the workspace format.

## 2. Data separation

Historical target layout:

```text
<work_root>/.pcw/<workspace>/
  INSTRUCTIONS.md       # user rules and settings
  PROJECT.md            # purpose, goals, requirements and boundaries
  current/<task-id>.md   # state of an individual task
  knowledge/<id>.md     # coherent topics and links
  tmp/<cycle-id>/       # working results and proposed changes
  .pcw-runtime/         # derived vector index and configuration
  .git/                 # optional permanent-data history

<work_root>/.pcw/.control/<workspace>/
  ...                   # operation state and conflict protection
  format.json           # format version and migration state
<work_root>/.pcw/.analytics/
  events.sqlite         # observed PCW operations
```

New service filenames and API names here specify the intended contract. In this version, a material's filename remains its ID: moving it or changing its title preserves identity. Moving IDs into YAML had been excluded from this particular update; later designs superseded that decision.

| Data | Use and modification |
| --- | --- |
| INSTRUCTIONS | Read completely before work; sole authority for project rules. Changes require a direct request, presentation and approval of the revision. |
| PROJECT | Read on entry; derives from the user's purpose and boundaries. Agent-written revisions are presented first under INSTRUCTIONS. |
| Current | Read only for the selected task; stores assignment, constraints, verified state, next step, blockers and status. Update after substantive changes while preserving the assignment's meaning. |
| Knowledge | Search and selectively read; maintain current descriptions under workspace policy rather than accumulating reports. |
| Tmp | Supplementary cycle inputs/results, without the authority of approved rules or knowledge. Excluded from ordinary search and memory Git history. |
| Journal and Git | Evidence and former revisions, retrieved for a bounded analysis rather than mixed into ordinary vector search. |

This historical design does not duplicate PROJECT, INSTRUCTIONS or Current in knowledge and does not embed them. INSTRUCTIONS defines their maintenance; code enforces technical checks.

## 3. Selecting and creating a context

Preserve the user's `work_root` and `workspace`. A new question, changed working directory, a single discovered folder or empty search does not authorize creation or switching. Ask if the selection is lost or ambiguous. Concurrent sessions do not share one global selection switch.

Opening and creation are separate. Reads and ordinary startup do not create missing storage. `workspace_create` runs only after the creation procedure obtains a selection, checks that the destination is absent and makes retries idempotent.

`pcw-create` accepts a location and purpose in ordinary language. If placement is unknown, offer concrete existing choices with full paths or accept the user's path. Ask only for settings not already supplied:

1. May the agent initiate context updates implicitly?
2. Must knowledge changes be approved?
3. Enable automatic Git history of permanent data?
4. Material limit: **1024 tokens — recommended**, **another limit**, or **unlimited**. Ask for a number only when the user chooses another unspecified limit.

The proposed default is **1024 body tokens** from the INSTRUCTIONS template. Five complete materials can therefore require up to 5120 tokens before metadata. This is a practical starting point, not a measured optimum or a required record length. Silence and a UI's preselected option are not answers. Plugin upgrades do not replace existing settings. Present the initial INSTRUCTIONS and PROJECT, then create using those agreed values; do not create an intermediate workspace with incorrect settings. Creation does not start project execution, and `current/` starts empty.

## 4. Reading and updating have different entry conditions

`pcw-search` supports independent retrieval without a work cycle: open an existing context, read its rules, search, read relevant topics and follow useful links. It creates no tmp, Current task, proposal or duty to finish a cycle.

Instruction/project/metadata reads, search and material reads therefore accept an omitted `cycle_id`. Missing workspaces fail rather than being created. Reads do not silently rebuild indexes or migrate. An analytics event may record the call without modifying knowledge/settings.

INSTRUCTIONS governs implicit initiation separately from write approval:

| `allow_implicit_updates` | `knowledge_approval_required` | Behavior |
| --- | --- | --- |
| false | true | Start on request; present and wait for approval. |
| false | false | Start on request; present and apply without another question. |
| true | true | May begin review implicitly; wait for approval before applying. |
| true | false | May begin review, present and apply. |

Missing permission retains the prohibition on implicit updates; an absent legacy approval setting retains confirmation. Reject invalid values. Implicit updates do not permit switching context, changing instructions or expanding the assignment. Code checks policy and revisions, but a model-supplied parameter alone cannot prove that approval came from a human.

## 5. Main cycle and additional procedures

```text
Select the procedure and established context
  -> read INSTRUCTIONS, PROJECT and relevant Current
  -> find and read relevant knowledge
  -> perform the assignment; save working results in tmp
  -> compare results with knowledge under current rules
  -> prepare and present changes or a grounded no_changes outcome
  -> apply under policy, complete review and close the cycle
```

Start through explicit invocation or the configured implicit-update policy. One-time authorization does not become permission for unrelated later work; saved settings remain effective until changed.

| Request | Procedure |
| --- | --- |
| Find information | `pcw-search` |
| Create a context | `pcw-create` |
| Change rules | `pcw-edit-instructions` |
| Upgrade storage format | `pcw-upgrade` |
| Inspect knowledge quality | `pcw-audit` |
| Transform selected knowledge | `pcw-rework` |
| Analyze memory use | `pcw-analyze` |
| Work using memory | Main `pcw` cycle |

The skill defines the procedure, INSTRUCTIONS defines project rules, and MCP executes operations. Load only the relevant procedure; searches inside a cycle do not reload the search skill. Finding a problem does not authorize a new procedure; a compound request such as “inspect and fix” can authorize both.

Code enforces mode, revisions, links and apply policy. Audit has no knowledge-apply route. A designated clean-context subagent may lead the whole cycle and read its own rules/knowledge; the main agent relays questions, results and actual user approvals. Ordinary workers return findings to the cycle lead for tmp.

Result review is required, but a permanent write may be unnecessary. Present actual changed text and links in the conversation language; a file link, UUID or topic list is insufficient. Pending approval/application/indexing and unreviewed results keep the cycle open. In this historical design, startup/finish clear only the addressed cycle's tmp; apply does not clear it, other cycles remain intact, task Current survives, and finishing a resolved cycle requires no separate closing question.

## 6. Materials and navigation

A coherent material answers one independent question. Sections normally needed for different queries justify separation. Preserve conditions, grounds and limits. General and specific topics may link; size alone does not define coherence.

Compare conclusions with existing topics, prepare complete final materials and links, and submit one coherent create/update/delete/merge/split proposal. For a large existing topic, read it completely, identify independent questions, retain conditions, decide the original ID's destination and check incoming links. Truncation is not consolidation.

When enabled, `proposal_prepare` counts each final body with the index tokenizer and reports offending IDs, measured sizes and limits before writing. The value and mode are configurable; size validation cannot find semantic boundaries. Agents split topics by meaning; code divides finished text into overlapping token windows for embeddings. Qwen remains the default, with replaceable models/APIs; no separate semantic-splitting model is introduced.

Reads return a complete selected material. Link metadata includes IDs and useful destination labels from the saved title and description, authored with the topic rather than generated on every read. Labels use normal material preparation; neighboring bodies are loaded selectively. Automatic recursive expansion and graph search are excluded. Optional `from_id` records a declared traversal along an existing link; that event does not prove correct use of the content.

## 7. PROJECT and task state

PROJECT holds purpose, goals, requirements and boundaries. Current has one file per task, not a shared session state file. PCW issues `task_id`; it is not derived from an agent session ID.

Planned contracts: `project_read/update`, `current_create/list/read/update`. Assigned work creates Current; independent retrieval does not. List compact summaries and read only the selected task. Resume a known task ID or clarify ambiguity. Empty knowledge search does not create a task.

Updates validate `expected_revision` under lock. Refresh Current after a substantive result, blocker, assignment clarification, next-step change or status change. Store current state, not every call. Closing a cycle differs from finishing a long task: another cycle may resume the same task ID. Changing PROJECT does not automatically rewrite prior tasks.

## 8. Git history

Git is included in proposed creation defaults, which users may accept together or customize. Enabled history creates an initial snapshot, then one commit per completed permanent apply; empty comparisons create none. Knowledge, INSTRUCTIONS and PROJECT are included; tmp, index and analytics are excluded. Current is not a substitute for knowledge history.

`history_list` filters by material ID, path, message and dates; `history_search` finds addition/removal of exact text; `history_changes` shows changed files and declared transformations. `history_diff`/`history_read` return bounded old text. Commit trailers retain material IDs, operation, cycle and declared merge/split/replace relationships, including entirely rewritten text. Similarity cannot replace declared provenance; older commits lacking it are identified accordingly.

Use history to investigate contradictions, changed requirements, origins or restoration. Old versions are neither indexed as current knowledge nor treated as current rules. Restore through a presented proposal under the current policy.

Data save and Git commit have separate statuses. A failed commit retries the captured already-written snapshot; its exact contents/revisions are retained under lock so another agent's later changes cannot enter the earlier commit silently. No remote push is implied.

## 9. Operation analytics

Plugin code records MCP calls in one SQLite journal per work root, distinguishing workspace, cycle, task and operation. The agent need not separately log every action. Analysis may include selected other cycles and imported sessions.

Record call time/procedure/tool/parameters/outcome/error/duration/completion; touched material IDs and before/after versions, paths and retained text references; returned link destinations and declared `from_id`; operation/commit/path/diff references; optional host transcript coordinates; and separate analysis judgments linked to evidence.

Large bodies may live in hashed artifacts referenced by SQLite. Hashes alone cannot restore content: retain text or its Git revision. Remove secrets before recording and mark omissions. Record operation start separately from completion. A crash between file write and outcome leaves an uncertain result requiring revision checks; SQLite and filesystem writes are not one atomic transaction.

The MCP journal cannot observe omitted calls, direct file edits or final answers without host history/file snapshots. Git does not prove authorship or retain all uncommitted intermediate states. Import matches events only with evidence, leaving unmatched events explicit. Bound reports by contexts/tasks/cycles/tools/time; compare durations, errors, repeated reads, transferred volume and paths to application. Do not invent missing token/cost measurements. Analytics remains outside knowledge and is read for an analysis task.

## 10. Upgrading old contexts

Format version is separate from plugin version. Check the selected context on opening; plugin changes without format changes do not rewrite memory.

`pcw-upgrade` determines supplied transitions, presents affected files, preserves a recoverable snapshot, applies and verifies transitions, then records the new version. Retry resumes interrupted work. Infer an unversioned format only from verifiable features; do not guess.

The plan reports compatibility, exact unfinished work and blockers. Preserve compatible active cycles, tmp and proposals with their IDs, revisions and statuses. Upgrade neither cancels work, applies drafts nor closes cycles. The snapshot includes workspace and separate cycle control data; verify integrity before version marking. Incompatible/corrupt state requires recovery. Task review is distinct from format migration.

Batch mode is limited to user-selected roots/contexts, never a hidden machine-wide scan. An incompatible open reports the necessary procedure; search/audit do not migrate internally. Code preserves knowledge, IDs, links and settings during technical migration. Splitting old Current or mixed content is a separate reviewed semantic rewrite. New defaults never replace custom rules. Old template/custom revision/new template can inform a proposal, applied through `pcw-edit-instructions`.

## 11. Audit and rework

`pcw-audit` inspects a selected scope without changing permanent data. Findings identify material, revision, exact fragment, problem and basis: a rule, conflicting material or missing link target. Separate verified errors from hypotheses. Another agent may reuse the report after checking revisions.

`pcw-rework` performs the requested transformation: delete selected information, compact, retain relationships, expand a relationship or align knowledge with new instructions. Enumerate the full affected scope; “all information” cannot be limited to vector top-N. Use ordinary proposals with incoming-link handling, presentation and policy checks.

Audit does not automatically fix findings; changing INSTRUCTIONS does not automatically rewrite knowledge. Investigate authorized sources when more evidence is required. Code checks format/links; the agent performs semantic review using current rules and evidence.

## 12. Accepted boundaries

Included: coherent materials with configurable limits, labelled links, independent reading, separate initiation/approval policies, configurable creation, PROJECT/task Current, Git history, SQLite analytics, migration, audit, rework and procedure routing.

Not selected: JSONL/DuckDB instead of SQLite, mandatory external OpenTelemetry, automatic neighbor loading, a global agent-session registry, derived contexts or specialized database views. They are not implicit plan items.

The basis is the private design discussion and accepted link, migration, audit, rework and skill decisions. The latest explicit acceptance governs the material limit; an earlier rejection is not represented as current. New parameter/method names specify intended implementation rather than a previously released API.

## Required external sources

Creation separately asks whether to author links requiring fresh verification, saving `create_required_sources` in INSTRUCTIONS and including the standard authoring rule when accepted. Existing contexts are not rewritten automatically.

The historical `required_sources` body block contains an exact URL and bounded `extract` question. Every receipt of the material requires fresh access to each marked source, even within the same task and regardless of the authoring setting. Retrieve only the answer and necessary conditions, not the entire site. Ordinary citations/topic links do not invoke this procedure. Mark unavailable evidence unverified. The agent performs retrieval; the plugin stores the block without making HTTP requests on its behalf.

## Memory-use self-analysis

`pcw-analyze` examines selected work using PCW events and supplied host history. It presents evidence of search, reading, traversal, changes and errors with observation limits. Reading does not prove use. Analysis neither changes knowledge nor opens a work cycle and is not a substitute for a separate comparative benchmark.
