-- Workspace owns event/history schema. Imported historical events/evaluations
-- gain optional columns through the module-owned version-1 adaptation hook.
CREATE TABLE IF NOT EXISTS blobs (id TEXT PRIMARY KEY, text TEXT NOT NULL, redactions INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, workspace TEXT NOT NULL, tool TEXT NOT NULL, cycle_id TEXT, task_id TEXT,
 mode TEXT, started_at TEXT NOT NULL, finished_at TEXT, duration_ms REAL,
 state TEXT NOT NULL, params TEXT, result TEXT, error TEXT, result_bytes INTEGER,
 source TEXT NOT NULL DEFAULT 'mcp', source_event_id TEXT, import_id TEXT,
 instructions_revision TEXT, instructions_ref TEXT, runtime_version TEXT,
 procedures_revision TEXT, operation_id TEXT, journal_format INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS events_scope ON events(workspace, cycle_id, task_id, started_at);
CREATE TABLE IF NOT EXISTS versions (
 event_id TEXT, path TEXT, relation TEXT NOT NULL, before_hash TEXT, after_hash TEXT,
 before_blob TEXT, after_blob TEXT, PRIMARY KEY(event_id, path, relation));
CREATE TABLE IF NOT EXISTS observed (workspace TEXT, path TEXT, hash TEXT, blob TEXT,
 PRIMARY KEY(workspace, path));
CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY, adapter TEXT NOT NULL,
 source_path TEXT, session_id TEXT, imported_at TEXT, records INTEGER);
CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, created_at TEXT,
 criterion TEXT, assessment TEXT, evidence TEXT, scope TEXT, instruction_basis TEXT);
CREATE TABLE IF NOT EXISTS event_references (
 event_id TEXT, ordinal INTEGER, reference TEXT NOT NULL, PRIMARY KEY(event_id, ordinal));
CREATE TABLE IF NOT EXISTS history_commits (
 context TEXT, oid TEXT, metadata TEXT, PRIMARY KEY(context, oid));
CREATE TABLE IF NOT EXISTS history_versions (
 context TEXT, oid TEXT, material TEXT, path TEXT, blob TEXT, PRIMARY KEY(context, oid, material, path));
CREATE TABLE IF NOT EXISTS history_edges (
 context TEXT, oid TEXT, group_id INTEGER, kind TEXT,
 source_id TEXT, source_commit TEXT, target_id TEXT, target_commit TEXT,
 PRIMARY KEY(context, oid, group_id, source_id, target_id));
CREATE INDEX IF NOT EXISTS history_edges_source ON history_edges(context,source_id,source_commit);
CREATE INDEX IF NOT EXISTS history_edges_target ON history_edges(context,target_id,target_commit);
CREATE INDEX IF NOT EXISTS history_versions_material ON history_versions(context,material,oid);
CREATE TABLE IF NOT EXISTS legacy_snapshot_references (
 context TEXT, kind TEXT, row_key INTEGER, side TEXT, reference TEXT,
 PRIMARY KEY(context,kind,row_key,side));
