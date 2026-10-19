"""Rebuildable, context-qualified Git metadata and declared version lineage."""
from contextlib import contextmanager
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import re

from .runtime import PCWError, _id
from .history_queries import PERMANENT_PATHS, page


class HistoryCatalogMixin:
    @property
    def _history_context(self):
        return str(self.engine.root.resolve())

    @contextmanager
    def _history_catalog(self):
        from .analytics import EventStore
        with EventStore(self.engine.work_root).connect() as db:
            yield db

    def _history_blob(self, commit, path):
        result = self._history_git('rev-parse', '--verify', '--end-of-options', commit + ':' + path, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def _history_content_reference(self, path, content_sha256, ref='HEAD'):
        """Resolve exact content at one Git tree path, never dirty workspace text."""
        self._history_paths([path])
        if not isinstance(content_sha256, str) or not re.fullmatch('[0-9a-f]{64}', content_sha256):
            raise PCWError('content_sha256 must be a SHA256 digest')
        head, status = self._history_head(ref)
        result = dict(status='unversioned', context=self._history_context, path=path,
                      content_sha256=content_sha256, commit=None, blob=None)
        if head is None:
            result['status'] = status
            return result
        blob = self._history_blob(head, path)
        if blob is not None:
            content = self._history_git('show', head + ':' + path).stdout
            if hashlib.sha256(content.encode('utf-8')).hexdigest() == content_sha256:
                result.update(status='versioned', commit=head, blob=blob)
        return result

    def _history_source_version(self, parent, ident, path):
        changed = self._history_git('log', '-1', '--format=%H', parent, '--', path).stdout.strip() or parent
        declared = self._history_git('log', '-1', '--format=%H',
            '--grep=^Workspace-Record: ' + ident.replace('.', r'\.') + '$', parent).stdout.strip()
        if declared and self._history_git('merge-base', '--is-ancestor', changed, declared, check=False).returncode == 0:
            event = self._history_commit(declared)['event']
            if event and (any(c['id'] == ident and c.get('after') for c in event['changes'])
                          or any(ident in t['targets'] for t in event['transformations'])):
                if self._history_blob(declared, path) == self._history_blob(parent, path):
                    return declared
        return changed

    def _history_index_commit(self, commit):
        self.engine._own_git()
        info = self._history_commit(commit)
        event = info.pop('event')
        info['provenance'] = 'declared' if event else 'unknown'
        parent = info['parents'][0] if info['parents'] else None
        args = [parent, commit] if parent else ['--root', commit]
        paths = self._history_git('diff-tree', '--no-commit-id', '--no-ext-diff',
            '--no-textconv', '--no-renames', '--name-only', '-r', '-z', *args,
            '--', *PERMANENT_PATHS).stdout.split('\0')
        info['files'] = [{'path': p, 'before_blob': self._history_blob(parent, p) if parent else None,
                          'after_blob': self._history_blob(commit, p)} for p in paths if p]
        info['event'] = event
        versions, edges = [], []
        source_versions = {}
        mismatches = []
        target_mismatches = []
        if event and parent:
            for change in event['changes']:
                if change.get('before'):
                    path = change['before']['path']
                    self._history_paths([path])
                    content = self._history_git('show', parent + ':' + path, check=False)
                    if content.returncode or hashlib.sha256(content.stdout.encode('utf-8')).hexdigest() != change['before'].get('revision'):
                        mismatches.append(change['id'])
                        continue
                    source_versions[change['id']] = self._history_source_version(parent, change['id'], path)
        info['source_mismatches'] = mismatches
        if event:
            for change in event['changes']:
                for side, oid in (('before', source_versions.get(change['id'], parent)), ('after', commit)):
                    entry = change.get(side)
                    if entry and oid and not (side == 'before' and change['id'] in mismatches):
                        path = entry['path']
                        self._history_paths([path])
                        if side == 'after':
                            content = self._history_git('show', oid + ':' + path, check=False)
                            if content.returncode or hashlib.sha256(content.stdout.encode('utf-8')).hexdigest() != entry.get('revision'):
                                target_mismatches.append(change['id'])
                                continue
                        versions.append((self._history_context, oid, change['id'], path, self._history_blob(oid, path)))
                if parent and change['id'] not in mismatches + target_mismatches and change.get('before') and change.get('after'):
                    edges.append((self._history_context, commit, -1, 'revision', change['id'], source_versions.get(change['id'], parent), change['id'], commit))
            for group, transform in enumerate(event['transformations']):
                # Retained endpoints can be unchanged and absent from changes.
                for source in transform['sources']:
                    if parent and source not in source_versions and source not in mismatches:
                        try:
                            path = self._history_record_paths(parent, source)[0]
                            oid = self._history_source_version(parent, source, path)
                            source_versions[source] = oid
                            versions.append((self._history_context, oid, source, path, self._history_blob(oid, path)))
                        except PCWError:
                            mismatches.append(source)
                for target in transform['targets']:
                    if target not in target_mismatches and not any(v[1] == commit and v[2] == target for v in versions):
                        try:
                            path = self._history_record_paths(commit, target)[0]
                            versions.append((self._history_context, commit, target, path, self._history_blob(commit, path)))
                        except PCWError:
                            mismatches.extend(transform['sources'])
                if parent and not any(source in mismatches for source in transform['sources']) and not any(target in target_mismatches for target in transform['targets']):
                    for source in transform['sources']:
                        for target in transform['targets']:
                            edges.append((self._history_context, commit, group, transform['kind'], source, source_versions.get(source, parent), target, commit))
        info['target_mismatches'] = target_mismatches
        with self._history_catalog() as db:
            db.execute('INSERT OR REPLACE INTO history_commits VALUES (?, ?, ?)',
                       (self._history_context, commit, json.dumps(info)))
            db.executemany('INSERT OR REPLACE INTO history_versions VALUES (?, ?, ?, ?, ?)', versions)
            db.executemany('INSERT OR REPLACE INTO history_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)', edges)
        return commit

    def history_index(self, ref: str = 'HEAD', cursor: str | None = None, limit: int = 100):
        self._require_format()
        page(0, limit)
        with self.control.guard():
            head, status = self._history_head(ref)
            offset = 0
            if cursor is not None:
                try:
                    token = json.loads(cursor)
                    if set(token) != {'context', 'head', 'offset'} or token['context'] != self._history_context or not re.fullmatch('[0-9a-f]{40,64}', token['head']):
                        raise ValueError()
                    offset = token['offset']
                    if type(offset) is not int or offset < 0:
                        raise ValueError()
                except (TypeError, ValueError):
                    raise PCWError('Invalid history index cursor') from None
                if token['head'] != head:
                    reachable = head and self._history_git('merge-base', '--is-ancestor', token['head'], head, check=False).returncode == 0
                    raise PCWError('History head advanced; restart indexing' if reachable else 'History rewritten or cursor head unreachable; restart indexing')
            if head is None:
                return dict(status=status, head=None, scope={'context': self._history_context, 'ref': ref}, next_cursor=None, indexed=[])
            commits = self._history_git('rev-list', f'--max-count={limit + 1}', f'--skip={offset}', head).stdout.splitlines()
            indexed = [self._history_index_commit(c) for c in commits[:limit]]
            next_cursor = json.dumps(dict(context=self._history_context, head=head, offset=offset + limit)) if len(commits) > limit else None
            return dict(status='indexed', head=head, scope={'context': self._history_context, 'ref': ref}, indexed=indexed,
                        next_cursor=next_cursor, coverage={'offset': offset, 'count': len(indexed), 'reached_root': next_cursor is None,
                                                         'complete': offset == 0 and next_cursor is None})

    def history_lineage(self, material_id: str, direction: str = 'ancestors', ref: str = 'HEAD', since: str | None = None, until: str | None = None, max_depth: int = 5, limit: int = 50, start_commit: str | None = None):
        self._require_format()
        _id(material_id)
        page(0, limit)
        if direction not in {'ancestors', 'descendants'} or type(max_depth) is not int or not 0 <= max_depth <= 20:
            raise PCWError('Use ancestors/descendants and max_depth between 0 and 20')
        dates = []
        for date in (since, until):
            try:
                parsed = datetime.fromisoformat(date) if date is not None else None
                dates.append((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat() if parsed else None)
            except (ValueError, TypeError):
                raise PCWError('History dates must use ISO format') from None
        since, until = dates
        if since is not None and until is not None and since >= until:
            raise PCWError('since must precede until')
        with self.control.guard():
            head, status = self._history_head(ref)
            if head is None:
                return dict(status=status, head=None, nodes=[], edges=[])
            with self._history_catalog() as db:
                if start_commit is not None:
                    start = self._history_revision(start_commit)
                    if self._history_git('merge-base', '--is-ancestor', start, head, check=False).returncode:
                        raise PCWError('Start version is unreachable from ref')
                else:
                    escaped = material_id.replace('.', r'\.')
                    start = self._history_git('log', '-1', '--format=%H', '--grep=^Workspace-Record: ' + escaped + '$', head).stdout.strip() or head
                    # Deletion/transformation commits may only declare a source version.
                    row = db.execute('SELECT source_commit FROM history_edges WHERE context=? AND oid=? AND source_id=? LIMIT 1',
                                     (self._history_context, start, material_id)).fetchone()
                    target = db.execute('SELECT 1 FROM history_versions WHERE context=? AND oid=? AND material=? LIMIT 1',
                                        (self._history_context, start, material_id)).fetchone()
                    if row and not target:
                        start = row[0]
                return self._history_lineage_graph(db, material_id, direction, head, since, until, max_depth, limit, start)

    def _history_lineage_graph(self, db, material, direction, head, since, until, depth, limit, start):
        # Date predicates precede LIMIT; stale branches are checked within a bounded candidate window.
        column = 'target' if direction == 'ancestors' else 'source'
        other = 'source' if direction == 'ancestors' else 'target'
        def candidates(ident, version, count):
            sql = f"""SELECT e.* FROM history_edges e JOIN history_commits c
                ON c.context=e.context AND c.oid=e.oid
                WHERE e.context=? AND e.{column}_id=? AND e.{column}_commit=?"""
            params = [self._history_context, ident, version]
            for operator, value in (('>=', since), ('<', until)):
                if value is not None:
                    sql += f" AND julianday(json_extract(c.metadata,'$.time')) {operator} julianday(?)"
                    params.append(value)
            return db.execute(sql + ' ORDER BY e.oid,e.group_id LIMIT ?', [*params, count]).fetchall()

        def node(ident, version):
            indexed = db.execute('SELECT 1 FROM history_commits WHERE context=? AND oid=?',
                                 (self._history_context, version)).fetchone() is not None
            return dict(material_id=ident, commit=version, context=self._history_context, indexed=indexed)

        queue = deque([(material, start, 0)])
        nodes = {(material, start): node(material, start)}
        edges, seen_edges, seen_nodes = [], set(), set()
        truncated, candidates_incomplete, filtered_unreachable = False, False, 0
        while queue:
            ident, version, level = queue.popleft()
            if (ident, version) in seen_nodes:
                continue
            seen_nodes.add((ident, version))
            probing = level >= depth or len(edges) >= limit
            remaining = limit - len(edges)
            bound = 1 if probing else remaining + 1
            rows = candidates(ident, version, bound)
            # A full candidate window may hide reachable edges behind stale indexed branches.
            if len(rows) == bound:
                candidates_incomplete = True
            for row in rows:
                key = tuple(row)
                if key in seen_edges:
                    continue
                edge = dict(row)
                if self._history_git('merge-base', '--is-ancestor', edge['oid'], head, check=False).returncode:
                    filtered_unreachable += 1
                    continue
                if probing or len(edges) >= limit:
                    truncated = True
                    break
                seen_edges.add(key)
                edges.append(edge)
                for side in ('source', 'target'):
                    endpoint = (edge[side + '_id'], edge[side + '_commit'])
                    nodes[endpoint] = node(*endpoint)
                queue.append((edge[other + '_id'], edge[other + '_commit'], level + 1))
            if truncated:
                break
        return dict(status='ok', head=head, context=self._history_context, nodes=list(nodes.values()), edges=edges,
                    start_commit=start, coverage='indexed declared provenance only; missing ancestor metadata may require history_index',
                    candidate_coverage_incomplete=candidates_incomplete and filtered_unreachable > 0,
                    filtered_unreachable=filtered_unreachable, truncated=truncated, max_depth=depth)
