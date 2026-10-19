# PCW: creating and using context

> Historical version dated September 17, 2026; not current instructions. Personal paths and trace/material IDs are anonymized; measurements are retained. See the [next design](context-workspace-design-v2.md) for its successor. Project rules belong to INSTRUCTIONS.md.

The basis was the separately preserved local governing document at `.pcw/project-update/knowledge/plugin-update-design.md`; that private document is not included in this repository. This text specifies a target design for the primary agent's working memory, not an implementation report, plugin release or certification of installed code.

The central change was to remove mandatory delegation of external research, modifications and verification. The primary agent may act directly or delegate under the user's and host's rules. Both routes must consult accumulated context and preserve useful new knowledge.

Embedding preparation is automatic: save ordinary knowledge, let code create overlapping token windows, and compute vectors with the configured provider. The default is local `Qwen/Qwen3-Embedding-0.6B`; another local model or embedding API may replace it. No separate language model chooses boundaries.

## 1. Goal

Retain useful context across tasks and sessions while retrieving only what the current work needs. Consult existing knowledge first, then incorporate verified findings and corrections. Reduce repeated source investigation and unnecessary reading through independently meaningful topics, precise navigation links and overlapping search fragments within each topic.

PCW owns storage, retrieval and maintenance. Users, the primary agent and its host own worker selection, external execution and verification. Memory does not impose one universal delegation scheme.

## 2. Responsibilities

| Participant | Responsibility |
| --- | --- |
| Primary agent | Retrieve and assess context, act or delegate, form knowledge, and manage all memory through MCP. |
| Optional external worker | Complete a bounded authorized assignment and return findings, results and verification limits; do not manage PCW. |
| Embedding provider | Compute one vector per prepared text/query. Do not split or edit knowledge. |
| Plugin/MCP | Save and address materials, apply explicit link changes, tokenize/index automatically and return actual operation statuses. |

### Memory belongs to the primary agent

The primary agent reads knowledge, saves topics, updates Current, corrects/compacts text, changes links and deletes replaced materials. External workers do not receive these responsibilities. Saving takes complete ordinary text, not fragment arrays, boundaries or indexing assignments.

Knowledge may come from direct research, a change or test, the user, or a substantive worker result. Preserve provenance and verification limits. Direct observations require neither a fake assignment nor an invented worker ID.

### External work

The primary agent may directly inspect authorized source/data, make permitted changes and verify them. PCW adds no mandatory intermediary. When delegating, provide a self-contained task with needed facts as text, allowed sources, constraints, acceptance checks and expected return. Do not pass knowledge/Current IDs or paths, the PCW journal, memory-management instructions or inherited PCW conversation history.

The assignment explicitly prohibits PCW calls and discovery/read/write of `.pcw`. This is an instruction boundary, not a claim of technical filesystem/tool isolation. The worker reports to the primary agent, which decides all shared-memory changes.

PCW does not require host registration, client version collection, tool/model lists, reasoning levels, launch settings, environment snapshots or a model-selection map. These are not prerequisites for open/search/write/index. It neither launches workers nor requires registered assignments, linked worker sessions or a separate verifier. The embedding profile below is unrelated to host-agent discovery.

### Short memory cycle

Separate instructions from operation calls. Do not resend complete unchanged rules at every operation. Ordinary writes return ID, revision, links, status and exact unfinished actions; substantive reads return bodies. The plugin verifies the stored revision before reporting success. If that response establishes the intended save, another immediate read solely to acknowledge it is unnecessary; read to assess meaning or resolve uncertainty.

Batch independent materials with individual outcomes and explicit partial success. Opening returns Current, pending operations and materials awaiting preparation, not all past actions/results or irrelevant settings.

### User command and authority

Use one `$pcw` skill, not a separate `$pcw-run`. Preparing context does not execute a future task described in it. Reading a rule, source link or saved plan does not authorize external modification, installation, publication or a Git commit. Respect actual current and previously granted scoped authorization.

### MCP startup and dependencies

The historical plugin declares its local server in root `plugin.json` and `mcp.json`. Codex expands `${PLUGIN_ROOT}/scripts/launch.sh` before invoking `sh`; the script does not rely on that variable already existing in the environment. `.codex-plugin/plugin.json` remains compatible metadata without a second legacy `mcpServers` transport. Manual `codex mcp add` is unnecessary. A startup failure is not grounds for replacing the Python storage/search implementation.

Use an explicitly chosen existing Python or available `python3`; no mandatory venv path. Startup neither creates an environment nor installs packages/downloads a model. On `$pcw`, the primary agent checks MCP and performs bounded installed-plugin/dependency diagnosis itself. A connection error alone is not its root cause.

Report missing components and destination. Install only under existing authorization or after obtaining it, including Python itself if missing. Install libraries into the interpreter actually used by PCW. Do not automatically create a venv. An explicitly agreed separate package directory may be used with an OS-protected system interpreter without disabling system-package protection; it is not a new interpreter or venv. Updated plugin files do not establish dependency readiness.

After permitted setup, retry startup and a real MCP connection, then resume the task. Release checks use a fresh actual Codex session to discover tools and read/write/search. A manually adjusted server environment or standalone server test does not prove the current client's connection. Use an available client recovery mechanism; if a session restart is necessary, say so and preserve the task. Do not invent a restart command.

Refusal, missing permissions, connectivity, disk space or installation failure are explicit blockers. Do not bypass refusal or call unsaved text saved. Knowledge files remain authoritative after index failure; MCP readiness does not establish local-model readiness. Qwen dependencies and the initial weight download require their own authorization. Reuse the loaded model in the running process rather than creating an environment/process for every text.

## 3. Data

### Real sources

Project files, code, documentation, data and other authorized objects exist independently of PCW. The primary agent or optional worker may inspect them. Saved knowledge describes observed source state; it does not prove that state is unchanged.

### Knowledge material

A material is readable Markdown about an independent subject, freely combining text, tables, code, conditions, limits and source coordinates. Save its complete body without embedding boundaries. Apply the same rules to worker-derived findings.

Before saving, separate independent subjects into coherent topics. Keep related facts with their conditions; do not combine architecture, independent storage rules and export behavior merely because one run discovered them. Retrieval for one subject should not require a whole multi-topic report. One file per isolated fact is not required. Token windows are an index detail, not topic boundaries. Small windows cannot repair a source topic that is too broad.

MCP generates the unique new ID and `.md` filename. The primary agent does not invent it; corrections to the same subject preserve identity. UTC save time, content revision and provenance distinguish observations. Worker metadata is included only for actual delegation with established identity.

Knowledge is not execution history: creation receipts, Current updates, chunk boundaries, waits and retry logs are not topics. An external artifact may need verification without copying it into memory. Retain useful findings, its necessary coordinates and actual limits; track operational state separately.

### Embedding fragments

A fragment is a literal contiguous token-selected span of saved text. Adjacent spans overlap. They retrieve their source material rather than replace it, and need not be semantically complete. Code uses the configured tokenizer/window/overlap; agents do not choose boundaries. No separate fragment files are created.

### Vector database

LanceDB stores each fragment's original text alongside its vector, material ID and current path, plus content revision, fragment position and chunking configuration. Multiple rows may point to the same material; overlap intentionally repeats portions. The full Markdown remains authoritative.

A material reference identifies saved knowledge, not automatically the external file that originally supported it. Search returns material IDs/current addresses; read resolves an ID and opens the whole material. Paths are derived addressing data, not immutable identity. Links come from knowledge files; no independent graph table becomes a second authority. Vector proximity suggests possible relevance, not freshness or an answer.

### Initial approved stack

- **LanceDB OSS:** embedded local storage without a separate database server; material references are metadata for precise updates. [Quickstart](https://docs.lancedb.com/quickstart).
- **Qwen/Qwen3-Embedding-0.6B:** approximately 0.6-billion-parameter multilingual text/code retrieval model, default 1024 dimensions. Support for lower dimensions does not select them for PCW. Code handles tokenization and splitting. [Model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).

The rationale was embedded storage and Qwen's published multilingual results at a relatively small size. One target computer and one fixed PCW corpus were measured in the [search assessment](search-assessment.md). Those measurements concern the former preparation scheme, not the new overlapping token windows. Comparative superiority over other stacks was not measured.

### Replaceable embedding providers

Local Qwen uses `sentence-transformers`, loaded lazily once per running process. Shutdown unloads it; a later process loads it again. Initial weights come from the model source; cached weights support later use. Never silently replace a failed local model with a remote service.

Providers accept prepared texts and purpose `document`/`query`, returning one numeric vector per input in order. Supported adapters are local sentence-transformers; Ollama `/api/embed` using `model`, `input`, `embeddings` and `truncate:false`; and compatible `/v1/embeddings` using `model`, `input` and `data[].embedding` ordered by `index`. Other protocols require adapters. Configure the endpoint/model and secret environment-variable reference explicitly. A chat model or chat subscription alone is not an embedding API, and a Codex subscription does not authorize paid API use. Keep secret values out of knowledge/Git.

Separate connection details from the vector-space profile: model/revision, dimensions, document/query formatting, normalization and computation settings. Bind the active LanceDB table to that profile and selection settings. Equal dimensions do not prove compatibility between models or variants.

Chunking separately specifies tokenizer/version, `chunk_size_tokens` and `overlap_tokens`. For an API, configure a compatible tokenizer and input limit. Do not assume Qwen's tokenizer matches another model. Window size includes document formatting and special tokens. Qwen queries use the instruction below; documents do not. Other providers use their configured format.

Reject unsupported input sizes and malformed responses; never silently truncate or fabricate vectors. The local adapter checks tokenizer length; Ollama receives `truncate:false`. `/v1/embeddings` has no universal no-truncation flag, so its server must reject oversized input. A returned vector alone cannot reveal hidden server truncation.

### Switching providers

Switch only on an explicit request:

1. Validate the new connection/profile and compatible chunking. Reject non-embedding sources without changing the active database.
2. Pause vector retrieval/index updates when model/chunking changes or compatibility is unproven.
3. Reuse stored fragments of current revisions only if chunking and length remain valid. Otherwise rechunk authoritative Markdown. Compute into an inactive new table without rewriting knowledge or involving agents in boundaries.
4. Atomically publish the provider/table configuration together. Queries and materials always use compatible profiles. Calibrate distance parameters separately rather than inheriting an allegedly optimal old threshold.
5. Remove the old search table and resume. IDs/links/knowledge remain unchanged; the temporary new table is an index replacement, not an archive.

If computation fails, retain the old active provider/table and report failure. Changing only a secret does not require embedding again. Endpoint/process changes can reuse vectors only with established computational compatibility; even the same model name or a quantized variant is insufficient evidence.

### Changes and old states

With authorization, Git retains old knowledge for inspection/restoration. Do not maintain separate old vector databases as rollback history. Reconcile fragment text, vectors, paths and links after restoration.

Apply changes to affected records. Body changes update text and search representation; link-only changes do not recompute embeddings. New/changed records become searchable after preparation; deleted/replaced versions must disappear. Mark revised text pending until preparation completes and never expose its old parts as current. Leave unchanged materials' embeddings intact.

Code manages LanceDB's acceleration index separately from embedding generation. Ordinary search also scans unindexed rows, so rebuilding the entire acceleration index after every write is unnecessary; do not use `fast_search` when it would omit those rows. [Vector indexing](https://docs.lancedb.com/indexing/vector-index).

Immediate physical reclamation is not required: replaced/deleted bytes may remain in LanceDB files but cannot appear in current results. Reclamation is separate maintenance, not a prerequisite for every save. No seven-day vector rollback archive is introduced; Git provides history. Changes/maintenance occur during plugin requests without a dedicated permanent maintenance process; no fixed interval is specified. [Update/delete behavior](https://docs.lancedb.com/tables/update).

### Current

Current has five fields: goal, constraints, completion condition, current step and blocker. The primary agent maintains them against the real request; no blocker means an empty field. Rewording the goal must not silently broaden authority.

Use Current to restore focus across sessions, not to log every action or control workers. Update on meaningful state/step/blocker changes, not unchanged rewrites. Pending memory operations remain separate. Reading Current does not replace knowledge search or prove completion; memory needs no mandatory worker registry.

## 4. Task cycle

1. Determine the actual request and authorized boundaries. Open the selected workspace, restore Current and inspect pending memory work.
2. **Before external investigation, retrieve accumulated context:** issue targeted vector queries, read selected coherent topics and useful links. Chat recollection does not replace reading relevant PCW materials. Load only needed content; if storage is empty, acknowledge it and proceed.
3. Assess sufficiency, applicability and freshness. Refine a deficient query, then investigate missing facts in authorized real sources instead of repeating fruitless search indefinitely.
4. Perform or delegate permitted work and verify substantive outcomes on real objects. Execution mode does not change memory rules.
5. **As substantive findings become available**, identify new facts, corrections, conditions and verification limits. Compare affected knowledge, remove established errors/duplication and prepare coherent topics. Do not defer all knowledge until the end of a long task.
6. Save through MCP and create useful links. Direct observations need no worker return. A complete repeat does not create another file.
7. Code chunks saved new/revised bodies, obtains embeddings and updates the index automatically. The primary agent neither resends the same text nor creates chunk arrays or a helper task. Unchanged materials remain untouched.
8. Check real save/search statuses, update Current where necessary and report results/limits. An external success does not complete pending memory save/preparation/verification.

Preserve conclusions before the final answer, updating an existing topic rather than duplicating it. On interruption retain already established findings and specific unfinished state. No extra worker or verifier is required merely to close the memory loop.

## 5. Preparing vector search

Save complete ordinary text. Code, not an agent/model, selects overlapping token windows:

1. Capture saved text and revision; start preparation as part of the write.
2. Use the model-compatible tokenizer. Window size must be a positive integer; overlap a positive integer smaller than it. Formatted input must fit the model. Reject invalid settings rather than silently fixing them.
3. Advance by `chunk_size_tokens - overlap_tokens` until the end. The last window may be shorter; do not create a trailing overlap-only window. Short nonempty text creates one fragment; empty text creates none.
4. Extract literal source spans preserving characters, spaces and line endings, with complete ordered coverage and only the intended repeated overlap. Do not paraphrase/normalize or change the Markdown.
5. Store each fragment's text/vector/positions/material ID/revision/chunk configuration in LanceDB.
6. Recheck content revision/settings before publication. Never activate results for stale inputs. Report `indexed` only after all parts for that material have actually been updated.

Boundaries may cut paragraphs, tables or code. Overlap provides neighboring text, not guaranteed semantic completeness. The whole material is unchanged, and agents still own topical structure/links.

Batches report each material separately. Chunk metadata creates no knowledge files. Tokenizer/window/overlap changes require automatic preparation from saved bodies; unchanged body/profile/chunking needs no recomputation. Pending revised bodies remain readable after tokenizer/provider/index failure, with explicit unfinished preparation. Resume from saved text/settings without handing splitting back to agents. Link/path-only changes require no new embeddings.

## 6. Search

Query for the needed fact or new result to compare, whether it came from a worker or direct work. Use a profile compatible with stored vectors. Historical Qwen formatting:

```text
Instruct: Given a query, retrieve relevant technical documentation.
Query: What happens to the completed CSV file when writing fails?
```

The task instruction is English; user queries may be multilingual. Document fragments receive no search instruction; query vectors do not become knowledge. Results identify candidates by ID/path/distance; read relevant bodies and only necessary links. Closeness is not authority or certainty.

### Historical distance selection

This archived algorithm applies a nearest-fragment cap **before** deduplication; later designs replaced that behavior. `N` caps candidates; `d_max` rejects weak matches even when they are the nearest available; a distance jump bounds the relevant prefix.

1. Retrieve at most `N` nearest LanceDB rows ordered by ascending distance, without reading every topic body.
2. Remove `d > d_max`. Empty results mean no accepted candidates for this query, not proven absence of knowledge.
3. Compute adjacent differences, take the median of positive differences and set the jump threshold to `max(min_jump, median * multiplier)`.
4. Keep the prefix before the first gap at least that large, excluding the farther item and following tail. For `0.10, 0.11, 0.12, 0.35, 0.36, 0.37` and threshold `0.03`, retain the first three.
5. If there is no jump, return the remaining bounded list. With fewer than three candidates or no positive differences, do not apply jump cutting.
6. Deduplicate material references; the primary agent compares actual content.

The reference was `reference-analytics/vectorstore.py::_search_collection`, with minimum jump `0.02` and multiplier `3`. Those source values were not automatically approved for Qwen/PCW. Unlike reference `get_context`, this design requests a bounded result, not the whole collection, and calculates the median after the maximum-distance filter.

No jump means `N` remains the cap, not proof that later candidates are irrelevant. Refine the query/focus when needed rather than appending a previously rejected weak tail. Read selected PCW materials even if similar statements are remembered in chat.

For one fixed historical PCW corpus, Qwen revision and cosine metric, the calibrated starting profile was `N=10`, `d_max=0.45`, `min_jump=0.01`, `multiplier=2`. Selection used 24 queries; 12 held-out queries were evaluated afterward. Complete answers were found for 6/8 positive queries, all 4 negatives were rejected, and Russian positives scored 2/4. See [search assessment](search-assessment.md). This does not validate the new overlapping windows, change existing settings/defaults or transfer to another corpus/model/metric/chunker without calibration.

The reference MCP's extra “plateau” rule was not adopted: one close distant pair did not establish a sustained flat tail. Without a jump, the accepted limits are `N` and `d_max`.

## 7. Consolidating knowledge

Consolidate while processing new findings/corrections, without mandatory storage of every raw report or artifact. Use read knowledge plus new facts, retrieving missing topics as needed:

1. Determine whether the result adds, changes or repeats information. Similar vectors alone do not justify a merge.
2. Do not duplicate complete repeats. Add details to a coherent existing topic while retaining conditions/useful facts; separate independent subjects.
3. Consider extracting genuinely shared descriptions and linking them. Preserve version/environment/condition/result differences.
4. Correct established errors. Retain unresolved contradictions with their evidence and uncertainty.
5. The primary agent saves final text and links through MCP. Complete replacement supplies explicit old-ID-to-final-ID mappings.
6. Prepare changed bodies for retrieval without embedding unchanged text again.

### Cost of representation

Storage volume and context read for a particular question are distinct measures. Avoid unnecessary sections through topic separation; small metadata overhead does not justify a multi-topic report.

Factoring common text adds residual text, links, metadata and possible extra reads. For a short repeat that cost can exceed duplication; do not extract it automatically. The primary agent may calculate costs without a mandatory worker. If prior query data exists, compare the same queries including necessary link traversals. Otherwise do not claim measured reading savings. For example, replacing 1000 tokens with 400 common + 200 specific + 30 metadata yields 630 stored text tokens, but proves neither reading savings nor semantic preservation. LanceDB text/vectors also consume space; smaller Markdown does not necessarily mean a smaller database.

Consolidation must change working material rather than add a summary beside unchanged duplicates. Do not delete facts merely unused by the current task, or rewrite the whole database after every finding. Preserve contracts, operation order, state transitions, race protection, exceptions and applicability. “Fixed,” test lists or a Git link cannot replace usable current knowledge.

## 8. Links and navigation

### 8.1. Meaning

A link says that another material may be useful to read. In this historical design it does not assert dependency, containment, contradiction or applicability, and needs no mandatory relationship type/explanation/confidence/vector. The primary agent chooses links; actual conditions and dependencies belong in the body. A navigation graph is not a continuously verified dependency graph. Assess applicability when using content, not through a mandatory global relationship audit.

### 8.2. Identity and representation

MCP generates the ID/`.md` filename; `pcw_create` takes content/links rather than an agent-selected filename. Corrections, titles and moves preserve ID; a different subject receives a new one. Existing filenames are not silently changed.

YAML `links` contains destination filenames, or an empty list. This historical format has no separate YAML `id` because identity is the filename. The metadata format is consistent while the body remains free-form. A link starts at its containing file and ends at the exact destination ID. Remove duplicates and self-links; reciprocal links are optional. Knowledge files are authoritative; no relation files or separate graph ledger are required. Git retains authorized versions.

Read resolves IDs through recoverable address metadata. A move updates paths in address/search rows while preserving ID and incoming `links`. Changing IDs is a separately authorized migration. Unchanged bodies are neither rechunked nor re-embedded.

### 8.3. Owners

The primary agent chooses useful links, compares new findings, defines merged/common content, saves exact source/target IDs and declares replacements. The plugin never infers replacement from text similarity or simultaneous creation/deletion.

Tools locate exact IDs in `links`, update lists/addresses and apply explicit additions/removals/reassignments. They do not rewrite factual claims, merge meaning autonomously or ask a language model to perform mechanical mass-ID replacement. Chunking and embeddings do not create links. In this design external workers neither receive memory coordinates nor maintain it.

### 8.4. Adding links

After selecting related topics, save their bodies to obtain IDs, then submit source and target IDs. Add them without disturbing other links; repeated additions are idempotent, unknown IDs fail rather than approximate a destination. Save the metadata without recomputing unchanged body embeddings. Removing a link deletes neither its destination nor other incoming links. No mandatory all-pairs linking pass is required.

### 8.5. Following links

Search identifies candidates; read a selected material with `links`. Use useful destination IDs for exact reads, optionally displaying target titles without authoring separate relationship descriptions. These traversals need no new vector query or threshold test. Read the target body rather than transferring conditions based solely on the link. Continue to relevant neighbors even when absent from the original search, but stop when enough evidence is available and avoid re-reading unchanged materials in a cycle.

If navigation is insufficient, refine the query or investigate authorized real sources. Missing IDs produce explicit errors, never nearest-vector substitutions; a separate search does not silently repair a broken link. External project-file references are a different source layer. When delegating, pass needed facts as text rather than memory coordinates; the primary agent incorporates the return.

### 8.6. Updating without changing identity

Changing a body does not trigger semantic reevaluation of all incoming/outgoing links. Preserve lists unless explicitly changed; update only the revised search representation. Remove a useless link or correct a neighbor when the task establishes a reason. Multiple affected topics may need updates, but a link alone does not authorize rewriting neighbors.

### 8.7. Merge and full replacement

The primary agent supplies final content and mappings; the plugin applies them:

1. Choose an existing survivor or create a new final material.
2. Supply every replaced ID's final destination.
3. Save the final body. For a full merge, inherit outgoing links unless an explicit final list is supplied; remove duplicates/self-links.
4. Find incoming references by scanning exact YAML IDs, not model-reading or vector-searching the whole corpus.
5. Replace old IDs, deduplicate and remove new self-links. Do not alter arbitrary body mentions or external-file references.
6. Only after final save and incoming-link updates, delete replaced files/search rows and publish the survivor's new representation. Metadata-only neighbors need no new embeddings.

Do not create permanent redirect files for every old ID. Preserve history in Git only when authorized. On failure report actual partial effects rather than a successful merge. Partial extraction with the original remaining does not redirect all incoming links. If one removed topic has several successors and no unique destination, the primary agent must specify the necessary reassignment; code cannot choose by similarity.

### 8.8. Deleting without replacement

The primary agent decides deletion and supplies an exact ID. Code removes incoming `links`, saves those lists, then removes the target, search rows and addressing data. Its outgoing links disappear with it, without deleting destinations. The delete request includes mechanical removal of navigation pointers; that differs from a dependency graph, where removing an edge cannot resolve a dependency. Do not claim remaining facts are correct merely because dangling links are gone. Known related factual errors require substantive corrections.

### 8.9. Automation boundary

Find backlinks from authoritative `links` rather than maintaining forward, mirror and separate graph records. Automate exact addressing, scans, list writes, deduplication and declared substitutions. Meaning, consolidation, replacement choice and deletion remain agent decisions. Run on request, without a permanent background monitor, source checksums, extra workflow ledger or mandatory semantic graph traversal.

## 9. Reality checks and corrections

Knowledge describes past observations, not an immutable environment. Consult it first and verify task-relevant claims when the work or a discrepancy requires it; do not recheck every source before every task.

### Discrepancies

1. Establish the cause within authorized scope. A wrong path, changed interface, denied access and command failure are distinct; failure alone does not prove obsolete knowledge.
2. Continue within the request using verified state. If a decision/permission is missing, preserve findings and the exact blocker rather than expanding the task on a hypothesis.
3. Separate observation from hypothesis with conditions/source/method. A worker returns corrections to the supplied facts without needing memory IDs.
4. Compare and correct active materials through MCP rather than leaving known-false text beside a corrective note.
5. Preserve differences that belong to distinct versions/conditions, reindex changed bodies and avoid a mandatory review of every neighbor.

### Sufficient verification

Choose evidence appropriate to the claim: actual file/link/data inspection, reproduction or relevant tests. Worker success statements and MCP status do not prove external outcomes. Check substantive milestones and discrepancies; checks need not be separate worker assignments or repeated after every return/save. Reuse adequate completed verification.

A small note change may need only readback, link checks and preservation comparison against the actual pre-edit state, not automatically Git HEAD. Behavior claims need appropriate execution evidence. Static inspection, builds and published-instance checks have different limits.

Retain useful observations, method, conditions and limitations, not call chronology or a bare “verified.” Do not accept incomplete work; resolve it through suitable investigation, including direct primary-agent reading, without creating a technical report in knowledge merely for compliance.

Incomplete tasks retain established findings. Suspected causes are not proven fixes; unknown conditions stay unknown and discrepancies cannot be hidden by completion status. Detection depends on tested objects/conditions/methods; no universal freshness guarantee or permanent memory-verifier agent is introduced.

## 10. End-to-end example

This is illustrative; actual filenames are MCP-generated IDs.

1. A user requests a CSV rule change. Restore Current, search/read CSV knowledge, then follow a useful link to temporary-file/rename saving. Do not read the whole JSON topic unnecessarily.
2. Inspect authorized code, discover and verify that the function moved, change the permitted rule and check the output CSV. A worker is optional.
3. Update the CSV topic under its existing ID with location, behavior and verification conditions. Do not rewrite unchanged generic saving/JSON topics.
4. If independent error-handling rules were established, create a separate topic and useful links, not one chronological report.
5. Code automatically chunks/embeds revised text, validates revisions and updates affected rows; the agent supplies no repeated text or boundaries.
6. Check memory statuses, update Current and report actual results/limits. A later CSV query reaches a focused topic and relevant links.

Delegating step2 changes neither memory ownership nor quality rules: supply facts, receive the result and perform the same memory maintenance.

## 11. Excluded mechanisms

- Host/model/tool inventories, environment snapshots and mandatory worker-launch maps.
- Mandatory delegation, worker model profiles, assignment registries or session binding as prerequisites for memory.
- PCW worker launching, a controller for the entire external task, or executing future work merely because context was prepared.
- Delegating knowledge/Current/link/compaction/deletion/Git-checkpoint ownership to external workers.
- Mandatory raw-report/artifact copies or execution histories in knowledge.
- A mandatory retrieval intermediary or a new verification task after every result.
- Claims of technical isolation based only on an assignment prohibition.
- Multi-topic reports made acceptable merely by tiny embedding chunks, or replacing full topics with vectors/paraphrased fragments.
- Agent/model-selected embedding boundaries or mandatory fragment arrays on ordinary saves.
- Autonomous semantic merges, similarity-based replacement destinations, mandatory link types/explanations, duplicate graph stores or manually maintained mirror links.
- Source-wide checksums, continuous dependency monitoring or one special check per conclusion as a universal freshness guarantee.
- Summary hierarchies that leave duplication untouched, re-embedding unchanged material, or indexing operational receipts.
- Calling a result correct merely because a procedure exists, MCP returned success or some tests passed.

## 12. Deferred decision

Automatic detection of unnoticed divergence between knowledge and reality is unspecified. Until separately agreed, retrieve accumulated context first, verify substantive facts as needed and correct observed discrepancies. Lack of automatic monitoring does not excuse leaving a known correction unsaved.

## 13. Implementation and acceptance requirements

These are future conformance criteria, not evidence that runtime, MCP schemas, skill or installed plugin changed.

The primary agent must read/search/create/update knowledge, links and Current without environment/worker registration or invented IDs. Direct observations retain real provenance. Automatic chunking is storage preparation, not an external-task controller. Writes validate revisions and actual persistence; batches expose partial errors. Do not impose redundant reads/rule reloads on every call. Reconcile addresses, links and index after file/Git restoration. A checkpoint requires actual authorization; lack of prohibition is not consent and an unauthorized commit cannot be a completion condition.

End-to-end checks:

1. **Direct work:** restore Current, find knowledge, inspect an authorized source and save coherent findings without host discovery, worker launch/registration or worker IDs.
2. **Formation/retrieval:** save complete text, automatically create literal overlapping windows/vectors, then retrieve one focused subject with useful links rather than a multi-topic report.
3. **Correction:** repair a known false statement, preserve conditions/identity, update only affected search rows and exclude the former revision from current results.
4. **Links/recovery:** create/move/merge/delete by exact ID; verify intact links after success, truthful partial failures and resumable preparation.
5. **Optional delegation:** provide sufficient facts without memory coordinates; all PCW calls remain with the primary agent. One compliant run does not establish technical isolation.
6. **Chunking:** test size/overlap/step, short/empty/last windows, invalid configuration, complete literal coverage including multilingual text/tables/code, stale revisions/provider failure and no embedding on link-only edits. Do not create knowledge files for service results.
7. **Providers:** test default Qwen, another local model and keyed API; compatible tokenizers/limits; reindex on profile/chunk changes; compatible query/document vectors; preserve the prior working index after switch failure.
8. **Small real task:** measure the complete cycle's time, calls, transferred context and actual result. A small MCP response or fast synthetic test does not establish user-task acceleration.

Documentation changes, programmatic tests and observed agent behavior are separate results. Verify implementation and actual use independently; earlier releases' evidence does not automatically validate this design.
