# Contextopus skills, MCP and context organization update

Historical agreed task and acceptance record for **Contextopus 1.1.0**. Sections 1–6 define requested behavior; section 7 records actual implementation and limits. Historical [release notes](https://github.com/pikiby/contextopus-core/releases/tag/v1.1.0) describe delivery.

## Goal

Make Contextopus shared context infrastructure for multiple agents/hosts. Projects, knowledge, goals and task state must be searchable and linked. Restore central-MCP connections through each host's supported mechanism.

## 1. Skills

Keep `$w`; use concise `w-<action>` names. Consolidate to w, w-search, w-create, w-settings; route audit/rework/upgrade through w by intent. A later explicit request adds short w-analyze, with its single algorithm in MCP. Add a short semantic-project-discovery skill in the same naming style (w-find was a discussed option). Skills point to canonical procedures instead of copying full algorithms across hosts. Main w routes ordinary-language functional requests to the proper procedure. During updates remove recognized old managed commands, preserve user changes/foreign skills and report collisions. Acceptance requires actual client discovery of the compact skill/tool set and valid procedure calls, not merely files/manifests.

## 2. One central MCP and supported reconnection

One local HTTP owner serves concurrent agents with shared databases/models; closing a client session does not stop it. Design covers Linux/macOS/Windows, with Codex one client. Installation uses native per-user autostart and crash recovery. Keep explicit start/stop/status and add restart. No second owner of the same data/model. Main TUI displays actual state and can start/stop/restart, including when MCP is down. CLI/TUI/install share service management without needing the stopped MCP. Distinguish stopped, environment access denial, reachable server and disconnected client; HTTP denial alone does not mean stopped.

Server startup and reconnection of an existing client session are separate. First test automatic recovery; otherwise use the selected host's native integration. Sequence: server ready→client reconnect→tools discovered→control call through the target session. Installed Codex exposed config/mcpServer/reload via codex app-server proxy; this queues MCP reload across open sessions, beyond Contextopus. Verify recovery from failed initialization and impact on parallel sessions before accepting it. Expose reconnect only for supported hosts, with explicit limits. Independently establish methods for Codex, Claude Code, OpenCode, Crush and later clients; do not claim untested methods. MCP cannot control arbitrary clients; host/integration initiates recovery. Historical API reference: https://learn.chatgpt.com/docs/app-server#api-overview; validate the actual installed version.

Acceptance: concurrent clients, one-session closure isolation, declared-platform autostart/recovery with untested platforms labelled, stopped-server TUI start, initially disconnected session regains tools/calls without losing conversation, and survival of MCP restart. Health200 or a separate-process request is not target-session acceptance.

## 3. Unified typed Workspace records

Remove separate Current. Move its assignment, constraints, verified state/results, next step, blockers and status into knowledge under ordinary read/write/approval/index/link/history rules, preserving IDs and unfinished-work continuity. Replace Current-specific operations/procedure with ordinary record operations. Search must discover task state without a pre-known ID; IDs remain exact addresses.

```yaml
id: stable-record-id
type: task
status: active
links:
  - related-record-id
```

Types are open data: knowledge facts, task assignments/state, goal conditions, project current description, or additional nonduplicative user/agent categories. The agent checks existing categories and applicable rules before choosing/adding one. Context membership and record type are independent. Procedures recognize project purpose specifically without hardcoding a closed type catalog.

All types share RAG, links, revision/history and deletion handling. Context/type/status filters precede vector selection. Return ID/title/type/context and enough evidence to choose selective reads. Cross-type links connect task/goal/project/knowledge. Update derived index after edits/migration/external changes; saved bodies, links and retrieval must remain consistent. Searching another context changes neither active rules nor write destination. Maintain one indexed current project-description record without a separately synchronized catalog copy.

Procedures determine purpose/type; search/read existing relevant knowledge and work state before deciding update/create; preserve meaningful labelled links; distinguish fact/goal/assignment/plan/verified result; keep current meaningful topics rather than an action log; retain source/approval/context-rule independence; avoid duplicate full procedures in skills/references/user rules. Acceptance: ordinary search finds migrated work and all common operations work for every type, with no second live Current store.

## 4. Context discovery

Use the existing common root, folder catalog, operation registry and RAG, without a separate search engine/manual project registry.

| Operation | Required behavior |
| --- | --- |
| List contexts | Enumerate real folders with names/full paths; choose by explicit name/path; listing tracks actual folders. |
| Read project | Read the selected context's current type:project record without a work cycle. |
| Semantic project search | Filter type:project before vector retrieval; return context matches and explanatory excerpts. |

Register concrete operations in existing contextopus_query; discovery works before selecting Workspace and needs no new server. Known name/path→find folder→read selected project→authorized work; clarify multiple matches, do not load every description. Semantic request→short skill→project search→show matches. “Find” returns results; explicit unambiguous work request can proceed. Empty results do not authorize creation. Selection belongs to an agent's task, with no global switch across sessions and no guessed memory path from repository/cwd. Acceptance includes name/path lookup without embeddings, semantic lookup without loading all PROJECT files, fresh search after description edits, no context/rule/session mutations, and honest missing descriptions.

## 5. Existing-system migration

Inspect source records/rules/Current/PROJECT/active cycles/pending proposals. Move Current/project descriptions to common records preserving IDs/bodies/statuses/work continuity and consistent service/link addresses. Upgrade format/index and verify sources, typed search and cross-type links. Only then retire separate live stores; backups remain technical recovery, not parallel truth. Update skills/procedures/manifests/client integrations while preserving user modifications/concurrent work. Instruction changes follow current approval, never silent template replacement. Retrying/interruption must not duplicate/lose data; never apply pending user proposals or cancel foreign work for migration.

## 6. Execution and completion

Compare sources, installed runtime and real clients; clarify contracts without changing the agreed model. Implement records/filters/links/migration, then discovery/procedures/skills, then MCP service/TUI/reconnection. Verify each section and existing-install/data upgrade. Only within release authorization update packages/clients and test fresh plus existing-session recovery. Report sources, installed version, data migration and actual client behavior separately. Completion requires consistency across code/rules/storage/search/links/migration/delivery and per-environment evidence, beyond file presence.

## 7. Historical acceptance

Sources implemented six short skills, common typed records/filters, project discovery, format 3 migration, CLI/TUI central service management and host reconnection. Long MCP calls run in a worker thread so health checks remain responsive.

Runtime 1.1.0 installed. Fresh Codex discovered all six skills and made a real MCP read; other clients distinguish config validation, connection and model tool calls. Enabled user systemd recovered MCP after process termination. Installed TUI started the stopped server without logout/login. **22/27 contexts** migrated to format 3, with **117 task and 22 project records** searchable, preserving IDs/instructions/pending proposals/**25 cycle-state files**. Five old contexts stayed unchanged because approved instructions were absent; three also had old-ID conflicts. No guesses or work cancellation repaired these.

Codex proxy transport fixed to wsproto for the active WebSocket. Isolated same-transport tests used two initially disconnected sessions: reload restored calls in the same threads with nonempty history, and later MCP restart survived. Working session separately confirmed new operations after service restart. OpenCode API recovered after initial failure/restart and preserved history, without running a model turn or claiming model tool use. Claude Code/Crush retain their own documented recovery limits.

Wheel/sdist/four client ZIPs/SHA256SUMS validated before publication; installed code/resources compared to canonical sources. Historical [release1.1.0](https://github.com/pikiby/contextopus-core/releases/tag/v1.1.0) and [CI matrix](https://github.com/pikiby/contextopus-core/actions/workflows/ci.yml) record delivery and Linux/macOS/Windows Python3.12/3.13 wheel build/install. macOS/Windows autostart was configuration-structural testing, not native login/service recovery. No real GPU or model behavior in every host is claimed.

## Addendum: Git record history

Local Git history joins standard new-context choices; offer complete defaults first, then custom questions. Existing rules are not rewritten. Proposals include concise commit meaning and explicit merge/split/replace source/result IDs, approved with content and saved together. MCP offers bounded ID/path/date/text history, commit composition and version reads. Investigate applicable history when a condition disappears, state/rules regress or project evolution is asked about; old versions remain evidence, not current rules. This was implemented in sources/installed MCP and aligned project-update cards, with exact-snapshot/failure-retry/lineage/bounded-query checks.

## Addendum: Git versus SQLite and bounded analysis

Git stores durable text versions and declared transformations. SQLite stores events/errors/durations/verified Git references; a derived index speeds lineage. Calls no longer copy the whole context before/after. Historical text requires verified commit/path/extraction hashes, never replacement by current text. Successful service snapshots become small receipts after full-tree verification; pending snapshots retain exact retry data. analytics_migrate converts old copies in bounded batches without deleting unverified or still-used evidence.

w-analyze loads one MCP procedure. Set question/contexts/period or task/cycle/import before review; absent bounds default to seven days. Cursor fixes filters/insertion upper bound; report partial coverage explicitly. Instruction/runtime/procedure filters separate rule eras; assess past actions under then-current rules, leave unknown rules unknown. Tests cover exact old text/rules, other-context reads preserving active rules, no second archive when Git off, pagination, lineage, safe old-snapshot reduction and interrupted commit recovery. Read counts do not prove model use; token/cost comparisons need host data and comparable runs.

Installed locally2026-10-15. Per-commit CI logs record full tests/build/install/skips. Six assets and 84 wheel files compared. Current session queried the new bounded report and read a format 2 event; fresh Codex confirmed MCP/six skills. Two project-update cards updated and two related cards created/indexed. Existing project-update Git stayed disabled; new events correctly report no_history for exact rule reproduction. Historical release composition is in1.1.0 notes.
