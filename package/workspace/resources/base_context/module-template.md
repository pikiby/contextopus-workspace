# Module design template

Use `module-guide` and the runnable `examples/modules/catalog-demo` package as authoring references. The deliverable is one folder with `module.json`, handler files and any declared migrations under `package/`. Installation is one `core.module_install(module=<id>)` action through `$m`, MCP, CLI or TUI.

- Purpose and user-visible behavior:
- Module ID, package version and supported Core version range:
- Operations, query/execute ownership, argument schemas and handler protocol:
- Structured tables, keys, relations and `{{module}}::` object names:
- Storage target version, inclusive compatibility range and numbered SQL migrations:
- Searchable source documents, stable keys, revision and deletion rules:
- Shared Core RAG adapter and object types, where search is needed:
- Configuration, defaults, validation and user files in `data/` and `config/`:
- Declared Python dependencies, external tools and bounded/background work:
- Passive discovery, one-action installation, update and disable behavior:
- Write authorization, failed migrations, compatible recovery and retry:
- Actual Application validation: install, query/write, restart, update and preserved data:

Core owns dependencies, migration application and activation. Handlers use `core.storage.query`/`execute` through `contextopus-core-v1`; they do not open the shared catalog or install their own schema. Domain tables are separate from the common RAG index. Preserve existing user files and applied migration contents.

Use the manifest and registry contracts as the executable specification. This template is a planning aid and does not authorize implementation, installation or publication.
