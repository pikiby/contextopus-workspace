# Find a context

Use the configured common memory root. Discovery does not require a selected Workspace or a work cycle and does not change another session's selection.

- Known name or absolute context path: call `workspace.contexts` with `name`. It lists actual context directories without embeddings. Follow pagination when listing all contexts.
- Unknown name or a question about which projects exist: call `workspace.projects_search` with the user's semantic query. The common RAG filters to `type: project` before nearest-result selection and returns descriptions, compact diagnostics and exact context paths. Do not read every project description.
- Read the chosen description with `workspace.project_read`, passing its exact workspace outside `arguments`. This reads the canonical project record and its source declarations. Follow required sources on each receipt. Missing descriptions and unavailable/stale index entries are limitations, not proof that no such project exists. If format upgrade is needed, follow the upgrade procedure for the identified context.

For a discovery-only request, show candidates with their names, full paths and relevant descriptions. For an explicit work request, a uniquely identified requested context may be selected; ask when ambiguous. Never create a context from an empty search or infer its identity solely from the repository name. Accessing another context's records does not replace the selected context's rules or write destination.
