"""Bounded, lossless conversion of legacy snapshot bodies to exact Git references."""
import hashlib
import json

from .runtime import PCWError
from .history_queries import page
from .operation_types import PageLimit


class AnalyticsMigrationMixin:
    def analytics_migrate(self, limit: PageLimit = 50, cursor: str | None = None,
                          apply: bool = False, git_limit: PageLimit = 50):
        self._require_format()
        page(0, limit)
        page(0, git_limit)
        if type(apply) is not bool:
            raise PCWError('apply must be boolean')
        from .analytics import EventStore
        with self.control.guard(), EventStore(self.engine.work_root).connect() as db:
            workspace = self.engine.workspace
            scope = str(self.engine.root.resolve())
            token = self._migration_cursor(db, cursor, workspace, scope)
            rows = db.execute('''
                SELECT 'versions' AS kind,v.rowid AS row_key,v.* FROM versions v
                JOIN events e ON e.id=v.event_id WHERE e.workspace=? AND v.rowid<=?
                AND (?='' OR (?='versions' AND v.rowid>?))
                ORDER BY v.rowid LIMIT ?
            ''', (workspace, token['versions_max'], token['kind'], token['kind'], token['last'], limit + 1)).fetchall()
            selected = [dict(row) for row in rows]
            if len(selected) <= limit:
                observed = db.execute('''SELECT 'observed' AS kind,rowid AS row_key,* FROM observed
                    WHERE workspace=? AND rowid<=? AND (?!='observed' OR (?='observed' AND rowid>?))
                    ORDER BY rowid LIMIT ?''', (workspace, token['observed_max'], token['kind'], token['kind'], token['last'], limit + 1 - len(selected))).fetchall()
                selected.extend(dict(row) for row in observed)
            # Explicit table order makes each frozen high-water range resumable.
            selected.sort(key=lambda row: (0 if row['kind'] == 'versions' else 1, row['row_key']))
            more = len(selected) > limit
            selected = selected[:limit]
            head, history_status = self._history_head('HEAD')
            budget = [git_limit]
            cache = {}
            proposals, preserved, candidates = [], 0, set()
            for row in selected:
                sides = ('before', 'after') if row['kind'] == 'versions' else ('observed',)
                for side in sides:
                    blob_field = side + '_blob' if side != 'observed' else 'blob'
                    hash_field = side + '_hash' if side != 'observed' else 'hash'
                    blob = row[blob_field]
                    if not blob:
                        continue
                    retained = db.execute('SELECT text FROM blobs WHERE id=?', (blob,)).fetchone()
                    key = (row['path'], row[hash_field], retained[0] if retained else None)
                    if key not in cache:
                        cache[key] = self._migration_match(head, row['path'], row[hash_field], retained, budget)
                    reference = cache[key]
                    if reference is None:
                        preserved += 1
                        continue
                    reference = dict(reference, workspace=workspace, projection='file', role='legacy_' + side, relation=row.get('relation'))
                    proposals.append(dict(kind=row['kind'], row_key=row['row_key'], side=side, reference=reference))
                    if apply:
                        db.execute('INSERT OR REPLACE INTO legacy_snapshot_references VALUES (?,?,?,?,?)',
                                   (scope, row['kind'], row['row_key'], side, json.dumps(reference)))
                        if row['kind'] == 'versions':
                            ordinal = db.execute('SELECT coalesce(max(ordinal),-1)+1 FROM event_references WHERE event_id=?', (row['event_id'],)).fetchone()[0]
                            db.execute('INSERT INTO event_references VALUES (?,?,?)', (row['event_id'], ordinal, json.dumps(reference)))
                        table = row['kind']
                        db.execute(f'UPDATE {table} SET {blob_field}=NULL WHERE rowid=?', (row['row_key'],))
                        candidates.add(blob)
            deleted = 0
            if apply:
                for blob in candidates:
                    # Only bodies verified in this transaction are GC candidates.
                    referenced = db.execute('''SELECT
                        EXISTS(SELECT 1 FROM events WHERE params=? OR result=? OR error=?) OR
                        EXISTS(SELECT 1 FROM versions WHERE before_blob=? OR after_blob=?) OR
                        EXISTS(SELECT 1 FROM observed WHERE blob=?)''', (blob,) * 6).fetchone()[0]
                    if not referenced:
                        deleted += db.execute('DELETE FROM blobs WHERE id=?', (blob,)).rowcount
            next_cursor = None
            if more and selected:
                last = selected[-1]
                token.update(kind=last['kind'], last=last['row_key'])
                next_cursor = json.dumps(token)
            return dict(status='applied' if apply else 'dry_run', context=scope, workspace=workspace,
                        examined_rows=len(selected), conversions=proposals, preserved_bodies=preserved,
                        deleted_blobs=deleted, next_cursor=next_cursor,
                        coverage=dict(history_status=history_status, head=head, git_candidates_examined=git_limit-budget[0],
                                      git_budget_exhausted=budget[0] == 0,
                                      historical_search_incomplete=preserved > 0 and head is not None,
                                      snapshots_only=True, event_params_result_error='preserved; not migrated',
                                      unmatched='preserved; bounded search does not prove absence'))

    def _migration_cursor(self, db, cursor, workspace, scope):
        if cursor is None:
            return dict(context=scope, workspace=workspace, kind='', last=0,
                        versions_max=db.execute('SELECT coalesce(max(v.rowid),0) FROM versions v JOIN events e ON e.id=v.event_id WHERE e.workspace=?', (workspace,)).fetchone()[0],
                        observed_max=db.execute('SELECT coalesce(max(rowid),0) FROM observed WHERE workspace=?', (workspace,)).fetchone()[0])
        try:
            token = json.loads(cursor)
            if (set(token) != {'context', 'workspace', 'kind', 'last', 'versions_max', 'observed_max'}
                    or token['context'] != scope or token['workspace'] != workspace
                    or token['kind'] not in ('versions', 'observed')
                    or any(type(token[k]) is not int or token[k] < 0 for k in ('last', 'versions_max', 'observed_max'))):
                raise ValueError()
            return token
        except (ValueError, TypeError):
            raise PCWError('Invalid analytics migration cursor') from None

    def _migration_match(self, head, path, digest, retained, budget):
        from .analytics import redact
        if not head or not retained:
            return None
        try:
            self._history_paths([path])
            value = json.loads(retained[0])
        except (PCWError, ValueError, TypeError):
            return None
        def match(commit):
            result = self._history_git('show', commit + ':' + path, check=False)
            if result.returncode or hashlib.sha256(result.stdout.encode('utf-8')).hexdigest() != digest:
                return None
            if redact(result.stdout)[0] != value:
                return None
            blob = self._history_blob(commit, path)
            if blob:
                return dict(status='versioned', context=self._history_context, path=path,
                            content_sha256=digest, commit=commit, blob=blob)
            return None
        current = match(head)
        if current or not budget[0]:
            return current
        # One extra entry accounts for HEAD when it changed this exact path.
        commits = self._history_git('log', f'--max-count={budget[0] + 1}', '--format=%H', head, '--', path).stdout.splitlines()
        for commit in dict.fromkeys(commits):
            if commit == head:
                continue
            if budget[0] == 0:
                break
            budget[0] -= 1
            reference = match(commit)
            if reference:
                return reference
        return None
