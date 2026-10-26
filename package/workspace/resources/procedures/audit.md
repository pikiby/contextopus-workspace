---
operations: ["workspace.audit", "workspace.finish", "workspace.instructions_read", "workspace.list", "workspace.project_read", "workspace.read", "workspace.read_many", "workspace.settings_read", "workspace.status"]
---

Use the obtained applicable procedure before acting. Calls use `read` for queries, `write` for changes, and `guide(procedure="name")` or `guide(operation="module.operation")` for a needed procedure or unknown signature. Preserve the selected context name; the server owns storage placement.


# Audit context

Use `read` for queries and `write` for changes under the ordinary [Workspace procedure](procedure:workspace). The names below are operations; place their parameters in `arguments`. Describe only an unknown signature.

1. Establish scope and follow [context selection](procedure:workspace). Read INSTRUCTIONS and the project record independently. An audit needs no work cycle; if one is useful, inspect existing work first and supply the exact user_request with `purpose: audit`.
2. Use `audit` for structural checks. For an entire-context request start `audit(scope="all", request_id=<fresh ID>, limit=50)` and follow next_cursor, without supplying offset or a changed scope. The frozen inventory includes Context.md and knowledge files; malformed, unregistered or unreadable entries remain explicit diagnostics. Retry the same start ID and bounds to reuse the inventory. Read necessary complete materials with `read_many(material_ids=[...], scope_id=<returned scope>)` and inspect relevant links. Unique catalog/body counts measure delivery only; they do not prove analysis. Changed or missing bodies remain unresolved against the original snapshot. To inspect subsequent changes call audit with scope_id and delta_refresh=true, then page that delta_cursor; a delta does not silently replace the original inventory. Expired scopes require an explicit new request ID. Opening must not migrate, repair indexing or rewrite materials.
   On every receipt of a material with `sources` (or legacy `required_sources`), follow the [required-source procedure](procedure:reference/required-sources); earlier checks never substitute.
3. Assess requested semantic issues against current instructions and sources: mixed questions, redundant claims, contradictions, unsupported claims or unnecessary detail. Distinguish observed errors from hypotheses requiring evidence. A large file alone does not prove mixed meaning.
4. For every finding show material/title, exact revision, concrete fragment, problem and its basis. Include a related material or rule when applicable. State scope and unresolved evidence. Do not claim a model judgment is a deterministic check.
5. Present the report in chat. If an audit cycle was opened, finish it after reporting under cleanup_approval_required, without a knowledge proposal. Audit does not authorize fixes, rule changes or permanent retention of the report. Rework starts only when requested.
