# Find a context and retrieve saved information

Use this scenario for “Which project covers this service?” or “What retry policy did we agree?” Obtain `guide(procedure="find")` for discovery or the independent-reading path of `guide()` for a question in a known context. Neither path creates a context or work cycle.

`workspace.contexts(name=...)` checks a known name or context path without embeddings. Omit `name` to list actual contexts and follow pagination. When the name is unknown, `workspace.projects_search(query=...)` searches indexed project records. Read the selected description with `workspace.project_read` before assuming purpose or boundaries.

Keep the selected `work_root` and `context` outside operation `arguments`. Unresolved candidates need clarification. A stale index, missing description and no matching context are different findings. A repository name or empty semantic result never creates or automatically selects a context.

For a new independent task, read `workspace.settings_read(fields=["reread_instructions"])`, then the complete `workspace.instructions_read`. Reuse complete current rules for the same task only as the effective policy permits. Read the project when its scope matters.

Call `workspace.search(query=...)` with inherited algorithm and limits on the first search. `types` and `statuses` filter eligible records before vector selection. `categories` or `all_categories` broaden evidence across contexts while preserving the active rules and settings. Check scope, counts, boundary, candidate completeness and pending records. Distance is not confidence; a result cap is not a relevance boundary.

Read useful bodies with `workspace.read(material_id=...)` or `workspace.read_many(material_ids=[...])`. The latter takes `material_ids`, not search result `keys`. Ordinary compact reads retain bodies; full search detail serves a specific diagnostic need. Follow useful navigation links and freshly check every declared required source before relying on dependent facts.

Example: search the retry policy, read it and its linked limit record, then answer with their conditions. Report missing evidence and investigate an observed retrieval limitation. An independent question needs no `.tmp`, proposal or finish call.

Implementation references: `package/workspace/discovery.py`, `package/workspace/cycles.py`, `package/workspace/runtime.py`, `src/contextopus/core/rag.py`. Detailed procedures: `find`, `workspace`, `search`.
