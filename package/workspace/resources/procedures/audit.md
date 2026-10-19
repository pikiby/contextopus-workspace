Use the obtained applicable procedure before acting. Calls use `read` for queries, `write` for changes, and `guide(procedure="name")` or `guide(operation="module.operation")` for a needed procedure or unknown signature. Preserve the selected work_root/context.


# Audit context

Use `read` for queries and `write` for changes under the ordinary [Workspace procedure](procedure:workspace). The names below are operations; place their parameters in `arguments`. Describe only an unknown signature.

1. Establish scope and follow [context selection](procedure:workspace). Read INSTRUCTIONS and the project record independently. An audit needs no work cycle; if one is useful, inspect existing work first and supply the exact user_request with `purpose: audit`.
2. Use `audit` for structural checks. For an entire-context request page through all results and `list`; vector top-N is insufficient. Read necessary complete materials and selected links. Opening must not migrate, repair indexing or rewrite them.
   On every receipt of a material with `sources` (or legacy `required_sources`), follow the [required-source procedure](procedure:reference/required-sources); earlier checks never substitute.
3. Assess requested semantic issues against current instructions and sources: mixed questions, redundant claims, contradictions, unsupported claims or unnecessary detail. Distinguish observed errors from hypotheses requiring evidence. A large file alone does not prove mixed meaning.
4. For every finding show material/title, exact revision, concrete fragment, problem and its basis. Include a related material or rule when applicable. State scope and unresolved evidence. Do not claim a model judgment is a deterministic check.
5. Present the report in chat. If an audit cycle was opened, finish it after reporting under cleanup_approval_required, without a knowledge proposal. Audit does not authorize fixes, rule changes or permanent retention of the report. Rework starts only when requested.
