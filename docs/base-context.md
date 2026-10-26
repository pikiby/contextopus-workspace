# Base documentation context

Contextopus ships product guides and templates as ordinary Workspace records. `core.base_status` reports the selected documentation context and installed bundle version. `core.base_plan` is read-only and returns exact incoming changes and `plan_revision`; `core.base_install(plan_revision, confirmation?)` installs that revision using Workspace creation, proposals, shared indexing and Git history. Status and preview do not initialize a context or load embeddings.

The shipped context is `contextopus-base`. Its initial instructions use `material_max_tokens: null` because product guides can be long. Changes to its instructions remain under the normal direct-request and instruction-approval rules. A user-added length limit is respected by later updates, which return `needs_split` rather than changing the rule. If knowledge approval is enabled, installation returns `review_requires_approval` until actual confirmation is supplied.

`core.base_select(context)` points the documentation role at another existing context. This is separate from active task selection and the general settings. Reading base records never changes active instructions. Updating the shipped context preserves the selected role when another context was selected.

The pointer and managed revision receipts live in `core/configuration/base-context.json`. Documents themselves live in the ordinary Workspace knowledge folder and common index; there is no additional documentation store. Modified or deleted managed records cause a conflict that includes incoming text. Updates preserve custom INSTRUCTIONS and retired documents. A colliding unmanaged context is never adopted or overwritten.

Interrupted creation, application and indexing retain their exact plan and proposal receipt. Repeating `core.base_install` with that original revision resumes the operation. Conflicts and user-added rules require review instead of automatic overwrite.

## Scenario cards

The bundle explains capabilities and common tasks, with short thematic records instead of a release changelog. Canonical procedures remain the authority for the complete workflow. See [usage scenarios](usage.md) for the public companion guide.

| Managed key | Content |
| --- | --- |
| `contextopus-guide` | Product overview, one Core/shared RAG, separate context files/rules and the scenario map. |
| `retrieval-guide` | Find a project, read current rules, retrieve typed records and use cross-context filters without switching rules. |
| `workspace-guide` | Update existing tasks/decisions/knowledge, review proposals, share optional .tmp handoffs and finish under cleanup policy. |
| `evidence-guide` | Required source checks, Git versions/lineage, SQLite operation evidence and bounded search analysis. |
| `integrations-guide` | Connect five native clients to the same owner and distinguish configuration, discovery and actual calls. |
| `module-guide`, `module-template` | Folder-module lifecycle, handlers, shared structured storage and RAG boundaries. |
| `algorithm-guide`, `algorithm-template` | Inspect/tune selection and author deterministic algorithms with diagnostic limits. |
| `installed-version`, `rules-template` | Documentation updates and directly requested rule/settings changes. |

Each manifest entry has an authored description and key-based navigation links. Installation resolves those links to real record IDs. Existing managed keys retain their IDs; new topics get new records. Implementation references use repository-relative source paths, and cards point to detailed installed procedures without copying them wholesale. Reading a cross-context reference never adopts its rules.

## Preview an update without losing local edits

1. Inspect `core.base_status`, then request `core.base_plan` for the selected context on the connected server. Even equal version strings do not establish that card contents are current; plan compares the managed content and links.
2. Review incoming bodies, descriptions, navigation changes, conflicts and the exact `plan_revision`. A modified/deleted managed card, custom description or incompatible local size rule remains an explicit unresolved update. Keep local text and show both versions for a scoped decision; do not reset receipts or silently replace it.
3. Apply only the reviewed plan through `core.base_install`, supplying actual confirmation when policy requires it. Retain pending proposal/indexing receipts for a safe retry. Report applied content, indexing/history and cleanup independently.

The long-lived MCP owner is bound to one configured root and caches an Application per context; that Application snapshots its packaged base bundle at construction. There is no public base-bundle reload operation. `core.algorithms_refresh` refreshes the algorithm registry/catalog, not these cards. Updating package files alone does not refresh an already-created Application: the supported owner refresh is a service restart, followed by a new plan. Do not use a fabricated context coordinate to evade the cache. No restart or live context update is implied by editing the shipped resources.

## Installation and upgrade

Explicit `contextopus init` (`core.init`) initializes storage. If no active embedding profile exists, its `setup` result reports `model_setup_required`; no documentation context or algorithm vectors are created yet. Preview and apply the model with `contextopus setupmodel --apply` (or `core.model_plan` then `core.model_apply`). After successful embedding setup, Core refreshes the shared algorithm catalog and installs the bundled documentation through the exact base plan. A later explicit init runs the same preparation for an existing root after a package upgrade.

Read-only status, diagnostics, base previews and algorithm searches never perform this setup. User-modified or deleted managed documents produce an explicit conflict plan. Current knowledge approval produces `review_requires_approval` with the exact plan; pass actual confirmation to `core.base_install` after presenting it. A documentation/catalog delivery failure appears under `setup` as pending and retains the successfully installed model. The response identifies the operation needed to retry. A custom selected documentation role remains selected.

A legacy `.pcw` root still follows the existing explicit relocation procedure before `core.init`; documentation setup does not migrate or replace that storage. Changing the embedding profile rebuilds source documents first, then refreshes derivative algorithm cards for the newly active profile.
