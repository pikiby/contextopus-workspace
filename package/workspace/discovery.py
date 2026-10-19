"""Read-only discovery before choosing an active Workspace."""

from pathlib import Path
from .layout import WorkspaceLayout


def contexts(layout, name=None, offset=0, limit=50):
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Use offset >= 0 and limit 1..100")
    layout = WorkspaceLayout(layout.root)
    base = layout.root / ".pcw" if layout.legacy else layout.workspace_base
    entries = []
    if base.is_dir():
        for path in sorted(base.iterdir()):
            if path.name.startswith(".") or path.is_symlink() or not path.is_dir():
                continue
            if name is not None and name not in (path.name, str(path)):
                continue
            entries.append({"name": path.name, "path": str(path)})
    return {"contexts": entries[offset:offset + limit], "total": len(entries),
            "next_offset": offset + limit if offset + limit < len(entries) else None}


def projects_search(layout, index, query, limit=10, distance=None):
    layout = WorkspaceLayout(layout.root)
    found = index.search(query, module="workspace", object_type="project", limit=limit, distance=distance)
    return {"results": [
        {"workspace": row["key"][1], "path": str(layout.workspace(row["key"][1])),
         "material_id": row["key"][2], "record_path": row["location"],
         "excerpt": row["excerpt"], "distance": row["distance"]}
        for row in found],
        "source_status": index.source_adapters["workspace"].last_status,
        "selection_changed": False}


def register(app):
    app._operation("workspace.contexts", "query", lambda **kw: contexts(app.layout, **kw), {
        "name": (str | None, None), "offset": (int, 0), "limit": (int, 50)})
    app._operation("workspace.projects_search", "query", lambda **kw: projects_search(app.layout, app.index, **kw), {
        "query": (str, ...), "limit": (int, 10), "distance": (float | None, None)})
