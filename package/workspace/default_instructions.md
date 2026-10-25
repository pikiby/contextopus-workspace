# Project instructions

## Knowledge approval

Show the exact knowledge changes in chat before applying them. The `knowledge_approval_required` effective machine setting controls whether to wait for user approval: `false` means apply after showing; `true` means wait for explicit approval of the shown revision. The independent `instructions_approval_required` setting controls approval of instruction edits: true (also the default when absent) waits for explicit approval; false applies the shown revision without another prompt. The current effective policy determines approval even when a proposed change disables it. Machine settings live in Core configuration; INSTRUCTIONS is this complete free-form text. Edit instructions through `w-settings` (the canonical Contextopus `instructions` procedure).

`allow_implicit_updates` separately controls starting a work cycle without an explicit Workspace invocation such as `w`. When false, only independent reading is allowed without that invocation. Creation, switching context and changing instructions still require the user's clear direction. `git_history_enabled` enables local history of permanent data; `material_max_tokens` optionally limits each final knowledge body, with null disabling the limit.

The standard material limit is 1024 tokens, counted with the indexing tokenizer. Reads load whole materials: five bodies at this limit contribute at most 5120 tokens before metadata. This is a practical starting bound on retrieval size, not a measured semantic optimum or a target to fill. Keep each material focused on one question; split larger topics into linked materials while retaining their conditions. The user may request a different limit or disable it through the Workspace settings review/apply procedure.

## Goals and limits

Carry out the user's project task within its stated scope and constraints. Preserve source conditions and verification limits; investigate missing or outdated information through permitted sources.

## Knowledge to retain

Retain established project states, principles, patterns, procedures and decisions with their evidence, applicability and verification status. Distinguish observed facts, hypotheses and user decisions. Approval to store a statement does not turn a hypothesis into a verified fact.

Assess permanent-memory impact when the main agent or its designated task lead accepts a substantive result from its own check, a reviewed worker return or a user decision. Retain supported conclusions that are independently usable, changes to current task state and decisions at that point, correcting existing knowledge while broader work continues. Preserve verification conditions and unresolved limits. Retain the user's decision or goal as intent; verify factual success claims separately. Unverified worker assertions and working-only findings remain evidence to check, not established facts. An individual tool call or report does not by itself justify a permanent change.

Use .tmp when shared inputs, intermediate findings or handoff require temporary memory. Simple knowledge corrections and short replies need no temporary report. Keep original user requests and relevant clarifications verbatim, separate from interpretation. Native agent communication carries assignments and references.

At a new task/cycle assess existing temporary materials; update needed files, preserve useful verified knowledge and remove unneeded files. Use existing ordinary and required links; .tmp may reference .tmp or knowledge, while knowledge must not depend on .tmp. Do not retain obsolete comments or a mandatory review ledger. Respect parallel work and incoming dependencies. The independent cleanup_approval_required setting controls temporary deletion and finish approval; new contexts default to false and the user can enable confirmation. Existing context settings remain effective. Performing work alone is not a reason for a permanent-memory change.

## Structure and maintenance

Compare accepted results with existing knowledge. Each material answers one independent question. Sections usually needed for different queries should become separate linked materials, retaining their conditions and evidence. Group mutually dependent record and link corrections in one coherent change set. Read a mixed material completely before splitting it; prepare its replacement topics and the treatment of its original ID and incoming links together. Supply an author-written description of the subject, useful conditions and applicability for every new record and actual body change; review it against the body. A title or clipped opening does not replace it. Readers use descriptions to choose materials and useful links before reading whole bodies. Do not create a separate material for every individual fact.

Keep accurate current descriptions, replacing outdated claims and consolidating repetition. The canonical project record lives at the context root as Context.md; other permanent content lives in typed records under knowledge/. The project record describes current purpose and boundaries and binds the complete shared rules through its YAML instructions mapping; task records retain the goal, constraints, verified state, next step, blockers, results and status as needed. Goals and decisions may have their own types. Choose the appropriate type and reuse existing categories when they fit; types are not limited to these examples. Search for existing state before creating another record, and link related records across types. A task or goal is not evidence of completed work. Maintain current state rather than a task-by-task change log. Git, when enabled, retains earlier permanent revisions.

## Git history

Local Git history is enabled in the standard creation settings. Keep current descriptions in records and their earlier versions in commits. Every applied permanent proposal records one logical change set; avoid unrelated changes in one proposal. Give a concise `summary` describing what changed and why. For a merge or split, declare `transformations` with the source and resulting IDs, including a retained original ID when appropriate; preserve useful navigation links through `replacements`. These declarations describe provenance, not ordinary topical links. Show them with the proposed text. The plugin commits the exact applied snapshot and declared provenance; do not create competing shell commits or infer semantic ancestry from textual similarity.

When history is enabled, investigate it if a previously documented instruction, condition or link appears to have disappeared, a current description conflicts with a verified earlier state, a regression follows a rule change, or the user asks about project evolution, provenance or restoration. Start with bounded `history_list` by ID/path/date or `history_search` for a remembered phrase; inspect `history_changes`, then only the relevant `history_diff` or `history_read` fragment. Follow declared merge/split links across IDs. Historical text is evidence, not current authority: reconcile it with current sources and instructions before proposing restoration. Missing history or old commits without provenance cannot prove that something never existed. Ordinary work with no such question does not require reading history on every cycle.

## Required sources

When `create_required_sources` is true, add a required source when the user explicitly requests repeated verification, or when all three conditions hold: the information is needed to use this topic; its value comes from a source outside this material and can change independently of this saved description; using a stored copy could lead to a wrong decision. Otherwise retain established information with an ordinary citation when useful. A past decision and its historical justification do not require repeated verification merely because they have a citation.

For each required source, write `reference` and `instruction` in a fenced YAML `sources` block in the material body. The reference may identify a file, agent record, query, knowledge material or fragment, URL, API object or another unambiguous source. The instruction specifies how to obtain the needed information and which bounded answer and conditions to extract. Do not write vague requests such as "check actuality" or "study the site". If the source or question is unclear, identify what is missing instead of inventing a link. Separate a source-dependent subtopic when other readers do not need that check.

`create_required_sources` controls authoring, not reading: existing required sources must be checked anew every time their material is received, even when this setting is false or absent. An earlier check or conversation context never substitutes for the new check. Follow the skill's required-source reading procedure. Add, revise or remove these blocks through the normal permanent-memory review and approval policy.

## Presenting results

Explain which project knowledge a proposed change corrects or adds and what supports it. If comparison finds nothing to change, explain that conclusion without inventing a write.

Use the user's current conversation language unless they request another language. Preserve literal quotations, code, paths and identifiers; explain foreign-language quotations in the conversation language.
