---
operations: ["workspace.contexts", "workspace.project_read", "workspace.projects_search"]
---

# Find a context

Use the connected MCP server. Discovery does not require a selected Workspace, a storage path or a work cycle and does not change another session's selection.

- Known context name: call `workspace.contexts` with `name`. It lists existing contexts without embeddings. Follow pagination when listing all contexts.
- Unknown name or a question about which projects exist: call `workspace.projects_search` with the user's semantic query. The common RAG filters to `type: project` before nearest-result selection and returns descriptions, compact diagnostics and context names. Do not read every project description.
- Read the chosen description with `workspace.project_read`, passing its exact workspace outside `arguments`. This reads the canonical project record and its source declarations. Follow required sources on each receipt. Missing descriptions and unavailable/stale index entries are limitations, not proof that no such project exists. If format upgrade is needed, follow the upgrade procedure for the identified context.

For a discovery-only request, show candidates with their names and relevant descriptions. For an explicit work request, a uniquely identified requested context may be selected by name; ask when ambiguous. Do not use a filesystem path as the context identifier. Never create a context from an empty search or infer its identity solely from the repository name. Accessing another context's records does not replace the selected context's rules or write destination.
