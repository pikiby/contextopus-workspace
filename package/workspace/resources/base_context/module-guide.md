# Create and install a Contextopus module

A module contributes a domain adapter and registered operations to Core. The author supplies a folder containing `module.json` and `package/`, places it in `<root>/modules/<id>`, and asks `$m` to install it. The skill obtains `guide(procedure="modules")` and calls `core.module_install(module=<id>)`. Core prepares dependencies, applies declared migrations and enables the operations. CLI `modules install ID`, TUI Install and MCP use this same operation. Inspection alone does not authorize installation; an installation request already authorizes its declared sequence.

Discovery reads the manifest without importing handlers, preparing dependencies, applying migrations, loading models or starting jobs. Disabled modules remain passive. `core.modules` reports compatibility, lifecycle and storage errors. A failed or incompatible module must not be treated as enabled. Retry installation through Core after resolving its cause. Low-level prepare/enable operations remain available for diagnosis; enable does not apply migrations.

Example: first inspect `core.modules` for a supplied catalog module. Once installation is authorized, install it through `core.module_install`, inspect its declared procedure/signature, then verify its own status and a bounded query through the shared registry. A visible folder or successful dependency preparation alone does not establish operational readiness.

## Package contract

A minimal dependency-free release is:

```text
sample/
  module.json
  package/
    adapter.py
```

```json
{"id":"sample","version":"1.0.0","core_requires":">=1.3,<2","operations":[{"name":"sample.status","kind":"query","handler":"adapter.py:status","arguments_schema":{"type":"object","additionalProperties":false}}]}
```

```python
# package/adapter.py

def status(arguments):
    return {"available": True}
```

Handlers return JSON-compatible values from a child interpreter. Operation names begin with the module ID and a dot; each declares `query` or `execute` and an argument schema. `dependencies.python` lists Python requirements prepared by Core in the release's own environment. Optional `procedures`, `skills` and `filter_algorithms` name directories relative to `package/`; `tui` names a contributed operation that returns terminal `argv`. Native skills are published to a client only through its separately requested integration workflow.

Packages execute trusted code. Child processes and SQL access checks are not an operating-system sandbox. Author handlers for bounded operations and document background-job lifecycle, external tools and write ownership when needed. Keep stdout for the operation's JSON protocol and use stderr for logs.

## Ordinary tables through Core

Declare structured storage in `module.json`:

```json
{"storage":{"engine":"sqlite","schema_version":1,"migrations":"migrations","compatible_versions":{"min":1,"max":1}}}
```

Put ordinary SQL in `package/migrations/001-schema.sql`, then `002-change.sql`, with one continuously numbered file per version through `schema_version`. Underscores after the number are also supported. Do not alter an applied file. Quote module-owned table, index and other object names with the `{{module}}::` prefix:

```sql
CREATE TABLE "{{module}}::sessions" (id TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE "{{module}}::messages" (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES "{{module}}::sessions" (id),
    body TEXT NOT NULL
);
```

Core creates and owns `<root>/core/storage/modules/<module-id>.sqlite3`, records applied versions in that module file and applies pending migrations transactionally. Each module has a separate SQLite writer lock; a write in one file does not hold another module's writer lock. SQLite allows one writer at a time within a file. The inclusive compatibility range must describe schema versions the package can actually use. SQL cannot access another module's tables, attach databases, set pragmas, load extensions or manage transactions. Schema changes belong in migrations; runtime execute operations use DML. Do not open the database directly or make a separate installer.

Only explicit Core storage apply or module installation migrates an older shared `core/storage/modules.sqlite3` catalog into module files. Core preserves its source and a recovery backup; discovery, preparation and queries remain read-only. Models, embeddings and RAG remain shared Core services. Transactions cover one module's SQL file; they do not span files or RAG. Relocation drains active module writers under the root guard before freezing and copying their files.

Storage-aware operations declare `"protocol":"contextopus-core-v1"` and use a two-argument handler:

```python
def messages(arguments, core):
    return core.storage.query(
        'SELECT id, body FROM "{{module}}::messages" WHERE session_id = ?',
        [arguments["session_id"]],
    )

def save(arguments, core):
    return core.storage.execute(
        'INSERT INTO "{{module}}::sessions" (id, title) VALUES (?, ?)',
        [arguments["id"], arguments["title"]],
    )
```

`query(sql, parameters=())` returns `columns` and a `rows` list of dictionaries. `execute(sql, parameters=())` returns `rowcount` and `lastrowid`. Core owns database connections; query operations cannot write. The complete runnable `examples/modules/catalog-demo` example uses this bridge for sessions and linked messages, including joins and aggregation. Legacy one-argument handlers continue to work without storage access.

Domain tables and RAG are separate services. Use the common Core index for searchable material; do not create a module-specific vector database. An operation manifest alone does not register an index source.

## Trusted in-process contributions

A module that needs native Core source adapters can declare
`"contribution": {"factory": "workspace/__init__.py:register", "requires": ["contextopus-core>=1.5,<2"]}`
instead of child operations or a service. Workspace uses this mode. The factory
receives an explicit host with shared resources, module storage and staged
operation, source, procedure and settings registrations. Native requirements are
checked in the Core interpreter; a child virtual environment does not supply them.

The same `core.module_install/enable/disable/update` operations manage both modes.
Only explicit installation or update runs paired `install_prepare` and
`install_apply` hooks; `ready` validates existing storage without creating tables.
Native modules use `host.module_storage.for_module(id).native()` for Core-managed
SQL connections, schema migrations and explicit legacy imports. Native modules
are trusted Python code, not an isolated child SQL sandbox. Declare migration
files in the module package and retain immutable migration checksums.

Core waits for admitted native calls before disable, update or relocation, then
removes all owned operations, aliases, procedures, settings and RAG adapters.
Factories cannot override another module's operation names. Failed installation
must not publish the new contribution. Module-specific callbacks must close
background resources; storage/schema failure and recovery require acceptance tests.

## Update and verify

The locally developed persistent-service extension declares `service: {"factory":"worker.py:create_service","ready_timeout":10,"stop_timeout":5}` and routes operations with `service: true` instead of `handler`. A service command instead declares `protocol: "contextopus-service-v1"`. The factory receives the child Core SDK and returns an object with optional `start()`, required `dispatch(operation, arguments, context)` and optional `close()`. The root owner starts enabled services, keeps one runtime per root/module and stops it on disable, update and shutdown. Listing is passive. Inspect `core.modules` service state/PID/error and the module's background-job status query. Verify source and installed capabilities separately; this is not a release claim.

Use `with core.storage.transaction(timeout_seconds=5) as tx:` for atomic `tx.query`/`tx.execute` batches. Commit follows success; errors, expiry and disconnect roll back. Maximum duration is five seconds. Multiple SQLite connections share one writer within the same module file; other module files have independent writer locks. Do file reads, parsing, waiting, tokenization, embedding and RAG work outside transactions. No WAL mode is implied.

Optional `rag: true` grants shared `core.embeddings.profile()`, `embed_documents(texts=..., profile=...)`, `embed_query(text=..., profile=...)` and `count_tokens(text=..., profile=...)`. Module-scoped `core.rag` supports `put_vector`, `vector_revisions`, `vector_revisions_page`, `search_vector` and `delete_document`. Use the exact Core profile; a profile change requires restart. SQL and RAG have separate commits: retain durable jobs and deletion records for replay and revision reconciliation. The runnable `examples/modules/service-demo` and repository `docs/implementation/module-services-local.md` provide exact SDK shapes and isolated source checks.

Revision lookup requires `collection` and `object_ids` (1–1,000 distinct IDs). It returns `revisions`, `missing`, `conflicts` and `snapshot`; disagreeing fragment revisions are not silently selected. Full inventory is a separate `vector_revisions_page(collection=..., cursor=None, limit=100, max_bytes=262144)` operation. Follow `next_cursor` until null; pages keep one native table snapshot and object-ID order. Both APIs default to 256 KiB results, with a 768 KiB maximum; pages allow at most 1,000 distinct objects. Oversized point lookups fail; pages return a bounded prefix and continuation. Invalid/expired cursors require restarting the inventory. Resume durable work at startup; a full inventory is an explicit audit, not a startup requirement.

RPC input/output frames are limited to 1 MiB each, including compact UTF-8 JSON, envelope and newline. An oversized Core result produces a bounded error; the child can catch it and continue calling Core. The embedding API accepts 1–128 documents, but large vector output may need smaller batches; reduce the batch after an explicit oversized-result error. In-flight parent model/native RAG calls cannot be forcibly interrupted. Core stops/disconnects the child, while asynchronous parent cleanup waits for the call to complete; the child stop timeout does not bound that call.

Mutable user files belong in `data/` and `config/`. An authorized `core.module_update(module=<id>, source=<unpacked release>)` stages the new package and prepares its dependencies and schema before activation. Previously disabled modules stay disabled. Preserve user files and retained releases; incompatible code must not resume against a changed schema. Disabling removes operations and keeps data.

Test installation through an actual Application in an isolated root, exercise relevant query/write operations, then rebuild the Application to verify persisted state and existing rows. Test schema failure and retry, incompatible versions, and passive discovery. Fetch unknown signatures through `guide(operation=...)` rather than inventing API calls. This guide and its template describe module authoring; they do not authorize unrelated installation or publication.

Implementation references: `src/contextopus/core/module_management/`, `src/contextopus/core/module_storage/`, `src/contextopus/core/module_management/child.py`. Detailed procedure: `modules`; runnable repository example: `examples/modules/catalog-demo`.
