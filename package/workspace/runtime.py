"""Knowledge is authoritative; LanceDB contains replaceable search representations.

There is deliberately no source-project reader, language-model splitter, remote
push, background service, or hidden embedding fallback in this module.
"""

from __future__ import annotations

from .text_io import read_utf8

import contextlib
import copy
import dataclasses
from datetime import datetime, timezone
from array import array
from contextopus.core import locking as fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import threading
from typing import Any, Callable
import uuid

import lancedb
import pyarrow as pa
import yaml

from contextopus.core.embeddings import (
    create_provider,
    default_config,
    embedding_identity,
    validate_config,
)
from contextopus.core.provider_pool import PROCESS_PROVIDERS
from .control import RecoveryRequired, control_store
from contextopus.core.chunking import (
    TokenChunker,
    default_chunking,
    validate_chunking,
    chunking_identity,
)
from contextopus.core.embeddings import EmbeddingError


class PCWError(ValueError):
    """An actionable memory error; completed multi-file writes are not rolled back."""


@dataclasses.dataclass(frozen=True)
class SearchConfig:
    # Engineering starting values, explicitly NOT empirically calibrated for PCW.
    N: int = 20
    d_max: float = 0.8
    min_jump: float = 0.02
    multiplier: float = 3.0
    metric: str = "cosine"

    def __post_init__(self):
        if (
            isinstance(self.N, bool)
            or not isinstance(self.N, int)
            or not 1 <= self.N <= 1000
        ):
            raise PCWError("N must be an integer between 1 and 1000")
        for field in ("d_max", "min_jump", "multiplier"):
            value = getattr(self, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or value < 0
            ):
                raise PCWError(f"{field} must be a finite nonnegative number")
        if self.min_jump <= 0 or self.multiplier <= 0:
            raise PCWError("min_jump and multiplier must be positive")
        if self.metric not in {"cosine", "l2"}:
            raise PCWError("metric must be cosine or l2 (squared Euclidean distance)")


def select_candidates(rows: list[dict], config: SearchConfig) -> list[dict]:
    """Compatibility adapter preserving the legacy search selection order."""
    from contextopus.core.filtering import select
    return select(rows, legacy=dataclasses.asdict(config))["results"]


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PCWError("Material ID must be an exact safe identifier")
    return value


def _revision(content: str) -> str:
    # Only the saved knowledge body, never source-project files, is hashed.
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _unique_links(values: list[str], own_id: str) -> list[str]:
    if not isinstance(values, list):
        raise PCWError("links must be a list of exact IDs")
    return list(dict.fromkeys(_id(value) for value in values if value != own_id))


@dataclasses.dataclass
class Material:
    id: str
    path: str
    metadata: dict
    content: str
    revision: str = dataclasses.field(init=False)

    def __post_init__(self):
        self.revision = _revision(self.content)

    def set_content(self, content: str):
        self.content = content
        self.revision = _revision(content)

    @property
    def links(self):
        return self.metadata["links"]


def _parse(text: str) -> tuple[dict, str]:
    match = _FRONT.match(text)
    if not match:
        return {}, text
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise PCWError("Invalid knowledge YAML frontmatter") from exc
    if not isinstance(metadata, dict):
        raise PCWError("Knowledge frontmatter must be a mapping")
    return metadata, text[match.end() :]


class PCWRuntime:
    """One explicitly selected workspace. All managed writes stay below .pcw.

    provider_factory is an injection seam for tests. Production MCP always uses
    create_provider, and LanceDB is mandatory for both test and production runs.
    """

    def __init__(
        self,
        work_root: str | Path,
        workspace: str,
        provider_factory: Callable = PROCESS_PROVIDERS.acquire,
        *,
        initialize: bool = True,
        allow_create: bool = True,
    ):
        raw_root = Path(work_root)
        if not raw_root.is_absolute() or not raw_root.is_dir():
            raise PCWError("work_root must be an existing absolute directory")
        if not _ID.fullmatch(workspace) or workspace in {".", ".."}:
            raise PCWError("workspace must be one safe directory name")
        from .layout import WorkspaceLayout as StorageLayout

        self.work_root = StorageLayout.resolve(raw_root).root
        self.layout = StorageLayout(self.work_root)
        self.root = self.layout.workspace_base / workspace
        self.knowledge = self.root / "knowledge"
        self.state = self.root / ".pcw-runtime"
        self.control = control_store(self.work_root, workspace)
        self._initialized = False
        self._allow_create = allow_create
        self._reading = False
        self._factory = provider_factory
        self._provider = None
        self._provider_config = None
        self._chunker = None
        self._chunker_key = None
        from contextopus.core.rag import VectorIndex

        self.index = VectorIndex()
        self._mutex = threading.RLock()
        self._depth = 0
        self._file_cache = {}
        self._reconciled_token = None
        self._assert_safe(self.state / "lancedb")
        if initialize:
            with self._guard():
                pass

    def _ensure_storage(self):
        self._assert_safe(self.state / "lock")
        self._assert_safe(self.knowledge)
        marker = self.control.root / "workspace-opened.json"
        self.control.safe(marker)
        if not self._initialized and not self.root.exists() and not marker.exists():
            if not self._allow_create:
                raise PCWError(
                    "Workspace does not exist; use explicit workspace creation"
                )
            self.knowledge.mkdir(parents=True)
            self.state.mkdir()
        missing = [
            str(path.relative_to(self.root))
            for path in (self.knowledge, self.state)
            if not path.is_dir()
        ]
        if missing:
            raise RecoveryRequired(self.root, missing)
        self._initialized = True
        if not marker.exists() and not self._reading:
            self.control.write(
                marker,
                json.dumps({"opened_at": datetime.now(timezone.utc).isoformat()})
                + "\n",
            )

    def recover_workspace(self, recreate_missing_knowledge: bool = False):
        """Explicit bounded worker recovery; never silently accept lost knowledge."""
        with self.control.guard(), self._mutex:
            self._assert_safe(self.knowledge)
            self._assert_safe(self.state / "lock")
            missing_knowledge = not self.knowledge.is_dir()
            if missing_knowledge and not recreate_missing_knowledge:
                return {
                    "status": "blocked",
                    "reason": "Knowledge is missing. Restore it or explicitly authorize recreation.",
                    "next_action": "restore_knowledge",
                    "workspace": str(self.root),
                }
            self.knowledge.mkdir(parents=True, exist_ok=True)
            self.state.mkdir(parents=True, exist_ok=True)
            self._file_cache.clear()
            self._reconciled_token = None
            with self._guard():
                return {
                    "status": "recovered",
                    "workspace": str(self.root),
                    "pending_preparation": sorted(self.pending),
                    "knowledge_recreated": missing_knowledge,
                }

    def _allocate_material_id(self):
        reservations = self.control.reservations()
        while True:
            ident = uuid.uuid4().hex + ".md"
            if ident not in reservations and not (self.knowledge / ident).exists():
                reservations[ident] = {
                    "allocated_at": datetime.now(timezone.utc).isoformat()
                }
                self.control.save_reservations(reservations)
                return ident

    def _assert_safe(self, path: Path):
        try:
            path.relative_to(self.layout.workspace_base)
        except ValueError as exc:
            raise PCWError("Path is outside selected .pcw storage") from exc
        for candidate in (self.layout.workspace_base, *path.parents, path):
            if candidate == self.work_root or self.work_root not in candidate.parents:
                continue
            if candidate.is_symlink():
                raise PCWError(f"Symlinks are not allowed in memory paths: {candidate}")
        if not path.resolve(strict=False).is_relative_to(self.root):
            raise PCWError("Path escapes the selected memory workspace")

    def _knowledge_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or "\\" in relative:
            raise PCWError("Use a relative knowledge path with forward slashes")
        parts = PurePosixPath(relative)
        if (
            parts.is_absolute()
            or not parts.parts
            or any(p in {"..", "."} or p.startswith(".") for p in parts.parts)
        ):
            raise PCWError("Knowledge path must not traverse or use hidden files")
        if parts.suffix != ".md":
            raise PCWError("Knowledge materials must have the .md extension")
        path = self.knowledge.joinpath(*parts.parts)
        self._assert_safe(path)
        return path

    @staticmethod
    def _material_id(path: Path) -> str:
        """The filename, including .md, is the sole material identity."""
        return _id(path.name)

    def _check_edit_reservations(self, material_ids=None):
        from .edit_reservations import EditReservations
        EditReservations(self.control).check_write(material_ids)

    def _write(self, path: Path, text: str):
        self._assert_safe(path)
        if (path.is_relative_to(self.knowledge) or path == self.root / "Context.md") and path.suffix == ".md":
            ids = {_parse(text)[0].get("id", path.name)}
            if path.is_file():
                ids.add(_parse(path.read_bytes().decode("utf-8"))[0].get("id", path.name))
            self._check_edit_reservations(list(ids))
        format_path = self.control.root / "format.json"
        # Recovery of a preceding migration must reproduce its immutable bytes;
        # the current migration adds owner tags after that recovery completes.
        current_format = (json.loads(read_utf8(format_path)).get("format_version", 0)
                          if format_path.is_file() else 0)
        if current_format >= 5 and (path.is_relative_to(self.knowledge) or path == self.root / "Context.md") and path.suffix == ".md":
            from .context_identity import context_metadata
            metadata, body = _parse(text)
            if metadata.get("id"):
                metadata = context_metadata(metadata, self.root.name)
                text = "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n" + body
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=".pcw-write-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            # Durability of the active-pointer rename includes its parent entry.
            from contextopus.core.storage import sync_directory

            sync_directory(path.parent)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _save(self, material: Material):
        material.metadata.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
        header = yaml.safe_dump(material.metadata, sort_keys=False, allow_unicode=True)
        self._write(self.root / material.path, f"---\n{header}---\n{material.content}")
        observed, body = _parse(
            (self.root / material.path).read_bytes().decode("utf-8")
        )
        if body != material.content or observed != material.metadata:
            raise PCWError("Saved material read-back differs from requested content")

    def _write_active(self, active: dict):
        self._write(
            self.state / "active.json",
            json.dumps(active, ensure_ascii=False, indent=2) + "\n",
        )

    def _upgrade_index(self):
        if self.active.get("index_format") == 2:
            return
        previous = self.active
        candidate = {**previous, "index_format": 2}
        if "chunking" not in candidate:
            candidate["chunking"] = (
                default_chunking()
                if candidate["config"]["provider"]["type"] == "sentence-transformers"
                else None
            )
        name = "parts_" + uuid.uuid4().hex
        self.db.create_table(
            name, schema=self._schema(candidate["config"]["profile"]["dimensions"])
        )
        candidate["table"] = name
        candidate["search_calibration"] = (
            "token windows; retrieval calibration not established"
        )
        # Publish before removing the previous derived table. Knowledge and old
        # control records are never rewritten by an index-format upgrade.
        self._write_active(candidate)
        self.active = candidate
        self.db.drop_table(previous["table"])

    def _chunking_id(self):
        if self.active.get("chunking") is None:
            return "unconfigured"
        return chunking_identity(self.active["chunking"], self.active["config"])

    def startup(self):
        with self._guard():
            result = self.status()
            result.pop("active")
            return result

    @staticmethod
    def _schema(dimensions: int):
        from contextopus.core.rag import RagIndex

        return RagIndex.schema(dimensions)

    @contextlib.contextmanager
    def read_guard(self):
        """Open existing sources/index without creating, migrating or reconciling data."""
        if not self.root.is_dir():
            raise PCWError("Workspace does not exist")
        with self.control.guard(), self._mutex:
            previous = self._reading
            self._reading = True
            try:
                with self._guard():
                    yield
            finally:
                self._reading = previous
                # A read-only reconciliation must not suppress later repair.
                self._reconciled_token = None

    @contextlib.contextmanager
    def _guard(self):
        from .layout import WorkspaceLayout as StorageLayout

        if (self.work_root / ".contextopus-freeze.json").exists():
            raise PCWError(
                "Root is frozen for relocation; reopen through the relocation redirect"
            )
        with self.control.guard(), self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self._ensure_storage()
            with (self.state / "lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._depth = 1
                try:
                    self._assert_safe(self.state / "active.json")
                    self._assert_safe(self.state / "lancedb")
                    if self._reading and (
                        not (self.state / "active.json").is_file()
                        or not (self.state / "lancedb").is_dir()
                    ):
                        raise PCWError(
                            "Index is missing; use explicit recovery before search"
                        )
                    self.db = self.index.connect(self.state / "lancedb")
                    active_path = self.state / "active.json"
                    if not active_path.exists():
                        settings = self._initial_settings()
                        config = settings["config"]
                        name = "parts_" + uuid.uuid4().hex
                        self.index.create_table(
                            name,
                            config["profile"]["dimensions"],
                            schema=self._schema(config["profile"]["dimensions"]),
                        )
                        self._write_active(
                            {
                                **settings,
                                "table": name,
                                "index_format": 2,
                                "search_calibration": "engineering defaults; not calibrated on PCW",
                            }
                        )
                    self.active = json.loads(active_path.read_text(encoding="utf-8"))
                    if self._reading:
                        if self.active.get("index_format", 1) != 2:
                            raise PCWError("Index migration required; use pcw-upgrade")
                    else:
                        self._upgrade_index()
                    self.table = self.index.open_table(self.active["table"])
                    self._scan()
                    self._reconcile()
                    yield
                finally:
                    self._depth = 0
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _initial_settings(self):
        return {
            "config": default_config(),
            "chunking": default_chunking(),
            "search": dataclasses.asdict(SearchConfig()),
        }

    def _scan(self):
        self.materials: dict[str, Material] = {}
        self.unregistered = []
        self._legacy_ids: dict[str, str] = {}
        self._legacy_id_paths: set[str] = set()
        self._legacy_link_paths: set[str] = set()
        cache = {}
        found = []
        found_ids = set()
        self._assert_safe(self.knowledge)
        for path in sorted(self.knowledge.rglob("*.md")):
            if any(
                part.startswith(".") for part in path.relative_to(self.knowledge).parts
            ):
                continue
            self._assert_safe(path)
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            stat = path.stat()
            signature = (
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
            cached = self._file_cache.get(relative)
            if cached is not None and cached[0] == signature:
                metadata, body, revision = copy.deepcopy(cached[1:])
            else:
                metadata, body = _parse(path.read_bytes().decode("utf-8"))
                revision = _revision(body)
            cache[relative] = (signature, copy.deepcopy(metadata), body, revision)
            ident = self._material_id(path)
            if ident in found_ids:
                raise PCWError(f"Duplicate material filename ID: {ident}")
            found_ids.add(ident)
            # A legacy id is retained only as migration input. It is never used
            # to address the material during normal runtime operations.
            legacy_id = metadata.get("id")
            if legacy_id is not None:
                legacy_id = _id(legacy_id)
                previous = self._legacy_ids.get(legacy_id)
                if previous is not None and previous != ident:
                    raise PCWError(f"Duplicate legacy material ID: {legacy_id}")
                self._legacy_ids[legacy_id] = ident
                self._legacy_id_paths.add(relative)
            found.append((ident, relative, metadata, body, revision))

        for ident, relative, metadata, body, revision in found:
            raw_links = metadata.get("links", [])
            if not isinstance(raw_links, list):
                raise PCWError(f"links must be a list in {relative}")
            canonical_links = _unique_links(
                [self._legacy_ids.get(_id(value), _id(value)) for value in raw_links],
                ident,
            )
            if canonical_links != raw_links:
                self._legacy_link_paths.add(relative)
            # Missing links are a valid empty navigation list. Do not write the
            # file merely because it is an older plain Markdown material.
            metadata["links"] = canonical_links
            material = Material.__new__(Material)
            material.id, material.path, material.metadata = ident, relative, metadata
            material.content, material.revision = body, revision
            self.materials[ident] = material
        self._file_cache = cache
        self._content_signature = tuple(
            (material.id, material.path, material.revision)
            for material in self.materials.values()
        )

    def _rows(self, columns=None) -> list[dict]:
        return self.index.read_rows(self.table, columns, self._namespace())

    def _namespace(self):
        return (
            "(module = 'workspace' OR module IS NULL)"
            if "module" in self.table.schema.names
            else None
        )

    def _delete_rows(self, ident: str):
        _id(ident)
        aliases = [ident] + [
            old for old, target in self._legacy_ids.items() if target == ident
        ]
        for value in dict.fromkeys(aliases):
            self.table.delete(
                f"material_id = '{_id(value)}'"
                + (" AND " + self._namespace() if self._namespace() else "")
            )

    def _reconcile(self):
        """Reconcile saved memory only, including externally restored Git content."""
        token = (
            self.table.name,
            self.table.version,
            self._content_signature,
            self._chunking_id(),
        )
        if token == self._reconciled_token and not self._reading:
            return
        rows = self._rows(
            [
                "material_id",
                "revision",
                "path",
                "chunking_id",
                "part_index",
                "part_count",
            ]
        )
        chunk_id = self._chunking_id()
        from contextopus.core.rag import DocumentKey, IndexDocument

        documents = {
            ident: IndexDocument(
                DocumentKey("workspace", self.root.name, ident),
                m.revision,
                m.content,
                m.path,
            )
            for ident, m in self.materials.items()
        }
        changed, paths, indexed = self.index.reconcile_rows(
            rows, documents, chunk_id, self._legacy_ids
        )
        if not self._reading:
            for ident in changed:
                self._delete_rows(ident)
            for ident, path in paths.items():
                if ident not in changed:
                    self.table.update(
                        where=f"material_id = '{_id(ident)}'"
                        + (" AND " + self._namespace() if self._namespace() else ""),
                        values={"path": path},
                    )
        self.pending = {
            ident
            for ident, material in self.materials.items()
            if material.content.strip()
            and (
                ident not in indexed
                or ident in changed
                or any(self._legacy_ids.get(old, old) == ident for old in changed)
            )
        }
        self._reconciled_token = (
            self.table.name,
            self.table.version,
            self._content_signature,
            self._chunking_id(),
        )

    def _material(self, ident: str):
        _id(ident)
        if ident not in self.materials:
            raise PCWError(f"Unknown material ID: {ident}")
        return self.materials[ident]

    def _check_links(self, links: list[str], ident: str):
        links = _unique_links(links, ident)
        for target in links:
            self._material(target)
        return links

    def _result(self, material: Material, content=False):
        result = {
            "id": material.id,
            "path": material.path,
            "links": material.links,
            **self._label(material.id),
            "link_targets": [self._label(ident) for ident in material.links],
            "revision": material.revision,
            "index_status": "pending_preparation"
            if material.id in self.pending
            else "indexed",
        }
        if content:
            result["content"] = material.content
        for field in ("saved_at", "executor_session_id"):
            if field in material.metadata:
                result[field] = material.metadata[field]
        invalidations = self._material_invalidations(material)
        if invalidations:
            result.update(
                knowledge_status="needs_correction",
                invalidations=invalidations,
                warning="This saved revision has a known discrepancy. Correct the knowledge before using it as an established fact.",
            )
        return result

    def _label(self, ident):
        material = self.materials.get(ident)
        if material is None:
            return {"id": ident, "title": ident, "description": "", "missing": True}
        title = next(
            (
                line[2:].strip()
                for line in material.content.splitlines()
                if line.startswith("# ")
            ),
            ident,
        )
        return {
            "id": ident,
            "title": title,
            "description": material.metadata.get("description", ""),
        }

    def _material_invalidations(self, material: Material):
        # Read-only migration compatibility: do not forget known bad revisions
        # from 0.7.x, and never resume its task/environment/assignment controller.
        path = self.control.root / "task-ledger.json"
        self.control.safe(path)
        data = json.loads(read_utf8(path)) if path.exists() else {}
        from .records import record_revision
        return [
            {
                "material_id": material.id,
                "revision": material.revision,
                "reason": entry.get("discrepancy_reason", "Needs correction"),
            }
            for task in data.get("tasks", {}).values()
            for entry in task.get("assignments", {}).values()
            if entry.get("affected_materials", {}).get(material.id) in (material.revision, record_revision(material))
        ]

    def _needs_correction(self):
        return sorted(
            material.id
            for material in self.materials.values()
            if self._material_invalidations(material)
        )

    def status(self):
        with self._guard():
            needs_correction = self._needs_correction()
            return {
                "workspace": str(self.root),
                "materials": len(self.materials),
                "pending_preparation": sorted(self.pending)[:50],
                "pending_count": len(self.pending),
                "needs_correction": needs_correction[:50],
                "needs_correction_count": len(needs_correction),
                "unregistered": self.unregistered[:50],
                "unregistered_count": len(self.unregistered),
                "active": self.active,
            }

    def durable_state(self):
        """Observable durable boundary, including project instructions and metadata changes."""
        with self._guard():
            files = {}
            for path in [self.root / "INSTRUCTIONS.md", self.root / "Context.md", *self.knowledge.rglob("*.md")]:
                self._assert_safe(path)
                if path.is_file():
                    files[path.relative_to(self.root).as_posix()] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
            return {
                "files": files,
                "paths": {key: value.path for key, value in self.materials.items()},
                "pending": sorted(self.pending),
                "unregistered": self.unregistered,
            }

    def uncheckpointed(self, paths: list[str]):
        if not paths:
            return []
        with self._guard():
            paths = self._git_paths(paths)
            if not (self.root / ".git").exists():
                return paths
            self._own_git()
            dirty = []
            for relative in paths:
                path = self.root / relative
                historical = self._git("show", "HEAD:" + relative, check=False)
                differs = (path.exists() != (historical.returncode == 0)) or (
                    path.is_file()
                    and read_utf8(path) != historical.stdout
                )
                if not path.exists() and historical.returncode:
                    history = self._git(
                        "log", "-1", "--format=%H", "--", relative, check=False
                    )
                    differs = bool(history.returncode or not history.stdout.strip())
                staged = self._git(
                    "diff", "--cached", "--quiet", "--", relative, check=False
                )
                if differs or staged.returncode:
                    dirty.append(relative)
            return dirty

    def list(self, offset: int = 0, limit: int = 50):
        with self._guard():
            if (
                type(offset) is not int
                or offset < 0
                or type(limit) is not int
                or not 1 <= limit <= 100
            ):
                raise PCWError("offset must be nonnegative and limit between 1 and 100")
            values = list(self.materials.values())
            return {
                "materials": [
                    self._result(material)
                    for material in values[offset : offset + limit]
                ],
                "total": len(values),
                "offset": offset,
                "next_offset": offset + limit if offset + limit < len(values) else None,
                "unregistered": self.unregistered[offset : offset + limit],
                "unregistered_count": len(self.unregistered),
            }

    def read(self, material_id: str):
        with self._guard():
            material = self._material(material_id)
            return self._result(material, content=True)

    @staticmethod
    def _batch_ids(material_ids: list[str]):
        if (
            not isinstance(material_ids, list)
            or not material_ids
            or len(material_ids) > 100
            or any(not isinstance(value, str) for value in material_ids)
        ):
            raise PCWError(
                "material_ids must contain between 1 and 100 exact material IDs"
            )
        if len(set(material_ids)) != len(material_ids):
            raise PCWError("material_ids must not contain duplicates")
        return [_id(value) for value in material_ids]

    def read_many(self, material_ids: list[str]):
        """Read only the selected full topic materials."""
        with self._guard():
            materials = [
                self._material(ident) for ident in self._batch_ids(material_ids)
            ]
            return {
                "materials": [
                    self._result(material, content=True) for material in materials
                ]
            }

    def create(self, content: str, links: list[str] | None = None):
        with self._guard():
            path = self._allocate_material_id()
            target = self._knowledge_path(path)
            if target.exists():
                raise PCWError(
                    "Knowledge path already exists; use update or explicit adoption"
                )
            ident = self._material_id(target)
            if ident in self.materials:
                raise PCWError(f"Material filename ID already exists: {ident}")
            material = Material(
                ident,
                target.relative_to(self.root).as_posix(),
                {"links": self._check_links(links or [], ident)},
                content,
            )
            try:
                self._save(material)
            except Exception as exc:
                # A post-rename fsync/read-back failure can leave a real file.
                # Return its identity so a caller cannot mistake it for no save
                # and create a second knowledge file on retry.
                if not target.is_file():
                    raise
                self._scan()
                self._reconcile()
                return {
                    **self._result(self._material(ident)),
                    "status": "partial",
                    "write_error": "Save confirmation failed ("
                    + type(exc).__name__
                    + ")",
                    "next_action": "read",
                }
            self.materials[ident] = material
            if content.strip():
                self.pending.add(ident)
            return self._index_receipt(material)

    def adopt(self, path: str):
        with self._guard():
            target = self._knowledge_path(path)
            metadata, content = _parse(target.read_bytes().decode("utf-8"))
            ident = self._material_id(target)
            relative = target.relative_to(self.root).as_posix()
            if ident in self.materials and self.materials[ident].path != relative:
                raise PCWError(f"Material filename ID already exists: {ident}")
            # id was an old transport identity. Keep every other metadata field,
            # but remove it when this file is explicitly adopted.
            metadata.pop("id", None)
            # Do not infer or reinterpret legacy free-text links.
            raw_links = metadata.get("links", [])
            if not isinstance(raw_links, list):
                raise PCWError("links must be a list of exact IDs")
            metadata["links"] = _unique_links(
                [self._legacy_ids.get(_id(value), _id(value)) for value in raw_links],
                ident,
            )
            material = Material(ident, relative, metadata, content)
            self._save(material)
            self._scan()
            self._reconcile()
            return self._index_receipt(self._material(ident))

    def migrate_legacy_ids(self):
        """Explicitly replace legacy YAML UUID identities with filename identities.

        This preserves bodies and existing LanceDB vectors. It is deliberately a
        separate operation: merely opening a workspace never rewrites knowledge.
        """
        with self._guard():
            mappings: dict[str, str] = {}
            for material in self.materials.values():
                if "id" not in material.metadata:
                    continue
                old = _id(material.metadata["id"])
                previous = mappings.get(old)
                if previous is not None and previous != material.id:
                    raise PCWError(f"Duplicate legacy material ID: {old}")
                mappings[old] = material.id
            # Never acknowledge a migration that leaves an old, unknown ID in
            # navigation. Keep the legacy file unchanged so the caller can
            # resolve the intended target and retry explicitly.
            for material in self.materials.values():
                missing = [
                    link for link in material.links if link not in self.materials
                ]
                if missing:
                    raise PCWError(
                        f"Legacy migration cannot resolve links in {material.id}: "
                        + ", ".join(missing)
                    )
            if not mappings and not self._legacy_link_paths:
                return {
                    "status": "already_migrated",
                    "migrated": [],
                    "index_updated": [],
                }

            applied = []
            index_updated = []
            try:
                # Update existing search rows in place: their text and vectors
                # remain untouched, only the material address changes.
                for old, new in mappings.items():
                    material = self._material(new)
                    if old != new:
                        self.table.update(
                            where=f"material_id = '{_id(old)}'"
                            + (
                                " AND " + self._namespace() if self._namespace() else ""
                            ),
                            values={"material_id": new, "path": material.path},
                        )
                        index_updated.append(f"index:{old}:{new}")
                for material in self.materials.values():
                    changed = (
                        material.path in self._legacy_id_paths
                        or material.path in self._legacy_link_paths
                    )
                    if "id" in material.metadata:
                        material.metadata.pop("id")
                        changed = True
                    canonical = _unique_links(
                        [mappings.get(value, value) for value in material.links],
                        material.id,
                    )
                    if canonical != material.links:
                        material.metadata["links"] = canonical
                        changed = True
                    if changed:
                        self._save(material)
                        applied.append(f"saved:{material.id}")
            except Exception as exc:
                return {
                    "status": "partial",
                    "migrated": applied,
                    "index_updated": index_updated,
                    "error": str(exc),
                    "repair": "Retry pcw_migrate_legacy_ids to complete the interrupted migration; pcw_reconcile is diagnostic only",
                }
            self._scan()
            self._reconciled_token = None
            self._reconcile()
            return {
                "status": "ok",
                "migrated": applied,
                "index_updated": index_updated,
                "pending_preparation": sorted(self.pending)[:50],
                "pending_count": len(self.pending),
            }

    def _update(self, material: Material, content: str):
        self._check_edit_reservations([material.id])
        if material.content != content:
            # Deleting first makes even a failed/partial save safely unsearchable.
            self._delete_rows(material.id)
            material.set_content(content)
            material.metadata["saved_at"] = datetime.now(timezone.utc).isoformat()
            self._save(material)
            if content.strip():
                self.pending.add(material.id)
            else:
                self.pending.discard(material.id)

    def update(
        self, material_id: str, content: str, expected_revision: str | None = None
    ):
        with self._guard():
            material = self._material(material_id)
            if expected_revision is not None and material.revision != expected_revision:
                raise PCWError("Material changed since it was read; read it again")
            self._update(material, content)
            return self._index_receipt(material)

    def _move_same_id(self, material: Material, destination: Path, requested_path: str):
        """Move one filename-ID to another directory without changing identity."""
        self._check_edit_reservations([material.id])
        old_path = material.path
        destination_relative = destination.relative_to(self.root).as_posix()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(self.root / old_path, destination)
        except Exception as exc:
            return {
                "status": "partial",
                "applied": [],
                "error": str(exc),
                "repair": f"Retry pcw_rename({material.id!r}, {requested_path!r}); the source path remains authoritative",
            }

        # The filesystem has one source file after os.rename. If the index path
        # update fails, a fresh runtime's reconciliation observes that same file
        # and completes only the derived path update; it never needs a copied
        # second material with the same filename-ID.
        material.path = destination_relative
        applied = [f"moved:{material.id}:{old_path}:{destination_relative}"]
        try:
            aliases = [material.id] + [
                old for old, target in self._legacy_ids.items() if target == material.id
            ]
            for stored_id in dict.fromkeys(aliases):
                self.table.update(
                    where=f"material_id = '{_id(stored_id)}'"
                    + (" AND " + self._namespace() if self._namespace() else ""),
                    values={"path": destination_relative},
                )
                applied.append(f"index_path:{stored_id}:{destination_relative}")
        except Exception as exc:
            self._reconciled_token = None
            return {
                "status": "partial",
                "applied": applied,
                "error": str(exc),
                "repair": f"Retry pcw_rename({material.id!r}, {requested_path!r}) to complete the index path update; pcw_reconcile can diagnose it",
            }
        self._reconciled_token = None
        return self._result(material)

    def rename(self, material_id: str, path: str):
        with self._guard():
            material = self._material(material_id)
            destination = self._knowledge_path(path)
            if destination.name != material.id:
                raise PCWError(
                    "A move must preserve the MCP-generated filename ID; existing IDs are migrated only by explicit migration"
                )
            if destination == self.root / material.path:
                return self._result(material)
            if destination.exists():
                raise PCWError("Destination already exists")
            return self._move_same_id(material, destination, path)

    def links(
        self,
        source_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ):
        with self._guard():
            material = self._material(source_id)
            additions = self._check_links(add or [], source_id)
            removals = {_id(value) for value in (remove or [])}
            material.metadata["links"] = _unique_links(
                [value for value in material.links if value not in removals]
                + additions,
                source_id,
            )
            self._save(material)
            return self._result(material)

    def links_many(self, changes: list[dict]):
        """Validate all exact-ID navigation changes before the first file write."""
        with self._guard():
            if not isinstance(changes, list) or not changes or len(changes) > 100:
                raise PCWError("changes must contain between 1 and 100 link updates")
            prepared, seen = [], set()
            for change in changes:
                if (
                    not isinstance(change, dict)
                    or "source_id" not in change
                    or set(change) - {"source_id", "add", "remove"}
                ):
                    raise PCWError(
                        "Each link update needs source_id and optional add/remove ID lists"
                    )
                source_id = _id(change["source_id"])
                if source_id in seen:
                    raise PCWError(
                        "Each source_id may appear only once in a link batch"
                    )
                seen.add(source_id)
                material = self._material(source_id)
                additions = self._check_links(change.get("add", []), source_id)
                removals = set(_unique_links(change.get("remove", []), source_id))
                links = _unique_links(
                    [value for value in material.links if value not in removals]
                    + additions,
                    source_id,
                )
                prepared.append((material, links))
            self._check_edit_reservations([m.id for m, links in prepared if m.links != links])
            applied = []
            for material, links in prepared:
                replacement = Material(
                    material.id,
                    material.path,
                    {**material.metadata, "links": links},
                    material.content,
                )
                try:
                    if material.links != links:
                        self._save(replacement)
                        self.materials[material.id] = replacement
                    applied.append(self._result(replacement))
                except Exception as exc:
                    return {
                        "status": "partial",
                        "materials": applied,
                        "failed_material_id": material.id,
                        "error": str(exc),
                        "repair": "Retry the same pcw_links_many changes; completed additions and removals are idempotent.",
                    }
            return {"status": "ok", "materials": applied}

    def _mapping(self, mapping: dict[str, str]):
        for old, target in mapping.items():
            self._material(old)
            self._material(target)
            if old == target or target in mapping:
                raise PCWError(
                    "Replacements must map removed IDs directly to surviving IDs without chains"
                )

    def _rewrite(self, mapping: dict[str, str | None], applied: list[str]):
        self._check_edit_reservations([m.id for m in self.materials.values()
                                       if set(mapping).intersection(m.links)])
        for material in self.materials.values():
            links = _unique_links(
                [
                    mapping.get(value, value)
                    for value in material.links
                    if mapping.get(value, value) is not None
                ],
                material.id,
            )
            if links != material.links:
                material.metadata["links"] = links
                self._save(material)
                applied.append(f"links:{material.id}")

    def reassign(self, mapping: dict[str, str]):
        with self._guard():
            self._mapping(mapping)
            applied = []
            try:
                self._rewrite(mapping, applied)
            except Exception as exc:
                return {"status": "partial", "applied": applied, "error": str(exc)}
            return {"status": "ok", "applied": applied}

    def _delete(self, material: Material, applied: list[str]):
        self._check_edit_reservations([material.id])
        self._delete_rows(material.id)
        applied.append(f"index_removed:{material.id}")
        (self.root / material.path).unlink()
        applied.append(f"deleted:{material.id}")
        self.materials.pop(material.id)
        self.pending.discard(material.id)

    def delete(self, material_id: str):
        with self._guard():
            material = self._material(material_id)
            self._check_edit_reservations([material.id] + [m.id for m in self.materials.values() if material.id in m.links])
            applied = []
            try:
                self._rewrite({material_id: None}, applied)
                self._delete(material, applied)
            except Exception as exc:
                return {"status": "partial", "applied": applied, "error": str(exc)}
            return {"status": "ok", "applied": applied}

    def merge(self, materials: list[dict], replacements: dict[str, str]):
        """Apply agent-written survivors and exact old-ID to target-ID mappings."""
        with self._guard():
            self._mapping(replacements)
            self._check_edit_reservations(list(set(replacements) | set(replacements.values()) | {m.id for m in self.materials.values() if set(replacements).intersection(m.links)}))
            targets = {item["id"] for item in materials}
            if len(targets) != len(materials) or targets != set(replacements.values()):
                raise PCWError(
                    "Supply exactly one final content entry for each replacement target"
                )
            plans = []
            for item in materials:
                target = self._material(item["id"])
                if not isinstance(item.get("content"), str):
                    raise PCWError(
                        "Each merge target requires final content supplied by the main agent"
                    )
                outgoing = item.get("links")
                if outgoing is None:
                    outgoing = target.links + [
                        link
                        for old, dest in replacements.items()
                        if dest == target.id
                        for link in self._material(old).links
                    ]
                outgoing = self._check_links(outgoing, target.id)
                outgoing = _unique_links(
                    [replacements.get(link, link) for link in outgoing], target.id
                )
                plans.append((target, item["content"], outgoing))
            applied = []
            try:
                for target, content, outgoing in plans:
                    if target.content != content:
                        self._delete_rows(target.id)
                        applied.append(f"index_removed:{target.id}")
                        target.set_content(content)
                        if content.strip():
                            self.pending.add(target.id)
                        else:
                            self.pending.discard(target.id)
                    target.metadata["links"] = outgoing
                    self._save(target)
                    applied.append(f"saved:{target.id}")
                # Every incoming rewrite must finish before any old file is removed.
                self._rewrite(replacements, applied)
                for old in replacements:
                    self._delete(self._material(old), applied)
                results = [self._index_receipt(target) for target, _, _ in plans]
            except Exception as exc:
                return {
                    "status": "partial",
                    "applied": applied,
                    "error": str(exc),
                    "pending_preparation": sorted(self.pending)[:50],
                    "pending_count": len(self.pending),
                }
            return {
                "status": "partial"
                if any(x["index_status"] != "indexed" for x in results)
                else "ok",
                "applied": applied,
                "materials": results,
                "pending_preparation": sorted(self.pending)[:50],
                "pending_count": len(self.pending),
            }

    def _get_provider(self):
        config = self.active["config"]
        if self._provider is None or self._provider_config != config:
            if self._provider is not None:
                self._provider.close()
            self._provider = self._factory(config)
            self._provider_config = config
        return self._provider

    def _get_chunker(self):
        key = json.dumps(
            [self.active["chunking"], self.active["config"]], sort_keys=True
        )
        if self._chunker is None or self._chunker_key != key:
            self._chunker = TokenChunker(
                self.active["chunking"], self.active["config"], self._get_provider()
            )
            self._chunker_key = key
        return self._chunker

    def _index_receipt(self, material):
        if material.id not in self.pending:
            return self._result(material)
        try:
            return self._index_material(material)
        except Exception as exc:
            # Saved text is durable even if optional dependencies or API fail.
            error = (
                str(exc)
                if isinstance(exc, (PCWError, EmbeddingError))
                else "Indexing failed (" + type(exc).__name__ + "); saved text retained"
            )
            observed = self.materials.get(material.id, material)
            return {
                **self._result(observed),
                "index_status": "pending_preparation",
                "index_error": error,
                "next_action": "reconcile",
            }

    def _build_rows(self, material, chunker, provider, config, chunk_id):
        from .source import WorkspaceDocuments

        document = WorkspaceDocuments(self).document(material)
        try:
            rows = self.index.rows(document, chunker, provider, config, chunk_id)
            if getattr(self, "table", None) is not None:
                columns = set(self.table.schema.names)
                rows = [{k: v for k, v in row.items() if k in columns} for row in rows]
            return rows
        except ValueError as exc:
            raise PCWError(str(exc)) from exc

    def _check_saved_revision(self, material, revision):
        _, saved = _parse((self.root / material.path).read_bytes().decode("utf-8"))
        if _revision(saved) != revision:
            self._scan()
            self._reconcile()
            raise PCWError(
                "Material changed during embedding; index must be retried for the current revision"
            )

    def _index_material(self, material):
        revision = material.revision
        active = copy.deepcopy(self.active)
        rows = self._build_rows(
            material,
            self._get_chunker(),
            self._get_provider(),
            active["config"],
            self._chunking_id(),
        )
        self._check_saved_revision(material, revision)
        if json.loads(read_utf8(self.state / "active.json")) != active:
            raise PCWError(
                "Index settings changed during embedding; retry using current settings"
            )
        self._delete_rows(material.id)
        try:
            if rows:
                self.table.add(rows)
        except Exception:
            self._delete_rows(material.id)
            self.pending.add(material.id)
            raise
        self.pending.discard(material.id)
        self._reconciled_token = None
        return {**self._result(material), "parts": len(rows)}

    @staticmethod
    def _vectors(provider, parts: list[str], dimensions: int, purpose: str):
        from contextopus.core.rag import VectorIndex

        try:
            return VectorIndex.vectors(provider, parts, dimensions, purpose)
        except ValueError as exc:
            raise PCWError(str(exc)) from exc

    def search(
        self,
        query: str,
        N: int | None = None,
        d_max: float | None = None,
        min_jump: float | None = None,
        multiplier: float | None = None,
    ):
        with self._guard():
            options = dict(self.active["search"])
            for key, value in {
                "N": N,
                "d_max": d_max,
                "min_jump": min_jump,
                "multiplier": multiplier,
            }.items():
                if value is not None:
                    options[key] = value
            config = SearchConfig(**options)
            results = []
            needs_correction = self._needs_correction()
            if self.table.count_rows():
                vector = self._vectors(
                    self._get_provider(),
                    [query],
                    self.active["config"]["profile"]["dimensions"],
                    "query",
                )[0]
                # No fast_search: LanceDB includes fresh rows outside an ANN index.
                predicate = self._namespace()
                excluded = set(needs_correction)
                excluded.update(self.pending)
                excluded.update(
                    row["material_id"]
                    for row in self._rows(["material_id"])
                    if self._legacy_ids.get(row["material_id"], row["material_id"])
                    not in self.materials
                )
                excluded.update(
                    old
                    for old, target in self._legacy_ids.items()
                    if target in excluded
                )
                if excluded:
                    excluded_predicate = (
                        "material_id NOT IN ("
                        + ",".join("'" + _id(ident) + "'" for ident in sorted(excluded))
                        + ")"
                    )
                    predicate = (
                        predicate + " AND " if predicate else ""
                    ) + excluded_predicate
                rows = self.index.nearest(
                    self.table,
                    vector,
                    predicate=predicate,
                    columns=["material_id", "path"],
                    limit=config.N,
                    metric=config.metric,
                )
                canonical = []
                for row in rows:
                    ident = self._legacy_ids.get(row["material_id"], row["material_id"])
                    material = self.materials.get(ident)
                    if material is not None and ident not in excluded:
                        canonical.append(
                            {
                                **self._label(ident),
                                "path": material.path,
                                "distance": row["_distance"],
                            }
                        )
                results = select_candidates(canonical, config)
            return {
                "results": results,
                "pending_preparation": sorted(self.pending)[:50],
                "pending_count": len(self.pending),
                "unregistered": self.unregistered[:50],
                "needs_correction": needs_correction[:50],
                "needs_correction_count": len(needs_correction),
                "unregistered_count": len(self.unregistered),
                "search": dataclasses.asdict(config),
                "search_calibration": self.active["search_calibration"],
            }

    def reconcile(self):
        with self._guard():
            results = [
                self._index_receipt(self._material(ident))
                for ident in sorted(self.pending)
            ]
            return {
                "status": "partial" if self.pending else "ok",
                "materials": results,
                "pending_preparation": sorted(self.pending)[:50],
                "pending_count": len(self.pending),
                "unregistered": self.unregistered[:50],
                "unregistered_count": len(self.unregistered),
            }

    def switch_embeddings(
        self, config: dict, search: dict, chunking: dict | None = None
    ):
        with self._guard():
            namespace = self._namespace()
            # Algorithm cards are derivative data. The explicit model-apply
            # boundary refreshes them after publishing the rebuilt source index.
            derivative = "(module = 'core' AND collection IN ('search_algorithm', 'instructions'))"
            if namespace and self.table.count_rows(f"NOT ({namespace}) AND (module IS NULL OR collection IS NULL OR NOT {derivative})"):
                raise PCWError(
                    "Profile switch requires source adapters for every indexed module; non-Workspace documents are present"
                )
            config = validate_config(config)
            chunking = validate_chunking(
                chunking if chunking is not None else self.active.get("chunking"),
                config,
            )
            search_config = dataclasses.asdict(SearchConfig(**search))
            from contextopus.core.rag import rebuild_profile
            from .source import WorkspaceDocuments

            source = WorkspaceDocuments(self)
            signature = source.signature()
            from contextopus.core.documents.instructions import InstructionLibrary
            instruction_library = InstructionLibrary(self.layout)
            instruction_snapshot = instruction_library.snapshot()
            if (namespace and self.table.count_rows("module = 'core' AND collection = 'instructions'")
                    and not instruction_library.root.is_dir()):
                raise PCWError("Instruction source directory is missing; profile switch refused")
            if not instruction_snapshot.complete or instruction_snapshot.errors:
                raise PCWError("Instruction source is incomplete; profile switch refused")
            def validate_sources():
                source.validate(signature)
                if instruction_library.snapshot() != instruction_snapshot:
                    raise PCWError("Instructions changed during profile switch; retry from a fresh snapshot")
            result = rebuild_profile(
                self.index,
                previous=self.active,
                config=config,
                search=search_config,
                chunking=chunking,
                documents=[*source.documents(), *instruction_snapshot.documents],
                validate_snapshot=validate_sources,
                read_active=lambda: json.loads(read_utf8(self.state / "active.json")),
                write_active=self._write_active,
                factory=self._factory,
                schema=self._schema(config["profile"]["dimensions"]),
            )
            if "_provider" not in result:
                return result
            old_provider = self._provider
            self._provider = result.pop("_provider")
            self._provider_config = config
            self.active = result["active"]
            self.table = self.index.open_table(self.active["table"])
            self._chunker = self._chunker_key = None
            self._reconciled_token = None
            self._reconcile()
            if old_provider is not None and old_provider is not self._provider:
                old_provider.close()
            return result

    def _git_paths(self, paths: list[str]):
        if not paths:
            raise PCWError("Specify exact memory files for the Git operation")
        for relative in paths:
            if relative in {"INSTRUCTIONS.md", "Context.md"}:
                self._assert_safe(self.root / relative)
            elif relative.startswith("knowledge/"):
                self._knowledge_path(relative[len("knowledge/") :])
            else:
                raise PCWError(
                    "Git scope allows only INSTRUCTIONS.md and exact knowledge/*.md paths"
                )
        return list(dict.fromkeys(paths))

    def _git(self, *args, check=True):
        self._assert_safe(self.root / ".git")
        from contextopus.core.history import GitRepository

        try:
            return GitRepository(self.root).command(*args, check=check)
        except ValueError as exc:
            raise PCWError(str(exc)) from exc

    def _own_git(self):
        if not (self.root / ".git").is_dir():
            raise PCWError(
                "Workspace has no separate Git repository; create an explicit checkpoint first"
            )
        if (
            Path(self._git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
            != self.root
        ):
            raise PCWError("Git repository is not the selected memory workspace")

    def checkpoint(self, paths: list[str], message: str):
        with self._guard():
            paths = self._git_paths(paths)
            applied = []
            try:
                if not (self.root / ".git").exists():
                    self._git("-c", "init.templateDir=", "init", ".")
                    applied.append("initialized memory Git repository")
                self._own_git()
                exclude = self.root / ".git" / "info" / "exclude"
                self._assert_safe(exclude)
                previous_exclude = (
                    exclude.read_text(encoding="utf-8") if exclude.exists() else ""
                )
                if "/.pcw-runtime/" not in previous_exclude.splitlines():
                    self._write(
                        exclude, previous_exclude.rstrip("\n") + "\n/.pcw-runtime/\n"
                    )
                if not self.uncheckpointed(paths):
                    return {
                        "status": "ok",
                        "commit": self._git("rev-parse", "HEAD").stdout.strip(),
                        "paths": paths,
                        "already_checkpointed": True,
                    }
                self._git("add", "--", *paths)
                applied.append("staged exact requested paths")
                self._git("commit", "--only", "-m", message, "--", *paths)
                commit = self._git("rev-parse", "HEAD").stdout.strip()
            except Exception as exc:
                return {"status": "partial", "applied": applied, "error": str(exc)}
            return {"status": "ok", "commit": commit, "paths": paths}

    def restore(self, commit: str, paths: list[str]):
        with self._guard():
            paths = self._git_paths(paths)
            self._own_git()
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.~^/-]*", commit
            ) or commit.startswith("-"):
                raise PCWError("Invalid Git revision")
            revision = self._git(
                "rev-parse", "--verify", commit + "^{commit}"
            ).stdout.strip()
            tree = self._git("ls-tree", "-r", revision, "--", *paths).stdout
            for line in tree.splitlines():
                if line.split()[0] not in {"100644", "100755"}:
                    raise PCWError(
                        "Restoring symlinks or nonregular memory files is not allowed"
                    )
            affected = [
                material
                for material in self.materials.values()
                if material.path in paths
            ]
            for material in affected:
                historical = self._git(
                    "show", f"{revision}:{material.path}", check=False
                )
                if historical.returncode:
                    self._delete_rows(material.id)
                    continue
                _, body = _parse(historical.stdout)
                if body != material.content:
                    self._delete_rows(material.id)
            try:
                self._git("restore", "--source", revision, "--worktree", "--", *paths)
                self._scan()
                self._reconcile()
                indexed = self.reconcile()
            except Exception as exc:
                return {
                    "status": "partial",
                    "error": str(exc),
                    "paths": paths,
                    "repair": "Retry pcw_reconcile to automatically index restored text",
                }
            return {
                "status": indexed["status"],
                "paths": paths,
                "materials": indexed["materials"],
                "pending_preparation": sorted(self.pending)[:50],
                "pending_count": len(self.pending),
            }

    def close(self):
        with self._mutex:
            self._chunker = None
            self._chunker_key = None
            if self._provider is not None:
                self._provider.close()
                self._provider = None
                self._provider_config = None
            if getattr(self, "index", None) is not None:
                self.index.table = self.index.db = None
            self.table = self.db = None
