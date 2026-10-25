"""Format 2: separate source folders, one category-filtered index per memory root."""

from .text_io import read_utf8

import copy
import dataclasses
import json
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone

import pyarrow as pa

from .runtime import (
    PCWRuntime,
    PCWError,
    Material,
    SearchConfig,
    _id,
    _parse,
    _unique_links,
    select_candidates,
)
from .control import RecoveryRequired, control_store
from .sources import (
    declarations,
    needs_source_upgrade,
    migrate_sources,
)
from .links import LinkCodec


def topic_name(content, ident):
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.startswith(("```", "---"))
    ]
    title = next(
        (line[2:].strip() for line in lines if line.startswith("# ")),
        lines[0] if lines else "",
    )
    name = re.sub(r"[^\w -]", "", title, flags=re.UNICODE).strip().replace("_", "-")
    name = (
        re.sub(r"[ -]+", "-", name)
        .strip("-.")[:100]
        .encode("utf-8")[:160]
        .decode("utf-8", errors="ignore")
        .rstrip("-.")
    )
    return (name or "Material-" + ident.removesuffix(".md")) + ".md"


class WorkspaceSourceAdapter(PCWRuntime):
    def __init__(self, work_root, workspace, **kwargs):
        initialize = kwargs.pop("initialize", True)
        super().__init__(work_root, workspace, initialize=False, **kwargs)
        self.workspace = workspace
        self.legacy_state = self.state
        self.state = self.layout.workspace_rag
        self.catalog = {}
        self.owners = {}
        self.incompatible = []
        if initialize:
            with self._guard():
                pass

    def _assert_safe(self, path):
        # Broader read namespace; mutation entrypoints still resolve only local IDs.
        base = self.work_root / ".pcw" if self.layout.legacy else self.work_root
        try:
            path.relative_to(base)
        except ValueError as exc:
            if not path.is_relative_to(self.layout.reservations.parent):
                raise PCWError("Path is outside selected .pcw storage") from exc
        for candidate in (base, *path.parents, path):
            if candidate == self.work_root or self.work_root not in candidate.parents:
                continue
            if candidate.is_symlink():
                raise PCWError(f"Symlinks are not allowed in memory paths: {candidate}")
        if not path.resolve(strict=False).is_relative_to(base) and not path.resolve(strict=False).is_relative_to(self.layout.reservations.parent):
            raise PCWError("Path escapes PCW storage")

    def _ensure_storage(self):
        for path in (self.knowledge, self.state / "lock"):
            self._assert_safe(path)
        if not self.knowledge.is_dir():
            raise RecoveryRequired(self.root, ["knowledge"])
        if not self.state.is_dir():
            raise RecoveryRequired(self.root, ["../.rag"])
        marker = self.control.root / "workspace-opened.json"
        if not marker.exists() and not self._reading:
            self.control.write(marker, "{}\n")
        self._initialized = True

    @staticmethod
    def _schema(dimensions):
        return PCWRuntime._schema(dimensions)

    def _initial_settings(self):
        source = self.legacy_state / "active.json"
        self._assert_safe(source)
        if source.is_file():
            old = json.loads(read_utf8(source))
            defaults = super()._initial_settings()
            return {key: old.get(key, defaults[key]) for key in defaults}
        return super()._initial_settings()

    def _allocate_material_id(self):
        path = self.layout.reservations
        self._assert_safe(path)
        if (self.state / "material-ids.json").exists():
            raise PCWError("Workspace material reservations require explicit storage installation/update")
        reserved = json.loads(read_utf8(path)) if path.is_file() else {}
        for directory in self.control.root.parent.iterdir():
            previous = directory / "material-ids.json"
            self._assert_safe(previous)
            if previous.is_file():
                reserved.update(json.loads(read_utf8(previous)))
            for version in (4, 5, 6):
                receipt = directory / f"migration-v{version}.json"
                self._assert_safe(receipt)
                if receipt.is_file():
                    from .migration_identity import validate_mapping
                    for item in json.loads(read_utf8(receipt)).get("identity_map", []):
                        validate_mapping(item)
                        reserved[item["new_id"]] = {"workspace": item["workspace"]}
        reserved.update({ident: {} for ident in self.catalog})
        while True:
            ident = uuid.uuid4().hex + ".md"
            if ident not in reserved:
                reserved[ident] = {"workspace": self.workspace}
                self._write(path, json.dumps(reserved, sort_keys=True) + "\n")
                local = self.control.reservations()
                local[ident] = reserved[ident]
                self.control.save_reservations(local)
                return ident

    def _scan(self):
        from .context_identity import assert_unique_context_names, context_metadata
        assert_unique_context_names(self.layout)
        self.materials, self.catalog, self.owners = {}, {}, {}
        self.unregistered, self.incompatible = [], []
        self._legacy_ids, self._legacy_id_paths, self._legacy_link_paths = (
            {},
            set(),
            set(),
        )
        cache = {}
        self.categories = []
        for root in sorted(self.root.parent.iterdir()):
            if root.name.startswith("."):
                continue
            self._assert_safe(root)
            if not root.is_dir():
                continue
            _id(root.name)
            marker = control_store(self.work_root, root.name).root / "format.json"
            self._assert_safe(marker)
            compatible = (
                marker.is_file()
                and json.loads(read_utf8(marker)).get("format_version") in (2, 3, 4, 5, 6)
            )
            if root != self.root and not compatible:
                self.incompatible.append(root.name)
                continue
            self.categories.append(root.name)
            from .context_document import record_paths
            for path in record_paths(root):
                self._assert_safe(path)
                stat = path.stat()
                signature = (
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
                key = (root.name, path.relative_to(root).as_posix())
                cached = self._file_cache.get(key)
                if cached is not None and cached[0] == signature:
                    metadata, body = copy.deepcopy(cached[1:])
                else:
                    metadata, body = _parse(path.read_bytes().decode("utf-8"))
                cache[key] = (signature, copy.deepcopy(metadata), body)
                metadata = context_metadata(metadata, root.name)
                if "id" not in metadata or needs_source_upgrade(body):
                    raise PCWError(
                        f"Workspace {root.name} requires pcw-upgrade: {path.name}"
                    )
                ident = _id(metadata["id"])
                if ident in self.catalog:
                    raise PCWError(
                        f"Duplicate YAML material ID {ident}: {self.owners[ident]} and {root.name}"
                    )
                declarations(body)
                material = Material(
                    ident, path.relative_to(root).as_posix(), metadata, body
                )
                self.catalog[ident], self.owners[ident] = material, root.name
                if root == self.root:
                    self.materials[ident] = material
        # All destinations must exist before resolving paths across contexts.
        codec = self.link_codec()
        for ident, material in self.catalog.items():
            material.metadata["links"] = codec.decode(
                material.metadata.get("links", []), ident, self.owners[ident]
            )
        self._content_signature = tuple(
            (i, m.path, m.revision) for i, m in self.materials.items()
        )
        self._catalog_signature = tuple(
            (i, self.owners[i], m.path, m.revision) for i, m in self.catalog.items()
        )
        self._file_cache = cache

    def library_capture(self):
        """Read source records even when the optional derived index is absent."""
        with self._mutex:
            if not self.root.is_dir():
                raise PCWError("Workspace does not exist")
            self._scan()
            return {"incompatible": list(self.incompatible)}

    def _rows(self, columns=None):
        return self.index.read_rows(
            self.table,
            columns,
            f"workspace = '{_id(self.workspace)}'"
            + (" AND " + self._namespace() if self._namespace() else ""),
        )

    def _reconcile(self):
        if not self._reading:
            # A file moved between categories keeps both identity and vectors.
            # Only retag complete, unchanged rows; stale bodies still re-embed.
            groups = {}
            for row in self.index.read_rows(
                self.table,
                [
                    "material_id",
                    "workspace",
                    "revision",
                    "chunking_id",
                    "part_index",
                    "part_count",
                    "metadata",
                ],
                self._namespace(),
            ):
                groups.setdefault(row["material_id"], []).append(row)
            for ident, rows in groups.items():
                material = self.catalog.get(ident)
                if (
                    material
                    and any(r["workspace"] != self.owners[ident] or json.loads(r.get("metadata") or "{}") != material.metadata for r in rows)
                    and all(
                        r["revision"] == material.revision
                        and r["chunking_id"] == self._chunking_id()
                        and r["part_count"] == len(rows)
                        for r in rows
                    )
                    and {r["part_index"] for r in rows} == set(range(len(rows)))
                ):
                    self.table.update(
                        where=f"material_id = '{_id(ident)}'"
                        + (" AND " + self._namespace() if self._namespace() else ""),
                        values={
                            "workspace": self.owners[ident],
                            "path": material.path,
                            "metadata": json.dumps(material.metadata),
                            **(
                                {
                                    "module": "workspace",
                                    "collection": self.owners[ident],
                                    "object_id": ident,
                                }
                                if "module" in self.table.schema.names
                                else {}
                            ),
                        },
                    )
        super()._reconcile()

    def _build_rows(self, material, *args):
        return [
            {**row, "workspace": self.owners.get(material.id, self.workspace)}
            for row in super()._build_rows(material, *args)
        ]

    def _save(self, material):
        from .context_identity import context_metadata
        material.metadata = context_metadata(material.metadata, self.workspace)
        material.metadata["id"] = material.id
        declarations(material.content)
        material.metadata.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
        catalog = {**self.catalog, material.id: material}
        owners = {**self.owners, material.id: self.workspace}
        codec = self.link_codec(catalog, owners)
        path = self.root / material.path
        aliases = self.link_codec().display_aliases(_parse(read_utf8(path))[0].get('links', []), material.id) if path.is_file() else {}
        self._write(path, codec.record_text(material.metadata, material.content, aliases=aliases))
        observed, body = _parse((self.root / material.path).read_bytes().decode("utf-8"))
        observed["links"] = codec.decode(observed.get("links", []), material.id)
        if body != material.content or observed != material.metadata:
            raise PCWError("Saved material read-back differs from requested content")
        self.catalog[material.id], self.owners[material.id] = material, self.workspace

    def link_codec(self, catalog=None, owners=None):
        return LinkCodec(self.work_root, self.catalog if catalog is None else catalog,
                         self.owners if owners is None else owners)

    def create(self, content, links=None, description=None):
        with self._guard():
            from .records import validate_description
            validate_description({"description": description}, required=True)
            content = migrate_sources(content)
            ident = self._allocate_material_id()
            material = Material(
                ident,
                self.new_path(content, ident),
                {"id": ident, "type": "knowledge", "description": description,
                 "links": self._check_links(links or [], ident)},
                content,
            )
            try:
                self._save(material)
            except Exception as exc:
                if not (self.root / material.path).is_file():
                    raise
                self._scan()
                self._reconcile()
                return {
                    **self._result(self._material(ident)),
                    "status": "partial",
                    "write_error": type(exc).__name__,
                    "next_action": "read",
                }
            self.materials[ident] = material
            if content.strip():
                self.pending.add(ident)
            return self._index_receipt(material)

    def rename(self, material_id, path):
        with self._guard():
            material = self._material(material_id)
            target = self._knowledge_path(path)
            if target.exists() and target != self.root / material.path:
                raise PCWError("Move destination already exists")
            if target != self.root / material.path and any(
                material.id in other.links for other in self.catalog.values()
            ):
                raise PCWError("Rename has incoming navigation links; use a reviewed record proposal to update affected paths")
            return self._move_same_id(material, target, path)

    def new_path(self, content, ident, occupied=()):
        name = topic_name(content, ident)
        candidate = "knowledge/" + name
        used = {m.path for m in self.materials.values()} | set(occupied)
        if candidate in used or (self.root / candidate).exists():
            candidate = (
                "knowledge/" + name[:-3] + "-" + ident.removesuffix(".md") + ".md"
            )
        if candidate in used or (self.root / candidate).exists():
            raise PCWError("Knowledge path collision")
        self._knowledge_path(candidate.removeprefix("knowledge/"))
        return candidate

    def _material(self, ident):
        from .migration_identity import legacy_reference
        return super()._material(legacy_reference(self, ident))

    def effective_task_id(self, ident):
        if ident is None:
            return None
        from .migration_identity import legacy_reference
        with self.read_guard():
            return legacy_reference(self, ident, local_task=True)

    def resolve(self, ident):
        from .migration_identity import legacy_reference
        ident = legacy_reference(self, ident)
        _id(ident)
        if ident not in self.catalog:
            raise PCWError(f"Unknown material ID: {ident}")
        return self.catalog[ident]

    def _check_links(self, links, ident):
        links = _unique_links(links, ident)
        return _unique_links([self.resolve(target).id for target in links], ident)

    def assert_no_foreign_incoming(self, removed):
        for material in self.catalog.values():
            if self.owners[material.id] != self.workspace and set(material.links) & set(
                removed
            ):
                raise PCWError(
                    f"Material {material.id} in {self.owners[material.id]} links to a removed ID; explicitly update that context first"
                )

    def _label(self, ident):
        material = self.catalog.get(ident) or self.materials.get(ident)
        if material is None:
            return {"id": ident, "title": ident, "description": "", "missing": True}
        return {
            "id": ident,
            "title": next(
                (
                    s[2:].strip()
                    for s in material.content.splitlines()
                    if s.startswith("# ")
                ),
                ident,
            ),
            "description": material.metadata.get("description", ""),
            "type": material.metadata.get("type", "knowledge"),
            "status": material.metadata.get("status"),
            "tags": list(material.metadata.get("tags", [])),
            "workspace": self.owners.get(ident, self.workspace),
            "path": material.path,
        }

    def _material_invalidations(self, material):
        owner = self.owners.get(material.id, self.workspace)
        original = self.control
        try:
            self.control = control_store(self.work_root, owner)
            return super()._material_invalidations(material)
        finally:
            self.control = original

    def _indexed_ids(self):
        groups = {}
        for row in self.index.read_rows(
            self.table,
            [
                "material_id",
                "workspace",
                "revision",
                "part_index",
                "part_count",
                "chunking_id",
            ],
            self._namespace(),
        ):
            groups.setdefault(row["material_id"], []).append(row)
        result = set()
        for ident, rows in groups.items():
            material = self.catalog.get(ident)
            if (
                material
                and all(
                    r["workspace"] == self.owners[ident]
                    and r["revision"] == material.revision
                    and r["chunking_id"] == self._chunking_id()
                    and r["part_count"] == len(rows)
                    for r in rows
                )
                and {r["part_index"] for r in rows} == set(range(len(rows)))
            ):
                result.add(ident)
        return result

    def _result(self, material, content=False):
        result = super()._result(material, content)
        from .records import record_revision
        result["revision"] = record_revision(material)
        if self.owners.get(material.id, self.workspace) != self.workspace:
            result["index_status"] = (
                "indexed"
                if not material.content.strip() or material.id in self._indexed_ids()
                else "pending_preparation"
            )
        if content:
            result["sources"] = declarations(material.content)
        return result

    def read(self, material_id):
        with self._guard():
            return self._result(self.resolve(material_id), content=True)

    def read_many(self, material_ids):
        with self._guard():
            return {
                "materials": [
                    self._result(self.resolve(i), content=True)
                    for i in self._batch_ids(material_ids)
                ]
            }

    def search(
        self,
        query,
        N=None,
        d_max=None,
        min_jump=None,
        multiplier=None,
        categories=None,
        all_categories=False,
        types=None,
        statuses=None,
        selection=None,
        output="materials",
        purpose=None,
        task_id=None,
        candidate_limit=None,
    ):
        if candidate_limit is not None and (type(candidate_limit) is not int or not 1 <= candidate_limit <= 100000):
            raise PCWError("Invalid candidate_limit")
        with self._guard():
            if type(all_categories) is not bool or (
                all_categories and categories is not None
            ):
                raise PCWError("Choose categories or all_categories, not both")
            from contextopus.core.settings import matches_location
            from .configuration import WorkspaceSettings as Settings

            from contextopus.core.filtering import registry_for_layout
            algorithm_registry = registry_for_layout(self.layout)
            snapshot = Settings(self.layout, registry=algorithm_registry).snapshot(
                module="workspace", context=self.workspace,
                request={"selection": selection} if selection is not None else None,
            )
            settings = snapshot["values"]
            from contextopus.core.filtering import select
            effective_selection = settings["selection"]
            parameters = dict(effective_selection["parameters"])
            for key,value in {"N":N, "d_max":d_max, "min_jump":min_jump, "multiplier":multiplier}.items():
                if value is not None:
                    if key not in parameters:
                        raise PCWError(f"{key} is not supported by the selected algorithm")
                    parameters[key] = value
                    snapshot["sources"]["selection.parameters." + key] = "request.legacy"
            effective_selection = {**effective_selection, "parameters":parameters}
            selected = list(self.categories) if all_categories else categories
            if selected is None and settings.get("context"):
                selected = [settings["context"]]
            if selected is None:
                selected = [self.workspace]
            if not isinstance(selected, list) or not selected or len(selected) > 1000:
                raise PCWError("categories must be a nonempty list of workspace names")
            selected = list(dict.fromkeys(_id(item) for item in selected))
            for name in selected:
                if name in self.incompatible:
                    raise PCWError(
                        f"Workspace {name} requires pcw-upgrade before cross-category search"
                    )
                target = self.root.parent / name
                self._assert_safe(target)
                if not target.is_dir():
                    raise PCWError(f"Unknown category: {name}")
            if all_categories and self.incompatible:
                raise PCWError(
                    "pcw-upgrade required for categories: "
                    + ", ".join(self.incompatible)
                )
            options = dict(self.active["search"])
            options["N"] = settings["searchN"]
            if settings["distance"] is not None:
                options["d_max"] = settings["distance"]
            options.update(
                {
                    k: v
                    for k, v in dict(
                        N=N, d_max=d_max, min_jump=min_jump, multiplier=multiplier
                    ).items()
                    if v is not None
                }
            )
            config = SearchConfig(**options)
            for field, values in (("types", types), ("statuses", statuses)):
                if values is not None and (not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values)):
                    raise PCWError(f"{field} must be a nonempty list of names")
            indexed = self._indexed_ids()
            eligible = {
                i
                for i in indexed
                if self.owners[i] in selected
                and (types is None or self.catalog[i].metadata.get("type", "knowledge") in types)
                and (statuses is None or self.catalog[i].metadata.get("status") in statuses)
                and not self._material_invalidations(self.catalog[i])
                and matches_location(self.catalog[i].path, settings)
                and (
                    settings["include_archived"]
                    or not self.catalog[i].metadata.get("archived", False)
                )
                and (
                    settings["object_type"] is None
                    or self.catalog[i].metadata.get(
                        "object_type", self.catalog[i].metadata.get("type")
                    )
                    == settings["object_type"]
                )
            }
            eligible_fragments = sum(1 for r in self._rows(["material_id"]) if r["material_id"] in eligible)
            selection_result = select([], effective_selection, eligible_count=len(eligible), eligible_documents=len(eligible), eligible_fragments=eligible_fragments, output=output,
                                      registry=algorithm_registry)
            results = []
            if eligible:
                vector = self._vectors(
                    self._get_provider(),
                    [query],
                    self.active["config"]["profile"]["dimensions"],
                    "query",
                )[0]
                predicate = (
                    ((self._namespace() + " AND ") if self._namespace() else "")
                    + "workspace IN ("
                    + ",".join("'" + c + "'" for c in selected)
                    + ") AND material_id IN ("
                    + ",".join("'" + i + "'" for i in sorted(eligible))
                    + ")"
                )
                rows = self.index.nearest(
                    self.table,
                    vector,
                    predicate=predicate,
                    columns=["material_id", "text", "revision", "part_index"],
                    limit=candidate_limit,
                    metric=config.metric,
                )
                selection_result = select(
                    [
                        {**self._label(r["material_id"]), "key":["workspace",self.owners[r["material_id"]],r["material_id"]], "distance": r["_distance"], "excerpt": r["text"], "revision":r.get("revision"), "part_index":r.get("part_index")}
                        for r in rows
                    ],
                    effective_selection, eligible_count=len(eligible), eligible_documents=len(eligible), eligible_fragments=eligible_fragments, output=output, registry=algorithm_registry,
                )
            results = selection_result["results"]
            selection_result["diagnostics"]["settings_sources"] = snapshot["sources"]
            selection_result["diagnostics"].update(purpose=purpose, task_id=task_id,
                scope={"module":"workspace", "contexts":selected, "settings_context":self.workspace,
                       "types":types, "statuses":statuses, "object_type":settings["object_type"],
                       "include_archived":settings["include_archived"],
                       "include":settings["include"], "exclude":settings["exclude"]})
            selection_result["diagnostics"]["limits"]["nearest"] = candidate_limit
            selection_result["diagnostics"]["candidate_pool_truncated"] = candidate_limit is not None and eligible_fragments > selection_result["diagnostics"]["counts"]["considered_fragments"]
            pending = sorted(
                i
                for i, m in self.catalog.items()
                if self.owners[i] in selected and m.content.strip() and i not in indexed
            )
            bad = sorted(
                i
                for i, m in self.catalog.items()
                if self.owners[i] in selected and self._material_invalidations(m)
            )
            return {
                "results": results,
                "diagnostics": selection_result["diagnostics"],
                "categories": selected,
                "active_workspace": self.workspace,
                "pending_preparation": pending[:50],
                "pending_count": len(pending),
                "needs_correction": bad[:50],
                "needs_correction_count": len(bad),
                "unregistered": [],
                "unregistered_count": 0,
                "search": dataclasses.asdict(config),
                "search_calibration": self.active["search_calibration"],
            }

    def switch_embeddings(self, config, search, chunking=None):
        # Base rebuild scans self.materials; expose the entire catalog for that
        # operation, while retaining the same shared file lock.
        with self._guard():
            original_scan = self._scan

            def scan_all():
                original_scan()
                self.materials = self.catalog
                self._content_signature = self._catalog_signature

            try:
                self._scan = scan_all
                scan_all()
                return super().switch_embeddings(config, search, chunking)
            finally:
                self._scan = original_scan
                self._scan()
                self._reconciled_token = None


class SharedRuntime:
    """Workspace composition boundary; vectors are owned by ContextCore."""

    def __init__(self, *args, **kwargs):
        object.__setattr__(self, "_adapter", WorkspaceSourceAdapter(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._adapter, name)

    def __setattr__(self, name, value):
        setattr(self._adapter, name, value)

    def __delattr__(self, name):
        delattr(self._adapter, name)
