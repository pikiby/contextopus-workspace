# Workspace architecture

Workspace is a trusted native contribution loaded by Core's generic module host.
`module.json` points to `package/workspace/__init__.py:register`. No Workspace
branch in Core startup is required. The contribution registers operations,
settings, procedures, skills, lifecycle hooks and a shared-index source adapter.

## State ownership

| State | Owner and location relative to the data root |
| --- | --- |
| Project instructions and Markdown records | Workspace: `modules/workspace/workspace_contexts/<context>/` |
| Cycle controls, migration receipts and temporary material | Workspace's context/control layout |
| Domain evidence and history catalog | Workspace schema in `core/storage/modules/workspace.sqlite3` |
| Module activation/releases | Core module lifecycle |
| Model providers and shared retrieval index | Core resources/RAG |
| Generic operation and search evidence | Core journal, linked to Workspace evidence |
| Effective machine policies | Core configuration hierarchy, with Workspace settings contribution |

Context names choose rules and write destinations. A query across categories
expands the evidence scope while keeping the active context's instructions.
Markdown records carry stable YAML IDs, descriptions, types and links; filenames
are descriptive and may change. Types are open vocabulary. Plans and goals record
intent until evidence establishes an outcome.

## Work and evidence

Independent retrieval reads complete rules, searches, reads relevant bodies and
checks each required source. Authorized work starts/reuses a cycle, retains optional
temporary inputs, prepares exact changes and applies the reviewed revision under
the effective approval policy. Cleanup has a separate policy and bounded scope.
Procedures in `package/workspace/resources/procedures/` are the authoritative
agent workflow; client skills route to them.

Git history retains permanent content versions when enabled. Workspace SQL stores
operation references, evidence and history indexes; Core owns generic journaling
and shared search evidence. A read event does not establish that an agent used a
record correctly, and missing historical instructions cannot be reconstructed from
current rules. See [search analysis](search-analysis.md).

Workspace supplies its schema and explicit migration hooks. Core coordinates
per-module writers and transactions. Ordinary reads never migrate storage. Legacy
format adoption, explicit schema cutover and source import preserve their own
receipts and checks; they are not inferred from module discovery.

[Base context](base-context.md) describes managed documentation delivery. Historical
design snapshots are preserved under [archive](https://github.com/pikiby/contextopus-workspace/tree/main/docs/archive); their old paths,
version assumptions and experiments do not define the current installation.

## Workspace records (format 5)

`modules/workspace/workspace_contexts/<context>/INSTRUCTIONS.md` owns that context's complete free-text rules. Machine policies use the existing Core configuration hierarchy, with migrated context values in `core/configuration/contexts/workspace/<context>.json`. `knowledge/*.md` contains all permanent record types. YAML `id` is the stable identity used by the API; on-disk YAML links are quoted Obsidian wikilinks to real vault paths. Descriptive filenames can change. `type` is open vocabulary (`knowledge`, `task`, `goal`, `project`, `decision` are examples); `status` is optional. Plans and goals remain intentions until evidence establishes completion.

The project description is one indexed `type: project` record. Tasks use the same read/search/proposal/link/history operations. There is no separate Current or mutable duplicate PROJECT file. `.tmp/<cycle>/` optionally holds shared assignments, inputs and intermediate results, with readable card names, YAML IDs and the normal links and required sources. Exact pending proposals also live there. Temporary cards can refer to permanent or temporary materials; permanent cards cannot depend on temporary data. Cleanup uses its own approval policy and exact revision, independently of knowledge approval; an empty or unchanged task needs no fabricated report or proposal. Custom instructions are not silently replaced by new defaults.

RAG belongs to Core. Context, type and status predicates narrow eligible records before vector top-N. Cross-context retrieval supplies evidence, not another context's rules. `workspace.contexts` lists actual directories, `workspace.project_read` reads one selected project, `workspace.projects_search` searches indexed project records and returns bounded excerpts. Discovery starts no work cycle and creates nothing.

Migration snapshots sources/control, moves legacy Current fields and PROJECT into records, extracts only known machine policy keys from instruction frontmatter into Core configuration, converts native navigation links, reconciles the shared index, then marks each completed format transition. Format 5 preserves immutable proposals through execution overlays, moves `tmp` to `.tmp`, normalizes temporary card names and adds derived `context/<owner>` tags to permanent records. Context names are unique under Unicode NFKC and case folding. The remaining instruction text and user frontmatter retain their original bytes. Original pending proposal text/digests remain immutable; technical execution overlays adapt paths and metadata without granting approval. A legacy instruction proposal that changes machine settings requires a separate settings review and renewed instruction preparation. Interrupted transitions reuse their receipt. Git tracks permanent records, rules and historical settings; tmp, indexes and service state stay outside memory history.

### Record provenance in Git

Workspace proposes Git history in the standard creation settings. Reviewed transformations describe merge/split/replacement edges between stable record IDs. Their before/after paths and fingerprints, summary and cycle/operation are captured with the permanent snapshot and stored in ordinary Git commit trailers. Git parents provide version ordering; declared transformations provide material ancestry. Native Git query operations filter by ID/path/date/message, search removed text, describe commits and read bounded diffs or old records. Old commits without declarations remain navigable by path/text and are explicitly marked as lacking provenance.


### Workspace settings history

Machine policies live in Core configuration at
`core/configuration/contexts/workspace/<context>.json`; `INSTRUCTIONS.md` keeps the
complete free-form user rules. The Git-only path `.contextopus-settings.json`
records effective settings and their origins. It has no live worktree copy, and
the Git index marks it `skip-worktree`. History read, diff and search accept that
path. Durable previews and apply receipts live in the Workspace control directory
under `settings/`; retries retain the exact approved revision. Settings writes and
pending Git commits have separate outcomes. Disabling history still records the
final change under the prior policy.

New analytics events retain a separate settings basis in
`instructions_ref.settings_revision` and `instructions_ref.settings_ref`, plus a
Git reference with `role: workspace_settings`. The free-text instruction revision
retains its own meaning. Analysis distinguishes settings eras even when that
instruction revision is unchanged. Missing historical settings remain unknown;
today's configuration never supplies a replacement. Settings-only observations
do not read the instructions and mark that part of their basis unknown.

### Operation evidence and analysis

SQLite journals record calls, parameters, result/error metadata, durations and references. Git remains the authority for permanent content versions; exact commit/blob references require matching captured content. Dirty or unversioned observations expose their replay limits. Historical retained blobs and unmatched evidence are preserved rather than discarded on an assumption that Git covers them. Drafts and imported host messages remain distinct evidence.

A derived SQLite history index supports bounded commit/version indexing and context-qualified lineage graphs from declared Git transformation trailers. It is rebuildable, and declarations do not prove semantic correctness. The `w-analyze` skill loads the canonical MCP analysis procedure: finite context/question/time/filter scope, progressive metadata selection, exact events, then Git versions/diffs. Reports state coverage and contemporaneous instruction/runtime evidence; unknown historical rules cannot support retrospective judgments. Reads do not prove actual model use, and performance or token savings require host usage and comparable control runs. Explicitly retained assessments are separate from objective events and knowledge.
