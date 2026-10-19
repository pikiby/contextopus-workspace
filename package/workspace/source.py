"""Workspace translates authoritative cards into Core documents."""

from contextopus.core.rag import DocumentKey, IndexDocument


class WorkspaceDocuments:
    def __init__(self, engine):
        self.engine = engine

    def document(self, material):
        owner = getattr(self.engine, "owners", {}).get(
            material.id, self.engine.root.name
        )
        return IndexDocument(
            DocumentKey("workspace", owner, material.id),
            material.revision,
            material.content,
            material.path,
            dict(material.metadata),
        )

    def documents(self):
        return [self.document(m) for m in self.engine.materials.values()]

    def signature(self):
        return self.engine._content_signature

    def validate(self, signature):
        self.engine._scan()
        if self.engine._content_signature != signature:
            self.engine._reconcile()
            raise ValueError(
                "Knowledge changed during index rebuild; switch was not published"
            )


class WorkspaceEligibility:
    """Validate derived Workspace rows without starting a work cycle or repair."""

    def __init__(self, layout, provider_factory=None):
        from .layout import WorkspaceLayout
        self.layout = WorkspaceLayout(layout.root)
        self.provider_factory = provider_factory
        self.last_status = {"state": "not_checked"}

    def eligible_documents(self, rows):
        from .shared import SharedRuntime
        from .runtime import PCWError
        from .workspaces import FORMAT_VERSION
        import json

        names = sorted(
            {
                row.get("collection") or row.get("workspace")
                for row in rows
                if row.get("collection") or row.get("workspace")
            }
        )
        selected = None
        for name in names:
            try:
                marker = (
                    self.layout.control(name)
                    if not self.layout.legacy
                    else self.layout.root / ".pcw/.control" / name
                )
                if (
                    json.loads((marker / "format.json").read_text()).get(
                        "format_version"
                    )
                    in range(2, FORMAT_VERSION + 1)
                ):
                    selected = name
                    break
            except (OSError, ValueError):
                continue
        if selected is None:
            self.last_status = {"state": "source_unavailable"}
            return {}
        engine = SharedRuntime(
            self.layout.root,
            selected,
            initialize=False,
            allow_create=False,
            **(
                {"provider_factory": self.provider_factory}
                if self.provider_factory
                else {}
            ),
        )
        try:
            with engine.read_guard():
                source = WorkspaceDocuments(engine)
                indexed = engine._indexed_ids()
                result = {
                    source.document(engine.catalog[ident]).key: source.document(
                        engine.catalog[ident]
                    )
                    for ident in indexed
                    if not engine._material_invalidations(engine.catalog[ident])
                }
                self.last_status = {"state": "checked", "eligible": len(result)}
                return result
        except (PCWError, OSError, ValueError) as exc:
            self.last_status = {
                "state": "source_unavailable",
                "error": type(exc).__name__,
            }
            return {}
        finally:
            engine.close()
