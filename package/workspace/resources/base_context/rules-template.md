# Context instruction template

Use this checklist to propose rules for one selected context. Read its current INSTRUCTIONS and preserve rules outside the user's direct request.

```yaml
allow_implicit_updates: false
knowledge_approval_required: false
instructions_approval_required: true
cleanup_approval_required: false
git_history_enabled: true
material_max_tokens: 1024
create_required_sources: false
reread_instructions: false
```

State the context's purpose, boundaries, evidence requirements, source checks, delegation requirements and desired presentation in the complete free-form INSTRUCTIONS body. Store machine settings in Core configuration. Read effective values and origins with `workspace.settings_read`; review and apply their exact revision through `workspace.settings_preview/settings_apply` under the current instructions-approval policy. The packaged documentation context starts with a null size limit; ordinary contexts start with 1024 tokens. Legacy migration preserves effective values, including true approval defaults for old absent flags.

Obtain `guide(procedure="create")` before creation and agree on the standard choices or requested customization. Supply an author-written project description with the project body. For existing contexts, use the instruction-edit procedure only on a direct request: read complete rules, prepare the complete revised text, present exact changes, then apply the shown revision under the current policy. A proposed false approval value cannot waive current true approval. Reading this template does not authorize changes to any context.

Keep `reread_instructions=false` unless an explicit agent/model profile needs repeated reading. Both modes require refresh for a new independent task, explicit context switch, changed rules or lost full text; true additionally rereads on every invocation. Repeated invocation does not start a new task or cycle. Required sources are checked afresh on every receipt in both modes.
