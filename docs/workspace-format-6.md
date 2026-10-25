# Workspace format 6: canonical context and shared instructions

The canonical project is `<context>/Context.md`. It is an ordinary indexed record with `type: project`, its existing stable YAML ID, description, context tag and native navigation links. It participates in read/search/discovery, reservations, reviewed proposals, source checks and Git history. A proposal edits its body and description without moving it out of the context root or deleting its identity.

```yaml
---
id: existing-project-id
type: project
description: Purpose and boundaries of this context.
links: []
instructions:
  workspace: '[[workspace.my-context]]'
  agents: '[[agents.my-team]]'
---
# My context
```

Instruction references identify real Markdown files under `<root>/core/instructions/`; unique basenames are native Obsidian links. The `workspace` role is mandatory. Other roles, including `agents`, use the same common Core resolver, with dependency ordering, missing/ambiguous reference checks and cycle detection. Instruction documents remain separate from knowledge records. Complete primary rules and every required document are returned by `workspace.startup` and `workspace.instructions_read`. Consumers follow the effective `reread_instructions` setting: reuse full text already loaded for the same task and reread after task/context/rule changes or lost text; do not repeatedly retrieve unchanged rules solely because another phase begins. Explicit profiles affect rereading only.

`instructions.content` remains the exact Workspace rule body for instruction proposals. `instructions.documents` contains the other complete required rules, dependencies first; the primary body appears only in `instructions.content`. `instructions.primary` carries its metadata without a second body. `required_revision` binds their combined full-file revisions. Knowledge and instruction reviews reject a dependency change before application. Machine settings retain their independent canonical Core configuration and approval policy.

## Explicit migration on an isolated root

Install the matching Core and Workspace packages in a test environment first. Operate on a copied root; stop other writers while migrating. No read, startup, context discovery or search performs this migration.

1. Inspect the existing context and call `workspace.upgrade_plan`. Inspect `ready`, blockers, pending work, renames, instruction destination and source revision.
2. Apply that exact revision with `workspace.upgrade_apply(expected_revision=..., user_request="Upgrade this isolated context")`. Newly initialized rules additionally require their separately reviewed confirmation. Existing rules are copied without adding or deleting user text; legacy machine flags are moved through the existing explicit settings transition.
3. Retain the returned workspace/control backups and `migration-v6.json`. If interrupted, retry its `resume_revision`. The receipt protects the immutable source snapshots; pending cycles and original proposal review digests remain intact with technical execution overlays.
4. Check `Context.md`, the preserved project ID and body, native navigation link destinations, full required rules, settings, search/discovery, a reviewed project update and historical reads. Verify indexing separately from source writes.

A runnable API example, with an existing copied root and context:

```python
from workspace.cycles import CycleRuntime
runtime = CycleRuntime("/absolute/path/to/copied-root", "my-context")
try:
    plan = runtime.upgrade_plan()
    if not plan["ready"]:
        raise RuntimeError(plan["blockers"])
    result = runtime.upgrade_apply(plan["resume_revision"] or plan["revision"],
                                   user_request="Upgrade this isolated copied context")
    print(result)
finally:
    runtime.close()
```

The verified migration backup retains the exact old `INSTRUCTIONS.md`; after copying its rules to Core, migration removes that obsolete live address. New contexts have no duplicate `INSTRUCTIONS.md`. Active instruction edits target Core through the existing instruction proposal operations. History captures active primary rules at the logical `INSTRUCTIONS.md` path and exact complete required instruction files under virtual `.contextopus-instructions/` paths; these are historical representations, not live copies. Unknown files remain untouched. A missing project receives an explicitly reported placeholder; migration does not invent project requirements. Multiple project records, an occupied unrelated `Context.md`, conflicting shared instruction content, symlinks, active reservations or incompatible pending operations block before applying. Foreign contexts linking to a project being moved require their own explicitly reviewed coordination; migration does not silently rewrite other contexts.

Local package installation and migration of the user's active root are separate delivery steps and are not performed by these procedures or tests.

## Changing role bindings

Reserve the Context.md project ID and prepare a normal `workspace.proposal_prepare` change with material_id, expected_revision and an `instructions` mapping. Its workspace role is required; optional roles such as agents must resolve in the shared Core library. Review shows the exact old/new mapping. Changes obey both knowledge approval and the independent instruction approval policy; disabling knowledge approval does not bypass approval for new rule bindings. The proposal binds target revisions, checks them again before apply and records the mapping in Context.md history.
