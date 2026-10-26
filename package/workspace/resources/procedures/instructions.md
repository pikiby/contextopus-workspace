---
operations: ["core.instructions_list", "core.instructions_read", "workspace.clarification_add", "workspace.cycles_list", "workspace.finish", "workspace.instruction_bind_prepare", "workspace.instructions_apply", "workspace.instructions_prepare", "workspace.instructions_read", "workspace.preparation_read", "workspace.project_read", "workspace.proposal_apply", "workspace.proposal_discard", "workspace.read", "workspace.settings_apply", "workspace.settings_preview", "workspace.settings_read", "workspace.startup", "workspace.status"]
---

# Change rules or machine settings

Obtain this procedure before editing rules/settings. Follow the ordinary Workspace context-selection and complete-rule reading requirements; an edit request does not create or switch a context. Preserve all unrelated user text and settings. A task result or inferred preference does not initiate an edit. Use `read` / `write` and stable `workspace.*` operation names; obtain only unknown signatures through `guide(operation=...)`.

## Common instruction authoring

Workspace owns creating, editing and binding instructions for a selected context,
regardless of their subject. Agent routing rules follow this same procedure; they do
not become module-wide Agents settings. Core stores the shared instruction documents.

1. Identify the intended document and its binding in Context.md. Read its complete text
   and required instructions before editing. Reuse a suitable shared instruction rather
   than copying it for every context. Bindings determine where it is used. Do not put
   work_root, absolute installation paths or "only this context/host" restrictions into
   reusable rules; preserve explicit user-requested conditions separately.
2. Represent relationships with direct Obsidian links to existing Contextopus files:
   instructions, knowledge, teams, profiles and skill cards use the same convention.
   Read each target through its owning enabled module and use its actual file path
   relative to the Contextopus root. Do not infer filenames from titles or IDs.
   A skill invocation, bare name, ID or external filesystem path is not an internal
   record link. A referenced cataloged skill is linked through its card, which identifies
   the external SKILL.md and package. Do not copy linked instructions into the new rules.
3. Keep conditions, priorities and exceptions separate from link targets, in a Routing
   section when routing is needed. For example, when the named files exist:

   ```markdown
   ## Related records
   [[modules/agents/data/teams/kubernetes]]
   [[modules/agents/data/profiles/kubernetes-quick-check]]

   ## Routing
   For a bounded read-only Kubernetes check, select the linked team's quick_check role.
   Supply the target environment and permitted access procedure.
   ```

   YAML instructions mappings contain required instruction documents. Ordinary links
   to teams, profiles, skill cards or knowledge do not become required instruction
   dependencies or authorize their execution merely by appearing in the text.
4. Include only the requested rules. For agent work these may define routing, delegation,
   limits, coordination and acceptance of evidence. Keep executor prompts/settings in
   profiles and membership in teams. Do not invent models, concurrency limits, permissions
   or host-specific launch requirements. Native actions remain the agent's responsibility
   through its host tools; Contextopus manages its own records and relationships.
5. Verify each new or changed link against the intended existing target and check named
   roles or capabilities through the owning module. Obtain another module's procedure
   only when it is installed and needed. Instruction role names do not require modules.
   Do not call unavailable operations or invent links to missing records; create missing
   records only within the user's authorized scope. Distinguish verified file links from
   links whose navigation has actually been verified in the web interface.

Use the save or binding path below under its current approval policy. After applying,
read the full resulting rules and verify the intended Context.md binding and links.

## Machine settings

Machine policy values live in the existing Core configuration, separately from free-form INSTRUCTIONS: allow_implicit_updates, knowledge_approval_required, instructions_approval_required, cleanup_approval_required, git_history_enabled, material_max_tokens, create_required_sources, and reread_instructions. `workspace.settings_read(fields=[...], agent=..., agent_model=...)` returns only requested effective values and their origins; explicit agent/model profiles can change rereading only and grant no permissions. Default rereading is false. Existing required sources are still checked anew on every receipt regardless of authoring settings.

For a direct settings-change request, read the affected values, call `workspace.settings_preview(changes, user_request)` through `write` to retain the immutable review across restarts, and show the actual before/after values and resulting effects in chat. Preserve false/0/null where meaningful; null material_max_tokens disables its limit. Use the returned current approval_required. When true wait for actual user approval of that exact preview revision, then call `write` with `workspace.settings_apply(preview_revision, confirmation)`; when false apply after showing without another prompt or fabricated confirmation. Disabling approval in the proposed settings does not waive the current policy. On a conflict read and prepare/show a new preview. Do not bypass policy through generic Core settings/request overrides, rule frontmatter or direct files. A settings-only change does not need a knowledge cycle; continue any already authorized work under the resulting rules.

## Complete free-form rules

This path replaces the currently bound Workspace instruction's text. To attach a
different instruction or create one for a selected role, use the binding path below.

1. Reuse an active applicable cycle; otherwise inspect `workspace.cycles_list` and start `workspace.startup(request_id, purpose="instructions", user_request=...)` with the exact request. Retries reuse the request ID. Read the complete current required instructions and its revision, including rules applying across phases and delegation. Do not segment or summarize it automatically.
2. Compose the full requested replacement, preserving unrelated text and unrelated metadata. Machine policy flags belong in the settings procedure above. `workspace.instructions_prepare(cycle_id, content, expected_revision, user_request)` prepares only a draft; reuse proposal_id when revising it.
3. Show actual review_changes in chat: affected before/after rules, additions and removals. A link or summary does not replace text. Questions/corrections keep review open; prepare and show each revision again.
4. Apply the exact returned proposal_id/revision under the **current** approval_required policy. When true wait for actual user approval and pass their confirmation to `workspace.instructions_apply`; when false apply after showing. Silence, old approval, self-approval and automatic tool approval are not user confirmation. A concurrent rule change requires rereading, repreparing and showing again. Retry partial writes with the same proposal; saved text, indexing and Git are separate outcomes. After success read and follow the full new rules.
5. A no_changes draft needs no approval/apply. Discard rejected drafts through proposal_discard. Resolve a standalone instructions cycle with the dedicated finish under cleanup_approval_required; it needs no knowledge proposal. Within an existing work cycle resume that task and its result comparison. Only actual cancellation permits cancel=true.


## Bind an instruction to the context

Use the attached operation definitions. The agent chooses the role and instruction;
role names are local labels and do not require an installed module.

1. Read `workspace.preparation_read` when the current rules are needed. Reuse an
   applicable work cycle or use the ordinary `workspace.startup` path with purpose=work.
   Obtain the current Context.md revision from `workspace.project_read`.
2. For an existing document, select its ID through `core.instructions_list/read`.
   For a new document, author its title, description and full content. Call
   `workspace.instruction_bind_prepare` with cycle_id, role, instruction_id and
   expected_revision plus expected_task_revision from the assignment used to prepare
   the binding. Record relevant clarifications first and reconsider the draft after
   a task_changed response. For creation also supply new_instruction with path, title,
   description and content. Reuse proposal_id when revising an unapplied proposal.
3. Show returned review_changes. Follow next_call under approval_required: when
   required, obtain actual user confirmation of the exact revision. Applying creates
   any new instruction and updates Context.md's binding together in the same recoverable
   proposal. Other bindings and the project body are preserved. No manual YAML step follows.
4. A partial or index_pending result is unfinished; use the returned retry call after
   addressing its error. The same proposal resumes without recreating completed files.
   Read the full resulting instructions. Keep the active cycle if its task continues.

Canonical layout: the indexed project record lives at the context root as `Context.md`
(`type: project`). Its YAML `instructions` mapping binds complete shared rules in Core
(`core/instructions`). Startup returns every required document in full; follow
`reread_instructions` to reuse text already available for the same task. Existing roots
move to this layout only through the explicit reviewed upgrade plan/apply path.
