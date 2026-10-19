# Format 2 contracts

> Privacy: personal paths and trace/material identifiers are anonymized; reported measurements are unchanged.

The current design is [v4](context-workspace-design-v4.md). MCP still publishes only `pcw_query` and `pcw_execute`; `describe` supplies operation schemas. Existing creation, work, approval, Current and history procedures remain.

## Source identity and scope

`work_root` names the selected common memory location. Each `.pcw/<workspace>/` retains its own INSTRUCTIONS, PROJECT, knowledge, Current, tmp and optional Git history. `.pcw/.rag/` contains a shared LanceDB table and embedding profile; `.pcw/.analytics/` contains the shared event journal.

Knowledge filenames describe topics. Frontmatter contains the stable identity and navigation:

```yaml
---
id: 00000000000000000000000000000001.md
links: []
description: Conditions and steps for connecting to the storage service
---
# Connecting to storage
```

The old `.md` suffix in migrated IDs is retained literally; it no longer denotes a required filename. All cards in the common root must have unique IDs. Renaming/moving a file retains its ID; path/category changes reuse vectors when the body and embedding profile remain unchanged. Proposal paths are relative to `knowledge/`. New paths default to a sanitized heading; collisions receive an ID suffix. Explicit destination collisions fail.

## Retrieval

`search` adds optional `categories: list[str]` and `all_categories: bool`. Omit both for the active context. Supply a list for selected categories or true for all, never both. Category and valid-row predicates are applied before nearest top-N. Returned results include the owning `workspace`, stable ID and current path. Incompatible requested categories require upgrade; missing categories are not created.

`read` and `read_many` resolve global IDs inside the common root and return provenance, labelled links and `sources`. `from_id` can validate a cross-context link traversal. Neither search nor reading changes active context coordinates or its instructions. Proposals mutate only the selected context. A deletion with incoming links from another context fails until those links are explicitly corrected there.

`switch_embeddings` applies to the common root. A rebuild includes all compatible categories and publishes the candidate table only after source verification. A failure before publication retains the prior table/profile. Ordinary reads never load other contexts' instruction files.

## Sources

The body can contain a fenced YAML block:

```yaml
sources:
  - reference: "pcw material 00000000000000000000000000000001.md, section Connection limits"
    instruction: "Read the current timeout and applicable environment from that section."
  - reference: "/absolute/project/queries/active-users.sql"
    instruction: "Read the saved query and its connection instructions; run the authorized read-only query and obtain its count."
```

References have no closed type enum. Each item requires a nonempty reference and retrieval instruction. Tools return declarations but never execute them. Skills require fresh bounded retrieval on every receipt, with explicit handling of ambiguity, failures and circular dependencies. Existing authoring settings remain independent of this reading obligation.

Legacy blocks are normalized before proposal review, so old custom instructions can still supply `required_sources/url/extract` without causing an invalid permanent write. Review and application use the same normalized text. Migration performs this same syntax conversion on saved cards.

## Upgrade and recovery

`upgrade_plan` checks file structure, index agreement and saved unfinished work. `upgrade_apply` accepts either a real `user_request` or `automatic: true` when the skill detects incompatibility. Technical migration does not approve a knowledge proposal or change custom instructions.

Formats 0/1 migrate to 2. The first migrated context supplies its old embedding configuration if no common profile exists; subsequent contexts use the existing common profile. Snapshot copies retain workspace/control sources, including tmp, old local index and Git history. The common index is reconciled by category; another context's vectors are not replaced by a local rebuild.

Migration receipts retain source fingerprints and exact before/after transformations. Retries accept only those recorded states. Index failure returns `index_pending` and `resume_revision`; the format marker is not advanced. Repeating that revision resumes saved work. Original proposal files, revisions and approval states stay intact; a technical mapping translates their snapshots when they are later applied. No old proposal is applied or cancelled by migration.

A current format marker alone is insufficient: cards lacking YAML IDs, legacy source declarations or an index that disagrees with files are reported by preflight. An unambiguously indexed path can recover a missing YAML ID; otherwise recovery requires its original identity from history rather than inventing one.

The root is explicit. This release does not discover or silently consolidate different memory roots across the computer. Selecting and relocating existing contexts from another root must name both locations and preserve their separate control state; simply copying `knowledge/` would omit rules, tasks and proposals.
