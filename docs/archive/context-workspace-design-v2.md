# PCW: project instructions and an explicit memory cycle

Historical governing design for the original project. `INSTRUCTIONS.md` replaced the earlier Current scheme. Source changes did not imply publication or installation. The [previous design](context-workspace-design.md) remains historical evidence.

## Responsibilities and context selection

PCW supplies project memory, temporary storage, search and coordinated writes. The user defines the project's goals, constraints, sources, workflow and knowledge requirements in the selected workspace's `INSTRUCTIONS.md`. The main `pcw` skill implements the technical cycle; `pcw-create` handles explicitly requested creation; `pcw-instructions` handles explicitly requested rule changes. `knowledge/` holds durable topics and links; `tmp/<cycle-id>/` holds results, executor returns, task state and proposals. Instructions are free Markdown, shared by cycles in that workspace; task state does not overwrite them.

Continue with established context coordinates. Creating or switching contexts requires an unambiguous user instruction; a new task, working directory, PCW invocation or empty search is insufficient. Resolve ambiguity before calling PCW. Check existence before opening: historical `pcw_startup` could create storage and must not be used to probe guessed names. This is an instruction-level restriction, without a separate technical proof of human permission. Switching does not close or clean another cycle.

The main skill routes an explicitly requested function to its dedicated skill, including ordinary-language creation and instruction-edit requests. A question about a function does not invoke it. Creation accepts a location and purpose in free form; offer concrete choices or clarify missing information before creating. Initial standard instructions can then be adapted through the separate instruction-edit procedure. Empty-context creation does not authorize future work or invented knowledge.

## One current source of project rules

Read the complete current `INSTRUCTIONS.md` before searching the context and use it during work and proposal preparation, within the user's task and mandatory environment rules. Old chat, cached instructions and the default template do not replace this read. `default_instructions.md` is the single initialization template: it is neither merged into existing rules nor used to restore sections removed by the user. Skills and MCP descriptions point to the instructions instead of copying parallel rules for selection, structure, consolidation, history or presentation.

Instructions survive start, restart, cancellation and finish, and are excluded from the vector index. Missing instructions in an initialized workspace are an error, not permission to replace user rules with defaults.

```yaml
---
knowledge_approval_required: false
---
```

With `false`, show exact knowledge changes and apply without another approval question; with `true`, wait for explicit approval of that displayed revision. New templates use `false`; absent settings in existing files retain required approval. Invalid or duplicate values are rejected, and free text does not override YAML. Editing the instructions themselves, including this setting, always requires explicit approval through their dedicated procedure. Knowledge approval grants no external-action authority. Both modes require displaying changes and reviewing results; runtime checks policy and revisions but cannot verify the chat display.

## Historical storage

```text
<work_root>/.pcw/<workspace>/
  INSTRUCTIONS.md
  knowledge/<id>.md
  tmp/<cycle-id>/
    materials/<id>.md
    proposals/<proposal-id>.json
    proposals/<proposal-id>.md
  .pcw-runtime/
<work_root>/.pcw/.control/<workspace>/
  cycles.json
  instructions-initialized.json
```

Instructions belong to the workspace; temporary results belong to a cycle. Goal, step, constraints and blockers can be ordinary temporary materials. This design removed fixed Current operations. Control records contain states, IDs and revisions, without retaining cleaned temporary bodies or proposals. Symlinks and escapes from selected storage are rejected.

## Work cycle

1. Select the requested procedure and existing context. An explicit work request starts `pcw_startup` with a new `request_id` and default `purpose="work"`. Retries reuse that ID; explicit restart names `restart_cycle_id`. A mention or past permission does not start new work.
2. Startup returns `cycle_id`, full instructions and revision, and bounded memory status without loading a model. Read rules, then search and read relevant context; chat alone is insufficient.
3. Perform authorized work and save temporary results with `pcw_tmp_write`. Delegation follows applicable user and environment instructions.
4. Review the completed task or meaningful portion, search by the conclusion, read related materials and prepare knowledge/composition/link changes using `pcw_proposal_prepare`. If nothing should change, send `changes=[]` and `no_changes_reason` as the outcome of that review.
5. Show `review_changes`: topics, complete new bodies or exact changed fragments, deletions, moves and links. A filename or topic list does not replace the content.
6. Apply the displayed revision under `knowledge_approval_required`. Required approval supplies the user's actual `confirmation`; automatic mode supplies none. Questions and corrections continue discussion and a revised proposal is displayed again. Writes preserve temporary results and schedule changed bodies for indexing.
7. After work, review and agreement, call `pcw_finish` without a separate closure question. It removes only this cycle's temporary files. Pending proposals or indexing block finish. User cancellation permits `cancel:true`, reports unfinished operations and does not roll back durable data.

Proposals depend on temporary-result and instruction revisions. Changed instructions require a fresh review; an unstarted proposal prepared under old rules cannot apply. Failed writes/indexing retain temporary files and retry the same operation. A write already started under the former policy can finish, followed by review under current rules before closure. Approval of a body does not authorize installation, publication or other external actions.

## Instruction editing

Only the dedicated instruction skill defines this procedure. Direct user requests to change project rules, even when called Current, route there; agent conclusions and work notes are insufficient authority. A separate invocation starts `purpose="instructions"`; editing during work reuses its cycle and returns to that task. A standalone instruction cycle writes no knowledge and needs no fictitious work-result review.

- `pcw_instructions_read`: complete file and revision.
- `pcw_instructions_prepare`: temporary full-text draft against the read revision and direct request; returns exact changes for display.
- `pcw_instructions_apply`: verifies approved/source revisions, writes and checks exact text without embeddings.
- `pcw_proposal_read/discard`: inspect or discard the specific proposal.

Instruction and knowledge proposals cannot substitute for one another; knowledge operations cannot write instructions. Concurrent changes require rereading, preparing and approving rather than overwriting. Identical text needs no write/approval. Explicitly empty instructions remain the user's choice. Revision and fidelity checks do not authenticate human authorship or guarantee semantic model compliance; shell access is no bypass.

## Roles, search and transition

The primary agent or a designated clean-context subagent performs the complete cycle from the instructions. The primary may relay task, questions, proposal and actual approval without approving for the user. An ordinary executor returns results to the cycle agent. PCW does not select models, define the environment or launch executors; standard MCP exposes the same procedure to different hosts.

Knowledge remains Markdown with filename IDs, revisions and exact navigation links. Displayed and, where required, approved changes may create, update, move, split, merge or delete topics, including incoming-link edits. Overlapping token windows and embeddings are derived automatically from a separately configured provider. Temporary files and instructions are excluded from durable search; unchanged bodies and link-only changes need no new embedding. Index errors do not erase saved text and retries reuse the operation.

At the historical transition, absent instructions were initialized from the single template while existing ones were preserved. Old root/cycle `current.md`, knowledge and control state were not converted into rules or silently rewritten. Old temporary files follow their own cycle cleanup. Old cycles count as work cycles; proposals lacking instruction revisions need new preparation before normal apply/finish.

Verification covers both knowledge policies, mandatory instruction approval, mode-change bypasses, first issuance, arbitrary Markdown, model-free instruction reads, persistence, exact approved revisions, conflicts/partial writes, cycle isolation, knowledge-write bypasses and skill routing. Code and packaging tests do not establish agent compliance.
