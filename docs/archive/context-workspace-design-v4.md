# PCW: shared databases and separate contexts

## Accepted goal

One explicitly selected `work_root` contains `.pcw/<workspace>/`, with separate
INSTRUCTIONS, PROJECT, knowledge, current and tmp. The shared index is
`.pcw/.rag/`; the journal is `.pcw/.analytics/`. A memory root is not implicitly the
current repository. Separate roots are not merged by inference; moves need exact
source and destination coordinates.

A category identifies a record's owning context. `search(categories=[...])` searches
those contexts, defaulting to the selected one; `all_categories=true` expands to
the whole database. Filtering precedes top-N and results retain their origin.
Expanded retrieval changes neither instructions, the cycle nor the write target.
The shared database uses one consistent embedding profile; incompatible old
vectors are recomputed during migration.

## Records

Format 2 stores a permanent YAML `id`; filenames reflect titles and can change
independently. Legacy filename IDs remain literal ID values. Links address IDs,
not paths. The index stores context, ID, path and fragment coordinates. Duplicate
IDs block ambiguous reads/writes. Path-only or metadata-only edits do not require
re-embedding the body.

Files remain authoritative and the database is derived. Cross-context links are
allowed; reading their targets does not change task rules. Writes address the
selected context. Before deleting a record with incoming links from another
context, obtain an agreed correction of those links rather than silently editing
foreign files.

## Sources

A record body may contain a YAML `sources` list with `reference` and `instruction`.
A reference can identify a file, agent record, material, fragment, URL, API, query
or another unambiguous object; there is no closed type list. Whenever a record is
received, the agent freshly obtains the requested information with authorized
tools and extracts the answer with its conditions. Clarify ambiguity and report
unavailability. This is an agent procedure, not autonomous server execution of
arbitrary commands.

INSTRUCTIONS governs whether new required-source blocks are authored; existing
blocks remain mandatory independently of that setting. Ordinary citations and
navigation links do not become required sources. Legacy
`required_sources/url/extract` converts to `sources/reference/instruction` while
preserving the verification question.

## Upgrade

On incompatibility, the agent immediately invokes pcw-upgrade and resumes the
original task after success; no additional task is needed for that technical
migration. Tools return a diagnosable upgrade requirement. Reading never silently
rewrites files.

Migration binds file/control revisions, preserves a recoverable snapshot and
records the final transformations. Retries accept only verified before/after
states, not arbitrary intervening changes. IDs, content, relationships, user
instructions and unfinished tasks are retained. Migration does not grant new
user approval to old proposals. The shared index is checked against actual files;
the format becomes current only after file changes and indexing finish. Failures
retain resumable state.

## Verification

Cover multiple contexts with one index, category prefiltering, cross-context reads
and links, rule/write isolation, YAML IDs, renames, duplicate IDs, source conversion,
migrations/retries, temporary work/proposal retention, and file/index consistency.
Tool tests do not establish universal model compliance with the skill.
