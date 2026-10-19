# Contextopus: shared memory for agent work

Contextopus keeps reusable project knowledge outside a single conversation. One Core owns shared RAG, settings, resources and typed operations. Workspace is a separately installed module that adds contexts with their own files, complete INSTRUCTIONS and work cycles. CLI, TUI and native agent clients use the same operations through a local MCP owner.

MCP exposes `guide` for procedures/signatures, `read` for queries, `write` for mutations and `finish` for closing a named work cycle. Calls keep `work_root` and `context` outside operation `arguments`; for example, `read(operation="workspace.search", arguments={"query":"retry policy"}, context="example")` queries that selected context under its procedure.

Each context has an indexed project record and permanent Markdown records under `knowledge/`. A record's YAML `id` stays stable when its descriptive filename changes. `type` is open vocabulary: knowledge, project, goal, task, decision, guide and template use the same operations; `status` is optional. Plans and goals remain intentions until evidence establishes completion.

INSTRUCTIONS contain complete free-text rules; machine policies and retrieval settings live in Core configuration. Contexts share the index while keeping separate files and rules. Filtering retrieval to another context supplies evidence without changing the active instructions or write destination. Modules use this same Core rather than a separate vector database.

Git records permanent versions when history is enabled. SQLite journals record operations and observed evidence. Neither captures every outside action or proves an agent understood a record. Optional `.tmp` cards hold inputs and handoffs for unfinished work.

Core keeps its operational journal in `core/analytics/operations.sqlite3`. Workspace owns `core/storage/modules/workspace.sqlite3`; Session Search and other modules receive their own module SQL files through the same storage service. The embedding model and RAG remain shared. Installing, disabling or updating a module uses the ordinary module lifecycle; disabling keeps its user data.

## Typical scenarios

- **Find a project or answer from saved facts:** the retrieval guide covers context discovery, independent reading, filters and complete record bodies. Empty results never authorize creating a context.
- **Work and retain a result:** the Workspace guide covers current rules, matching cycles, typed records, reviewed proposals and separate finish/cleanup.
- **Share an assignment:** save reusable inputs in that cycle's `.tmp` and pass the reference and applicable rules to native workers.
- **Verify a condition or earlier decision:** the evidence guide separates required sources, Git versions and operation analytics.
- **Connect an agent:** the integrations guide covers Codex, Claude Code, OpenCode, Crush and Hermes using the same service.
- **Add domain operations or tune retrieval:** the module and algorithm guides describe their separate contracts and limits.

These are reference cards. Obtain the applicable installed procedure through `guide()` or `guide(procedure=...)`; use `guide(operation=...)` for an unknown signature. Reading product documentation never selects its context, starts work or grants change permissions.

Implementation references: `src/contextopus/startup.py`, `src/contextopus/core/storage.py`, `package/workspace/operations.py`, `package/workspace/base_context.py`. Detailed procedures: `workspace`, `find`, `reference/memory-operations`.
