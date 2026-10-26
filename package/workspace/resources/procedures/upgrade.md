---
operations: ["core.index_upgrade", "core.relocation_apply", "core.relocation_plan", "core.relocation_rollback", "core.status", "workspace.list", "workspace.proposal_apply", "workspace.proposal_prepare", "workspace.read", "workspace.status", "workspace.upgrade_apply", "workspace.upgrade_plan"]
---

# Upgrade PCW installation, data and skills

This is the canonical Workspace upgrade procedure. It covers the old installation, selected data roots, context formats and host skills. Fetch needed procedures through `guide(procedure=...)`; use `guide(operation=...)` only for unknown signatures. An upgrade does not authorize substantive changes to project knowledge or instructions.

## Resolve scope and preserve state

Establish the exact old installation, old common memory root, selected contexts, new persistent root and installed client scopes from the user's request and current configuration. A repository rename or current working directory does not select a Workspace. Enumerate only the named roots. Detect existing short-name skill collisions before changing a host.

Record versions, root paths and current context IDs. Inspect `workspace.upgrade_plan` for every selected existing context, including apparently current ones, and report unfinished task/state records (including legacy Current), tmp, pending proposals and approval state. Preserve material IDs, links, custom INSTRUCTIONS, PROJECT, Current, tmp, pending immutable proposals, approval records, control backups and Git history. Unknown/missing structures are blockers for inspection, never authority to create replacement contexts. Do not apply, discard or cancel somebody else's pending work.

## Install the standalone runtime

Obtain the exact verified wheel and SHA256SUMS from the authorized local build or GitHub Release. Install with `uv tool install --python 3.12 <verified-wheel.whl>`; add `--force` to update an existing tool installation, preserving its required extras. Install the optional `local` extra only when local inference is required and authorized. Python is managed by uv. No PyPI package availability is assumed.

Keep the old plugin cache intact for existing sessions. Do not uninstall the old Codex plugin or remove its marketplace with commands that delete caches. Stop writes to the old data root while conversion/copy runs; no source deletion is part of this procedure. Use the new Contextopus service bound explicitly to the existing old root for format inspection and upgrade. Do not run `init` on an old root to create a second empty layout. An existing running service must be verified to own the selected root before use.

## Convert context formats, then relocate the root

Apply the context-format procedure below for each incompatible recognized context. Preserve the returned revision and original snapshots. Already compatible contexts require no rewrite. Resolve blockers and index repair before treating format conversion as complete.

Relocate only when the user has requested a different persistent root. For an ordinary runtime update at the same root, skip relocation and retain its binding; restart the service to load the new package, then update and verify native clients.

Then call `core.relocation_plan(destination=<explicit new root>)` against the old root. Show its actual source/destination, preserved data and planned effects. The normal response is a bounded review summary with its revision, counts and manifest identity; Core retains the full plan in an owned durable artifact under `.contextopus-relocation-plans/`. Use `core.relocation_apply(plan_revision=<returned revision>)`; never resend a huge manifest or reconstruct a plan from memory. Core freezes writes, checks the source snapshot, copies and verifies persistent files, captures the latest known volatile observer databases coherently, and publishes a destination receipt. The source remains frozen and never redirects requests; explicitly select the destination and rebind/restart the service there. Validate the returned receipt, destination manifest and preserved pending work before cutover. Retry an interrupted relocation with the exact retained `plan_revision`, including after an owner restart. Before publication, `core.relocation_rollback(plan_revision=...)` unfreezes the retained source without deleting partial destination files. After publication, rollback means a new verified relocation from the active root; do not remove the source freeze or reactivate two writable roots. Legacy redirect markers also reject the old root instead of selecting another folder.

New relocation snapshots use `.backups/relocations/<snapshot-id>/`; retry the same retained plan to reuse that identity. Earlier snapshot trees are preserved as opaque archives. Existing plans and receipts keep their recorded paths; do not rename or delete their snapshots during an upgrade.

Use the destination explicitly for subsequent requests and run `core.index_upgrade` to add generic namespace columns to an existing index without embeddings. Verify the selected Workspace's full instructions, stable IDs/links, typed work-state records/tmp, pending approval revisions and history at the destination. Compare actual counts and representative preserved records with the source snapshot; a readable directory or health check alone is insufficient. Leave retained source/snapshots in place. Source deletion requires separate authorization.

## Update native clients and old skill entrypoints

Install native per-user autostart with `contextopus --root <new root> service install`, then verify the single owner. `service start/stop/restart/status` and the TUI use the same lifecycle controls. Bind the persistent default with `contextopus root --set <new root>`; also pass the exact destination during rollout verification. Do not infer a shared Workspace selection from a process/session ID.

Choose and preserve one Codex delivery mode. For literal `$workspace`, `$workspace-search`, `$workspace-create`, `$workspace-settings`, `$workspace-context`, `$workspace-analyze`, `$m`, use the existing direct integration:

```console
contextopus integrate codex --user
contextopus integrate codex --user --apply
```

Before switching from plugin delivery to direct integration, set only the known
`plugins."persistent-context-workspace@persistent-context-workspace".enabled`
and `plugins."contextopus@contextopus".enabled` declarations to `false` in the
native Codex TOML configuration, preserving unrelated values and both caches.
Do not uninstall plugins to achieve this. The direct integration supplies one
central HTTP MCP server and seven bare user skills. Verify these exact bare names
in a fresh native session. Updating this installation uses the same direct mode;
do not turn its plugin back on as an automatic upgrade step.

Optional plugin delivery exposes `contextopus:w` and corresponding namespaced
skills, rather than literal bare `$workspace`. If that mode is explicitly chosen, use
supported native commands after checking the installed CLI's help:

```console
codex plugin marketplace add <new source checkout>
codex plugin add contextopus@contextopus
contextopus integrate codex --user --native-plugin
contextopus integrate codex --user --native-plugin --apply
```

The final step disables the old plugin declaration for fresh sessions, removes its obsolete marketplace declaration and any matching duplicate manual Contextopus MCP entry, enables the installed native Contextopus plugin, and preserves both old cache files and unrelated configuration. It refuses cutover until the new plugin cache exists. Existing loaded skills/session tools may remain old until a fresh session; do not claim those were replaced in place.

For manually installed native skills/configuration, preview `contextopus integrate <client> --user --legacy-skills <explicit old skill directory>` (or `--project <directory>`), inspect affected paths, then repeat with `--apply`. Recognized unchanged retired skill files are removed. User-modified/unrecognized files are preserved and reported separately; preserve supporting files and do not delete nonempty directories. Previously managed unchanged skills can be updated through their recorded hashes; stale previews are refused. Plugin caches are never edited through this option.

Avoid duplicate skill copies across roots a client already discovers. For example, Claude's user skills are also discoverable by OpenCode and Crush: after installing that shared copy, use `--config-only` for those clients. For Codex choose either enabled plugin skills plus its MCP, or bare user skills plus manual MCP; do not enable both copies. If switching modes, review previously managed user skills before removing them and never overwrite user edits. Genuine JSONC comments/trailing commas require the client's supported editor rather than destructive rewriting. Preserve unrelated MCP servers, settings and credentials, and never print whole configurations.

Verify each actual fresh client connection: tools/list and a read such as `guide(procedure="upgrade")` or `core.status` through that client. File presence, direct Python checks and the old session's working tools are not this proof. Verify that new requests reach the destination root and new process, while the old cache remains available. Report per-client verification separately and list remaining blockers honestly.

## Context format procedure

1. Resolve the user's exact context, list of contexts or bounded directories. Follow [context selection](procedure:workspace). Enumerate only those directories, never the whole computer.
2. Call `upgrade_plan` for each selected existing context. It returns compatibility, the source revision, technical changes, saved unfinished work, blockers and `project_description`. If a recognized never-initialized legacy context lacks INSTRUCTIONS, provide the exact human-reviewed full `instructions_content` to both plan and apply, together with the actual `instructions_confirmation` on apply. Never infer missing rules from old goals or substitute bundled defaults without approval. Lost rules from an initialized context remain a restoration blocker. Inspect these even when the format is already current; compatibility does not mean that work is finished or project content is complete. Explain each unfinished work item using its request/task and material titles; distinguish prepared proposals from applied changes awaiting closure. An open record does not prove that an agent is running. An unknown structure requires inspection/restoration; do not guess its version. An already compatible format requires no rewrite merely because the plugin was updated.
3. Show affected paths and explain that the snapshot retains the entire workspace, including tmp, plus its control state. Format 2 places stable IDs in YAML, gives knowledge files descriptive names, converts legacy source blocks, and indexes them in the common category-aware RAG. Compatible unfinished work keeps its IDs, immutable proposals and approval states; the migration retains technical path/format mappings for later application. Do not apply, discard or cancel that work. If `ready` is false, describe the exact blocker and affected work. Otherwise call `upgrade_apply(expected_revision, user_request)` for a direct request, or `upgrade_apply(expected_revision, automatic=true)` for detected incompatibility. Do not invent a user request or approval. Use `resume_revision` for a pending migration.
4. New migration snapshots are stored in the hidden control directory `.backups/<migration-id>/` (workspace and control copies). Existing receipt paths, including legacy `backups/`, remain authoritative for recovery; do not rename or rewrite them as part of an upgrade. Report verified format, backup paths, preservation of unfinished work, project-description status and any pending index repair. `missing`, `empty` or `placeholder` means the project record still needs content; state that explicitly. `present` does not establish accuracy or completeness. Retry interrupted migration using its original revision and retained snapshot. Source conflicts require inspection. Format completion requires both file conversion and successful shared indexing; `index_pending` is not completion. Resume the original Contextopus operation after success, under its original active context's instructions.
5. Preserve the complete custom INSTRUCTIONS free-form body and unrelated frontmatter verbatim; move only recognized machine policy keys into Core configuration, retaining effective values. A new default template does not authorize replacing user rules. Reclassifying old goals, splitting mixed knowledge and revising instructions are separate requested rework/instructions procedures. Do not turn format migration into substantive editing.

If the user also requested filling the project description, follow the [work cycle](procedure:workspace): read workspace instructions and relevant existing knowledge, verify missing facts from permitted project sources, then prepare the supported description with `proposal_prepare` and `type: project`. Show its actual text and apply through `proposal_apply` under the workspace approval policy. Do not invent goals or apply another task's pending proposal. Otherwise report the unfilled description as remaining content work.

## Unified records (format 3)

Current is no longer separate storage. Upgrade migrates every task ID, field and body into typed searchable records, and PROJECT into the canonical project record. Existing knowledge receives a type where absent. The shared index, links and technical proposal paths are reconciled together. Pending original proposal/approval documents stay immutable; their technical overlays do not approve or apply them. Resume old project proposals through ordinary `proposal_apply` using the original revision and required confirmation. After verified migration old Current/PROJECT sources exist only in recovery snapshots, not as competing active files. A missing project description remains missing; do not fabricate project content. Preserve custom INSTRUCTIONS and use the instructions procedure under their current approval policy for substantive rule edits.

## Direct 1.1 or 1.2 to 1.3

Package releases and Workspace format versions are separate: 1.3 introduced format 4; 1.4 introduces format 5. Apply all required format transitions directly; an intermediate 1.2 installation is not required. Preview/preserve policy values, free-form rules, YAML IDs, graph endpoints, required sources, descriptions, history, cycles, tmp, immutable proposals and approval revisions. Test the same transition and idempotent/interrupted retry on a coherent copy before live cutover; restore package, data and client configuration together on rollback. Do not run old code directly against incompatible converted data.

Format 4 moves machine flags into Core configuration and makes Obsidian navigation explicit while retaining stable YAML identity. Inspect unresolved/ambiguous links rather than inventing targets. Description gaps require a separate author-reviewed repair proposal from actual content; a format-ready context can still have missing descriptions. Do not overwrite custom rules with defaults or cancel pending work. A non-equivalent pending proposal blocks only its apply with a reprepare path; old approval does not authorize different meaning.

After an authorized package update, refresh managed skills and the used procedures, restart one shared MCP owner, and verify guide/read/write/finish plus a real context search/read from each targeted actual client. Compatibility aliases are explicit temporary CONTEXTOPUS_LEGACY_TOOLS=1 mode, not the default tool inventory. Published artifacts, installed package, running process, discovered skills/tools and successful client calls are separate checks. Keep evidence tied to the same release; do not infer model compliance from textual rules or unit tests.

## Optional temporary memory (format 5)

The upgrade plan detects normalized context-name collisions and permanent references to temporary data as explicit blockers. Do not silently merge contexts or remove useful information to make migration pass. Preserve instructions, six existing policy choices, user tags, original cycles and immutable proposal/review bytes. The new independent cleanup_approval_required setting defaults to true where absent.

Convert tmp to .tmp and temporary Markdown to readable cards with stable YAML IDs. Rewrite temporary references and retained path mappings; a pending permanent proposal keeps its original approval revision, with technical overlays applied only when resumed. Add owner-derived context/<name> tags while preserving unrelated tags. Check both saved card metadata and the shared index. Retry the retained migration receipt after interruption; keep snapshots until preservation is verified. No old work is applied, cancelled or purged by the format transition.


Canonical layout: the indexed project record lives at the context root as `Context.md` (`type: project`). Its YAML `instructions` mapping binds the complete shared rules in Core (`core/instructions`), including Workspace and optional Agents rules. Startup returns every required document in full; follow `reread_instructions` to reuse text already available for the same task. Existing roots move to this layout only through the explicit reviewed upgrade plan/apply path with retained backups.
