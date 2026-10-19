# Goal: execute the PCW upgrade plan using PCW

> Historical evidence: personal paths and trace/material identifiers are anonymized;
> measurements are preserved.

Status: completed. Candidate 0.11.0 was implemented and verified; see the
[results](context-workspace-v3-verification.md). User-approved changes to 24
project-update records were applied and indexed, and the PCW cycle was closed.
Publication and installation required a separate instruction.

## Intended outcome

Execute the [upgrade plan](context-workspace-upgrade-plan.md) according to
[architecture v3](context-workspace-design-v3.md): separate retrieval/updates,
user policies, PROJECT/task state, semantic records/navigation, Git history,
SQLite analytics, compatible upgrades, audit and rework.

Code, documentation, skills and migrations must describe the same behavior.
Distinguish implemented source, a verified package and a version installed in a
particular environment.

## Stable work coordinates

- Repository: `/home/example/Documents/persistent-context-workspace`.
- Existing PCW: `work_root=/home/example/Documents/persistent-context-workspace`,
  `workspace=project-update`.
- Do not create another context or guess its name if selection is lost.
- This document defines this task's procedure. Read current project rules from
  INSTRUCTIONS.md; do not copy them into this document or knowledge.

## Execution procedure

1. Once implementation is authorized, open an explicit cycle in the selected
   existing context. Read full INSTRUCTIONS and retrieve the architecture/current
   stage. Discussion helps establish the task but does not replace memory reads.
2. Compare the vision, plan and source state. Select the first unfinished stage
   from verified results, not an agent's earlier promise. Preserve goals and do
   not add functions from rejected alternatives.
3. Until Current tasks exist, keep stage/progress/evidence/next step/blockers in
   the cycle's ordinary tmp record. After implementing and verifying that feature,
   use one explicitly selected `current/<task-id>.md`, retaining its task_id across
   cycles. Do not call tools that do not yet exist.
4. Read relevant topics/links, investigate missing facts through permitted sources
   and work directly or through authorized executors. Returns belong in tmp and
   do not automatically become permanent knowledge.
5. Implement one bounded stage and satisfy its plan criteria. Update descriptions
   of actual behavior with the code. A feature described in v3 is not thereby
   working. Preserve exact blockers and remaining work.
6. After a substantive stage, search by the established conclusion, read matching
   topics and replace stale descriptions instead of appending a work log.
   PROJECT/INSTRUCTIONS hold goals/rules, Current or tmp holds work state, and
   knowledge describes established structure and procedures.
7. Prepare exact changes or no_changes and show their text/links in chat. Apply
   under the current approval policy; a plan is not approval of a particular
   revision. Retry the same operation for apply/indexing errors while retaining
   results.
8. Record verified completion or remaining work. At continuation, reread applicable
   rules and needed knowledge. Use the installed MCP's available functionality,
   not merely changed plugin source.
9. Close the cycle only after assessing results and resolving application or
   no_changes/rejection. Cleanup is not knowledge retention and must not remove
   the only copy needed for continuation. Completed Current tasks remain; closing
   one cycle does not declare the whole plan complete.
10. After all stages, perform end-to-end checks, reconcile docs and the change set,
    and report outcomes/limits. Publish/install only when authorized, then verify
    actual installed versions and state.

## Completion criteria

- All ten plan stages pass, including old contexts, parallel tasks, errors/retries.
- Knowledge, identities, useful links, user instructions and unfinished work are
  preserved; substantive rework stays within its authorized scope.
- Independent retrieval needs no cycle and creates no context. Updates obey
  separate invocation and approval settings.
- Audit is separate from repair; knowledge is separate from goals, temporary
  results and analytic history.
- Git/analytics expose observable facts and evidence limits. File-write success
  does not imply indexing, commit or release success.
- PCW describes implemented behavior rather than a list of intentions and reports.

An unfinished stage leaves this goal unfinished. A prepared plan, accepted design
and isolated passing tests do not themselves achieve it.
