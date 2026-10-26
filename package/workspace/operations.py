"""Workspace operation metadata is the canonical interface contract."""

import inspect
from typing import get_type_hints
from pydantic import ConfigDict, Field, create_model
from .cycles import CycleRuntime

_RETRIEVAL_PRECONDITIONS = (
    "Read the applicable Workspace procedure; this operation signature does not replace it. "
    "For a new task, obtain the effective reread_instructions policy through workspace.settings_read "
    "and the complete current required instructions together through workspace.preparation_read, or through "
    "the procedure's preparation path "
    "(workspace.startup for work, workspace.instructions_read for independent retrieval). "
    "Reuse complete current rules for the same task/context only as that policy allows; "
    "false never skips the first full reading. "
)
_FIRST_SEARCH_SETTINGS = (
    "For the first search, inherit the selected context's algorithm and limits: omit overrides "
    "unless the user explicitly supplied them. Later changes need an observed task-relevant limitation. "
)

_DESCRIPTIONS = {
    "preparation_read": "Read effective rereading/implicit-start policy, complete required instructions and Context.md in one read-only operation. Opens no cycle and creates no files. Reuse the complete returned rules within the same task according to policy; this does not attest model understanding.",
    "instruction_bind_prepare": "Prepare a complete instruction binding in Context.md. The agent chooses role and instruction_id; Core resolves exact links and dependencies. Preserve all other bindings and project text. Supply Context.md expected_revision and expected_task_revision from the assignment used; reuse proposal_id when revising. Optional new_instruction supplies path/title/description/content authored by the agent; its creation and binding are reviewed and retried as one proposal. No Agents or other optional module is required. Returns review_changes and the exact proposal_apply next_call under existing approval policies.",
    "reserve": "Before preparing an edit, atomically reserve its existing material IDs for this active cycle and give a one-line reason. Reading remains available. Returns reservation IDs, owners and expiry; conflicts include the other cycle's reason. Default TTL is 900 seconds, maximum 3600. Reusing a live reservation does not extend it. Related records should be reserved together; proposal_prepare includes automatic link effects. No semantic reservation of a new topic is implied.",
    "reservations": "List active edit reservations in this context, optionally scoped to a cycle, without opening a work cycle. Expired ownership is omitted; no files are modified.",
    "reservation_renew": "Extend one still-live reservation owned by this cycle. Expired/replaced reservations cannot be revived; reread materials, reserve again and reprepare instead.",
    "reservation_release": "Release only this cycle's exact reservation ID when its edit is no longer needed. Does not delete files, discard proposals or change knowledge. Prepared proposals bound to the released reservation must be prepared again.",
    "settings_read": "Read selected effective Workspace policy values and origins without loading instructions or project. Explicit agent/model affects rereading only.",
    "settings_preview": "Review requested machine-setting changes under the current instructions approval policy; show exact before/after values and changes before apply.",
    "settings_apply": "Apply the exact reviewed settings revision with actual confirmation when current instructions approval requires it; never bypass through generic Core settings.",
    "workspace_inspect": "Read the selected existing workspace format and compatibility, without creating or migrating it.",
    "workspace_create": "Only after an explicit creation request: create the exact context with the separate machine settings, complete free-form INSTRUCTIONS, project text and author-written project description agreed with the user through w-create. First offer the packaged defaults as one set; ask individual unanswered settings only if the user customizes. Offer all current policy settings explicitly; legacy six-setting callers retain the independent cleanup default. A repeated request_id with identical input resumes safely, including accepted older requests; existing contexts are not replaced.",
    "upgrade_plan": "Preflight the selected existing context, including already-current formats: return compatibility, source revision, technical changes, unfinished work with material titles and proposal states, exact blockers and project_description status. Format compatibility does not imply complete project content or finished work; present text is not a content review. No source changes. Compatible active cycles are preserved, not cancelled. A pending migration returns resume_revision and its retained backup. Use the upgrade procedure through w.",
    "upgrade_apply": "Apply the reviewed technical migration on direct request, or automatic=true when the skill detects format incompatibility. Preserve workspace/control snapshots; migrate cards, source declarations and shared category index together. Keep unfinished cycles and immutable proposal approvals; adapt technical paths without applying old work. Complete only after successful indexing. Retry the original revision after interruption; never fabricate a user request.",
    "startup": "Open an existing compatible context with a fresh request_id; retries reuse it. Never creates or migrates. purpose is work, instructions, audit or rework. invocation=implicit is allowed only for work under allow_implicit_updates. Optional task_id references a migrated or ordinary record; find its ID through search. Store the exact original user_request when starting new work. Inspect cycles_list first. Temporary storage is optional; restart_cycle_id never silently discards pending work. Returns instructions and project; read them before work. Reuse an active cycle for continuation; a repeated skill invocation or question does not require another startup.",
    "status": "Read cycle state, pending proposals, index status and exact cleanup revision/scope without loading weights. Review the deletion scope before finish.",
    "cycles_list": "List bounded unfinished work, original requests and pending-proposal counts before a new cycle or task. Reuse the matching work; age alone does not establish obsolescence. Read relevant materials before deciding to retain, update or delete them.",
    "clarification_add": "Record the user's exact relevant clarification before dependent work. Supply one stable request_id per message; retry the same ID and text without duplication. Reusing an ID with different text returns request_conflict. Returns task_revision for the resulting assignment. Preserve message order and relay changed instructions to workers through native communication.",
    "list": "List bounded permanent metadata. For an entire-context review use scope=all and a fresh request_id; continue with next_cursor, not offset. A replayable inventory includes Context.md and knowledge files, plus explicit invalid/unreadable entries. Unique catalog/body delivery counters do not prove understanding. delta_refresh with scope_id captures separate changes; follow delta_cursor. Expiry requires a fresh request ID and never removes user data.",
    "read": _RETRIEVAL_PRECONDITIONS + "Read a complete permanent topic by YAML ID across compatible categories, without a cycle. Returns owning workspace, path, labelled links and bounded relation_candidates with explicit coverage. Page neighbors using relation_offset/relation_limit; choose relevant ones without treating ordinary links as instructions. from_id validates link traversal. On every receipt obtain each sources reference according to its instruction; this tool returns declarations and does not execute them.",
    "read_many": _RETRIEVAL_PRECONDITIONS + "Read selected whole topics by YAML ID without changing active rules. Ordinary batch reads share a catalog scan and return per-record neighbor pages. Optional scope_id credits complete bodies only against that review snapshot; missing/changed/invalid records stay explicit and counters do not prove understanding. For every returned sources block freshly obtain each reference according to its instruction, even on repeated reads. Returns bodies/declarations, not fetched evidence.",
    "search": _RETRIEVAL_PRECONDITIONS + _FIRST_SEARCH_SETTINGS + "Material mode groups document keys before relevance and result limits; fragment mode uses individual parts. Evaluate diagnostics scope, candidate cap, relevance and result cap before choosing reads or changing selection. Search permanent knowledge in the shared RAG without a cycle. Defaults to selected workspace; categories selects several, all_categories expands to all compatible contexts. Context categories, record types and statuses filter before nearest top-N. Return IDs, owning workspaces, descriptive paths, titles and distances. Cross-category retrieval never switches active instructions or write destination. Pending/invalid rows are excluded; empty output is not proof of absence.",
    "instructions_read": "Read complete Workspace instructions, every required document and their revisions through the Context.md mapping in Core. Follow every required document; reuse full text already loaded according to reread_instructions.",
    "record_types": "List record types actually used in the selected workspace. Types are open vocabulary, not a fixed enum.",
    "project_read": "Read the selected context's project purpose, goals and boundaries without a cycle or vector search.",
    "instructions_prepare": "Only for a direct user request to edit instructions: stage complete Markdown against the revision read, with the actual user_request. Follow w-settings. Returns exact review_changes without writing INSTRUCTIONS.md. Optional proposal_id revises the same unapplied instructions proposal.",
    "instructions_apply": "Apply the shown instructions revision under instructions_approval_required from its current instructions: true or absent requires actual user confirmation; false omits confirmation and applies without another prompt. Conflicts require renewed review under the current policy. No embeddings or tmp cleanup. Follow w-settings.",
    "tmp_write": "Save optional reusable inputs, an assignment or an intermediate result as a temporary Markdown card under .tmp. Uses readable names, stable YAML IDs and shared card links. Update a current file with its exact revision instead of appending an obsolete notice. Ordinary short results and direct knowledge changes need no temporary report.",
    "tmp_read": "Read a temporary card in the selected unfinished work, including labelled links and required sources. Follow every returned required source using the existing bounded source-reading procedure. Temporary content is evidence to examine, not automatically accepted knowledge.",
    "tmp_list": "List bounded temporary card metadata without bodies. Use the existing links and required sources to select needed reading; no separate mandatory report-disposition ledger exists.",
    "tmp_delete": "Remove an unneeded temporary material at its exact revision under cleanup_approval_required. Incoming temporary links or required sources must be resolved first. Preserve other work; save useful verified knowledge through ordinary proposals before deletion.",
    "proposal_prepare": "On an accepted substantive result, search by the conclusion, read relevant complete records and direct-neighbor candidates, choose and reserve affected cards, then prepare ready coherent changes in the same cycle. Required expected_task_revision must match the assignment used to compose the draft; record relevant clarifications first. task_changed requires reconsidering text, not replacing its revision argument blindly. Preparation writes no knowledge. Busy ownership blocks the complete set, including automatic incoming edits; after reservation expiry reread and reprepare. Creates use key/content/description/links (@key references a new topic); existing records use material_id/expected_revision and changed content/description/type/status/links/path or delete:true. Context.md instructions mappings obey independent instructions approval. replacements maps deleted IDs to survivors. For merges/splits declare transformations with kind, sources and targets; summary and provenance are revision-bound. Optional changes=[] with no_changes_reason is not a closure requirement. Returns exact proposal revision, task revision, review_changes and captured relation_candidates. Captured neighbors are not fresh evidence or required edits. Show actual changed passages and link effects before apply under approval_required; a topic list or file link is insufficient. Reuse proposal_id when revising an unapplied proposal. Follow complete bound instructions for semantic choices; native tools and text remain the agent's responsibility.",
    "proposal_read": "Retrieve a prepared proposal and exact review_changes for presentation in chat. relation_offset/relation_limit page direct-neighbor candidates captured at preparation; their labels/revisions are not current evidence, and in_proposal means actual membership only. A link to the review file does not replace showing changes.",
    "proposal_discard": "Discard an exact unapplied proposal without changing knowledge; temporary materials remain until closure.",
    "proposal_apply": "Apply and index the exact proposal after showing review_changes. Current knowledge_approval_required=true requires the actual confirmation of that revision; false omits it. Instruction bindings retain independent instructions approval. An unstarted stale assignment returns task_changed without writing; a legacy proposal without task_revision returns reprepare_required. Read current assignment, reconsider and reprepare the same proposal_id, then show its new review. Authorized recovery of applying/partial/index_pending retains original before/after checks; applied replay returns its receipt. A stop never authorizes automatic recovery. Saves leave the cycle and tmp active; pending review is not saved knowledge. Chat presentation/message origin cannot be authenticated.",
    "finish": "Close only the selected work after its bounded task is resolved and remaining conclusions are rechecked against permanent knowledge, then clean its remaining .tmp. Prefer the dedicated MCP finish tool. Read status for the cleanup revision and deletion scope; current cleanup_approval_required controls separate actual user confirmation. Resolve pending proposals/indexing and incoming dependencies first. Earlier saves leave the cycle active; no fake tmp report, per-file disposition ledger or no-change proposal is required for closure. Pause/handoff preserve needed data. cancel requires actual user cancellation and never rolls back permanent knowledge. Retry is idempotent.",
    "reconcile": "Retry derived indexing of already saved permanent text. It does not approve or rewrite knowledge. After repairing a proposal's pending indexing, retry proposal_apply to complete that operation.",
    "switch_embeddings": "With authority to change the common memory root's provider/configuration, rebuild the shared derived index for all compatible categories. Failure retains the old profile/table. No knowledge text changes; this setting is root-wide, not per-context.",
    "checkpoint": "With actual user authorization, checkpoint exact permanent knowledge paths in local memory Git. No tmp, source-project commits or push.",
    "history_read": "Read bounded text by material_id (resolved at that commit) or exact typed-record/INSTRUCTIONS paths. offset/max_chars page through text. Restoration uses the appropriate current proposal procedure; instructions follow their current independent approval policy. Reading a past version does not make it current.",
    "history_list": "List bounded Git commits by material_id, exact paths, message query or ISO dates. Returns parents and declared transformations; pass returned head as ref for stable pagination. ID filtering covers explicit provenance in new commits; older history uses paths or history_search. History does not override active instructions.",
    "history_changes": "Inspect one commit without file bodies: actual changed paths, parents, operation/cycle, record IDs and explicit merge/split/replacement relations. Paginate with offset/limit. Legacy commits report provenance=unrecorded; do not infer semantic ancestry from similarity.",
    "history_search": "Native Git text search for commits where the number of occurrences of exact text changed (git log -S), including deleted content. Optional paths scope the search; offset/limit and returned head/ref keep results bounded. This is text search, not semantic search.",
    "history_diff": "Compare two local permanent-data versions, optionally selecting exact files. offset/max_chars bound the diff. Does not restore them.",
    "history_retry": "Retry a bounded batch of pending local commits using their captured exact snapshots; later filesystem edits cannot silently enter an earlier operation. After verification in Git, snapshots become compact receipts. Return remaining_operations when more work remains. No push.",
    "history_index": "Rebuild one bounded page of Git metadata and declared transformations in SQLite, without duplicating content. Pin the returned head as ref and use next_cursor. Reports coverage; does not change Git or knowledge.",
    "history_lineage": "Trace bounded declared ancestry/descendants of record versions through indexed Git metadata. Optional start_commit selects the initial version; direction, dates, max_depth and limit bound traversal. Missing index/legacy metadata remains unknown; use history_index to expand coverage.",
    "analytics_query": "Select metadata and measured totals for observed operations. Defaults to selected workspace and last seven days; explicit workspaces, time, task/cycle/import, tool, instruction_revision, runtime_version and procedures_revision restrict analysis. Reuse next_cursor for frozen scope and insertion boundary. No model/session identity or token savings is inferred.",
    "analytics_report": "Summarize a bounded page of events with explicit scope, rule/runtime/procedure eras, search/read/link/write/error counts and evidence coverage. Default last seven days in selected context; task/cycle/import can specify another scope. max_events and metadata-byte budget cap inspection. Page groups are not whole-history totals; follow next_cursor only inside the intended scope.",
    "analytics_read": "Read one event with bounded bodies, observed instruction revision, procedure/runtime versions and exact Git references. Large bodies return IDs for paged analytics_blob_read. A started record has unknown outcome. Legacy snapshots remain labelled legacy evidence.",
    "analytics_reference_read": "Read bounded exact historical content for an event reference from Git, verifying recorded hashes. reference_index selects a returned reference; -1 selects instructions observed at call start. Dirty/unversioned or lost Git content cannot be replaced with today's file. Redaction is explicit.",
    "analytics_blob_read": "Read a bounded page of retained sanitized event/legacy body, using offset/max_chars. Partial JSON is text, not a complete value. Current permanent text lives in Git references.",
    "analytics_migrate": "Inspect or apply one bounded batch converting legacy SQLite file snapshots to verified Git references. Default dry-run; apply only deletes verified unreferenced blobs. Unmatched/redacted evidence and request/result bodies remain retained. Return coverage and cursor; never guess ancestry or silently discard history.",
    "analytics_evaluate": "Store an explicitly requested assessment separately, with exact event_ids, analysis scope and instruction_basis matching those events. Historical evidence is judged by recorded contemporaneous rules; unknown bases remain unknown. Scope and cited revisions are checked. Does not edit knowledge or events.",
    "analytics_import": "Only on an explicit request: import one selected transcript, including another agent's session, using codex-jsonl or the host-independent pcw-session-v1 format. Preserve source record coordinates and redaction markers. No session discovery or identity/call attribution inferred from timing.",
    "audit": "Read-only structural audit with material/file revisions and observed grounds. For a complete review use scope=all plus request_id, continue next_cursor, and read_many with scope_id. Frozen inventory includes Context.md, invalid and unreadable files. Counters measure unique delivered entries/bodies, never analysis. Separate delta_refresh/delta_cursor report subsequent changes. Expired inventories require a fresh request ID. Semantic contradictions require agent assessment, not heuristics.",
    "recover_workspace": "Recover missing derived runtime directories inside an explicitly started cycle. Missing knowledge stays blocked; no silent recreation or restoration of permanent facts.",
}


_QUERY_OPERATIONS = frozenset(
    {
        "preparation_read",
        "settings_read",
        "reservations",
        "workspace_inspect",
        "upgrade_plan",
        "status",
        "cycles_list",
        "list",
        "read",
        "read_many",
        "search",
        "instructions_read",
        "project_read",
        "record_types",
        "tmp_read",
        "tmp_list",
        "proposal_read",
        "history_read",
        "history_list",
        "history_changes",
        "history_search",
        "history_diff",
        "history_lineage",
        "analytics_query",
        "analytics_report",
        "analytics_read",
        "analytics_reference_read",
        "analytics_blob_read",
        "audit",
    }
)
_EXECUTE_OPERATIONS = frozenset(
    {
        "instruction_bind_prepare",
        "reserve",
        "reservation_renew",
        "reservation_release",
        "settings_preview",
        "settings_apply",
        "workspace_create",
        "upgrade_apply",
        "startup",
        "instructions_prepare",
        "instructions_apply",
        "tmp_write",
        "tmp_delete",
        "clarification_add",
        "proposal_prepare",
        "proposal_discard",
        "proposal_apply",
        "finish",
        "reconcile",
        "switch_embeddings",
        "checkpoint",
        "history_retry",
        "history_index",
        "analytics_migrate",
        "analytics_evaluate",
        "analytics_import",
        "recover_workspace",
    }
)
if (
    _QUERY_OPERATIONS & _EXECUTE_OPERATIONS
    or _QUERY_OPERATIONS | _EXECUTE_OPERATIONS != _DESCRIPTIONS.keys()
):
    raise RuntimeError("Every PCW operation must have exactly one entrypoint")


def _argument_model(name: str):
    """Use the existing method contract for both validation and on-demand help."""
    method = getattr(CycleRuntime, name)
    annotations = get_type_hints(method, include_extras=True)
    fields = {
        p.name: (
            annotations[p.name],
            ... if p.default is inspect.Parameter.empty else p.default,
        )
        for p in inspect.signature(method).parameters.values()
        if p.name != "self"
    }
    if name == "search":
        annotation, default = fields["N"]
        fields["N"] = (annotation, Field(
            default=default,
            description="Optional N override; omit or pass null to inherit the configured limit. "
            + _FIRST_SEARCH_SETTINGS,
        ))
    return create_model(
        name + "_arguments",
        __config__=ConfigDict(extra="forbid", strict=True),
        **fields,
    )


_ARGUMENT_MODELS = {name: _argument_model(name) for name in _DESCRIPTIONS}
_DESCRIBE_MODEL = create_model(
    "describe_arguments",
    operation=(str | None, None),
    __config__=ConfigDict(extra="forbid", strict=True),
)


def register_operations(registry, provider_factory=None, instruction_reconcile=None):
    from contextopus.core.operations import OperationSpec

    def handler(name):
        def call(context, **arguments):
            if context is None or not context.workspace:
                raise ValueError("An explicit Workspace context is required")
            from .server import dispatch

            return dispatch(
                str(context.root),
                context.workspace,
                name,
                arguments,
                **({"provider_factory": provider_factory} if provider_factory else {}),
                instruction_reconcile=instruction_reconcile,
            )

        return call

    for name, description in _DESCRIPTIONS.items():
        registry.register(
            OperationSpec(
                "workspace." + name,
                "query" if name in _QUERY_OPERATIONS else "execute",
                _ARGUMENT_MODELS[name],
                handler(name),
                description,
                guard_root=False,
            )
        )
