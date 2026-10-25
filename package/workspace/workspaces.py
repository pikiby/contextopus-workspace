"""Explicit workspace creation and versioned, recoverable format migration."""

from __future__ import annotations

from .text_io import read_utf8

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .runtime import PCWError, _id, _revision, _parse
from .policies import policies, extract_settings
from .settings import WorkspaceSettingsMixin, configuration_text, save_configuration
from .configuration import WorkspaceSettings as Settings, WORKSPACE_SETTINGS, WORKSPACE_POLICY_DEFAULTS, WORKSPACE_CREATION_DEFAULTS


FORMAT_VERSION = 6  # Workspace format, deliberately independent of plugin releases.
EMPTY_PROJECT = "# Project\n\nPurpose, goals, requirements and boundaries have not been specified.\n"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class WorkspaceMixin(WorkspaceSettingsMixin):
    def _format_path(self):
        path = self.control.root / "format.json"
        self.control.safe(path)
        return path

    def _existing(self):
        self.engine._assert_safe(self.engine.root)
        if not self.engine.root.is_dir():
            raise PCWError(
                "Workspace does not exist. Select an existing context or explicitly use pcw-create."
            )

    def _format(self):
        self._existing()
        path = self._format_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if type(data.get("format_version")) is not int:
                raise PCWError("Invalid workspace format metadata")
            return data["format_version"]
        # Old storage can predate runtime profiles and instruction files.
        state = getattr(self.engine, "legacy_state", self.engine.state)
        for candidate in (
            self.engine.knowledge,
            state / "active.json",
            self.engine.root / "INSTRUCTIONS.md",
        ):
            self.engine._assert_safe(candidate)
        if self.engine.knowledge.is_dir():
            profile = state / "active.json"
            if profile.is_file():
                active = json.loads(read_utf8(profile))
                if (
                    type(active.get("index_format", 1)) is int
                    and active.get("index_format", 1) in (1, 2)
                    and {"config", "table"} <= active.keys()
                ):
                    return 0
            evidence = any(
                (self.engine.root / name).exists()
                for name in ("current.md", "CURRENT.md", "tmp")
            )
            cards = any(self.engine.knowledge.rglob("*.md"))
            if evidence and cards:
                return 0
        raise PCWError(
            "Unrecognized legacy workspace structure; inspect or restore it before migration"
        )

    def _instruction_initialization(self, content=None):
        from .context_document import read_instructions
        active_rules = read_instructions(self)
        if active_rules is not None:
            return {"required": False, "status": "present"}
        path = self.engine.root / "INSTRUCTIONS.md"
        self.engine._assert_safe(path)
        marker = self.control.root / "instructions-initialized.json"
        if path.is_file():
            if content is not None and read_utf8(path) != content:
                raise PCWError(
                    "Existing instructions must be preserved; use the instructions proposal procedure to edit them"
                )
            return {"required": False, "status": "present"}
        if marker.exists() or self._format_path().exists():
            return {
                "required": True,
                "status": "lost_initialized_rules",
                "blocked": True,
                "reason": "Previously initialized INSTRUCTIONS are missing; restore the actual rules instead of initializing replacements",
            }
        if content is not None:
            if not isinstance(content, str) or not content.strip():
                raise PCWError("Supply complete nonempty instructions")
        return {
            "required": True,
            "status": "never_initialized",
            "blocked": content is None,
            "content": content,
            "revision": _revision(content) if content is not None else None,
            "confirmation_required": True,
            "reason": "Review the complete proposed INSTRUCTIONS and obtain actual human approval before initialization",
        }

    def _require_format(self, *, storage=True):
        version = self._format()
        if version != FORMAT_VERSION:
            raise PCWError(
                f"Workspace format {version}; required {FORMAT_VERSION}. Use pcw-upgrade; opening does not migrate."
            )
        required = ["Context.md"]
        if storage:
            required += ["knowledge"]
        for relative in required:
            path = self.engine.root / relative
            self.engine._assert_safe(path)
            if not path.exists():
                raise PCWError(
                    f"Workspace file missing: {relative}; restore it, do not initialize defaults"
                )
        if storage:
            problems = self._format_problems()
            if problems:
                raise PCWError("pcw-upgrade required: " + "; ".join(problems))

    def workspace_inspect(self):
        """No creation, indexing, embedding load or migration."""
        version = self._format()
        problems = (
            self._format_problems()
            if version == FORMAT_VERSION
            else ["Old workspace format"]
        )
        return {
            "workspace": str(self.engine.root),
            "format_version": version,
            "required_format": FORMAT_VERSION,
            "compatible": not problems,
            "problems": problems,
            "next_action": "pcw-upgrade" if problems else "open",
        }

    def workspace_create(
        self,
        request_id: str,
        instructions_content: str,
        project_content: str,
        user_request: str,
        settings: dict | None = None,
        project_description: str | None = None,
    ):
        _id(request_id)
        if not isinstance(user_request, str) or not user_request.strip():
            raise PCWError(
                "An explicit user request to create this context is required"
            )
        if not isinstance(project_content, str) or not project_content.strip():
            raise PCWError("Supply the project description agreed with the user")
        imported, instruction_text = extract_settings(instructions_content, creation=True)
        supplied_settings = settings
        settings = imported if settings is None else settings
        if not isinstance(settings, dict) or set(settings) - set(WORKSPACE_SETTINGS):
            raise PCWError("Invalid Workspace settings")
        Settings(self.control.layout)._validate_changes(settings)
        if supplied_settings is not None and not (set(WORKSPACE_POLICY_DEFAULTS) - {"cleanup_approval_required"}) <= settings.keys():
            raise PCWError("Creation requires all six Workspace policy settings")
        settings = {"cleanup_approval_required": WORKSPACE_CREATION_DEFAULTS["cleanup_approval_required"], **settings}
        signature_input = [request_id, instructions_content, project_content, user_request]
        if supplied_settings is not None or project_description is not None:
            signature_input += [supplied_settings, project_description]
        signature = _revision(encode(signature_input))
        with self.control.guard():
            from .context_identity import assert_unique_context_names
            assert_unique_context_names(self.control.layout, self.engine.workspace)
            record = self.control.root / "creation.json"
            self.control.safe(record)
            previous = json.loads(read_utf8(record)) if record.exists() else None
            if previous:
                if previous["signature"] != signature:
                    raise PCWError("Workspace already has a different creation request")
                if previous["status"] == "created":
                    self._require_format()
                    return {
                        "status": "created",
                        "workspace": str(self.engine.root),
                        "replayed": True,
                    }
            elif self.engine.root.exists():
                raise PCWError(
                    "Workspace already exists; opening or migration never replaces it"
                )
            else:
                # New requests require the current choices. An identical older
                # accepted request must remain resumable after a plugin update.
                if supplied_settings is None:
                    settings = policies(instructions_content, creation=True)
                from .records import project_record
                project_record(self.engine.workspace, project_content,
                               description=project_description, require_description=True)
                previous = {"signature": signature, "status": "creating"}
                self.control.write(record, encode(previous))
            self.engine._assert_safe(self.engine.knowledge)
            self.engine.knowledge.mkdir(parents=True, exist_ok=True)
            self.engine.state.mkdir(parents=True, exist_ok=True)
            old_configuration = configuration_text(self.control.layout, self.engine.workspace)
            configured = json.loads(old_configuration) if old_configuration else {}
            if any(key in configured and configured[key] != value for key, value in settings.items()):
                raise PCWError("Interrupted creation conflicts with Workspace settings")
            save_configuration(self.control.layout, self.engine.workspace,
                               encode({**configured, **settings}))
            from .records import project_record
            project_id, project_text = project_record(self.engine.workspace, project_content,
                                                      description=project_description)
            from .context_document import ensure_instruction, bind_project, library, instruction_name
            project_text = bind_project(self, project_text)
            references = _parse(project_text)[0]["instructions"]
            default_reference = "[[" + instruction_name(self.engine.workspace).removesuffix(".md") + "]]"
            if references["workspace"] == default_reference:
                ensure_instruction(self, instruction_text)
            else:
                primary = library(self).read(references["workspace"])
                if primary["content"] != instruction_text:
                    raise PCWError("The selected shared Workspace instruction differs from the agreed creation rules")
            library(self).resolve(list(references.values()))
            for relative, content in {
                "Context.md": project_text,
            }.items():
                path = self.engine.root / relative
                self.engine._assert_safe(path)
                if (relative == "INSTRUCTIONS.md" and path.is_file()
                        and path.read_bytes().decode("utf-8") == instructions_content
                        and instruction_text != instructions_content):
                    self.engine._write(path, instruction_text)
                if path.exists() and path.read_bytes().decode("utf-8") != content:
                    raise PCWError(
                        f"Interrupted creation conflicts with {relative}; inspect before continuing"
                    )
                if not path.exists():
                    self.engine._write(path, content)
            with self.engine._guard():
                self._instructions(initialize=True)
                index = self.engine.reconcile()
                if index["pending_count"]:
                    return {"status": "index_pending", "index": index, "request_id": request_id}
            self.control.write(
                self._format_path(), encode({"format_version": FORMAT_VERSION})
            )
            self._require_format()
            history = self._history_record("initial-" + _revision(request_id))
            previous["status"] = "created"
            self.control.write(record, encode(previous))
            return {
                "status": "created",
                "workspace": str(self.engine.root),
                "format_version": FORMAT_VERSION,
                "settings": settings,
                "instructions": self._instructions(),
                "project": self.project_read(),
                "history": history,
            }

    def _source_files(self):
        """Bind review to permanent sources AND temporary work, excluding derived files."""
        result = {}
        for path in sorted(self.engine.root.rglob("*")):
            relative = path.relative_to(self.engine.root)
            if relative.parts[0] in {".git", ".pcw-runtime"}:
                continue
            self.engine._assert_safe(path)
            if path.is_file():
                result[relative.as_posix()] = file_hash(path)
        active = self.engine.state / "active.json"
        self.engine._assert_safe(active)
        if active.is_file():
            result[".pcw-runtime/active.json"] = _revision(read_utf8(active))
        return result

    def _migration_tree(self, root, *, control=False):
        """Binary-safe snapshot manifest; never recurse into previous backups."""
        result = {}
        check = self.control.safe if control else self.engine._assert_safe
        check(root)
        for directory, dirs, names in os.walk(root):
            if control and Path(directory) == root:
                dirs[:] = [name for name in dirs if name not in {"backups", ".backups"}]
                names = [
                    name for name in names if name not in {"lock", "migration.json"}
                ]
            for name in sorted(dirs + names):
                path = Path(directory) / name
                relative = path.relative_to(root)
                check(path)
                if path.is_file():
                    result[relative.as_posix()] = file_hash(path)
                elif path.is_dir():
                    result[relative.as_posix() + "/"] = None
                else:
                    raise PCWError(f"Unsupported migration source: {path}")
        return result

    @staticmethod
    def _migration_title(text):
        return next(
            (
                line.lstrip("# ").strip()
                for line in text.splitlines()
                if line.startswith("# ")
            ),
            "",
        )

    def _migration_work(self):
        """Report saved work, not guessed live-agent activity. Never apply or close it."""
        records = self._records()
        if not isinstance(records.get("cycles"), dict) or not isinstance(
            records.get("starts"), dict
        ):
            raise PCWError("Unrecognized cycle registry; inspect it before migration")
        work, blockers = [], []
        for cycle_id, cycle in sorted(records["cycles"].items()):
            _id(cycle_id)
            if cycle.get("state") in {"closed", "cancelled", "replaced"}:
                continue
            item = {
                "cycle_id": cycle_id,
                "state": cycle.get("state"),
                "request_ids": [
                    key for key, value in records["starts"].items() if value == cycle_id
                ],
                "task_id": cycle.get("task_id"),
                "temporary_materials": [],
                "proposals": [],
            }
            work.append(item)
            # Interrupted state transitions require their own recovery, not cancellation.
            if cycle.get("state") != "active":
                blockers.append(
                    {
                        "cycle_id": cycle_id,
                        "reason": "Recover the interrupted or unsupported cycle state: "
                        + str(cycle.get("state")),
                    }
                )
            if cycle.get("purpose", "work") not in {
                "work",
                "instructions",
                "audit",
                "rework",
            }:
                blockers.append(
                    {"cycle_id": cycle_id, "reason": "Unsupported cycle purpose"}
                )
            for path in sorted((self._tmp(cycle_id) / "materials").glob("*.md")):
                self.engine._assert_safe(path)
                item["temporary_materials"].append(
                    {
                        "path": str(path.relative_to(self.engine.root)),
                        "title": self._migration_title(
                            path.read_text(encoding="utf-8")
                        ),
                    }
                )
            for proposal_id, entry in sorted(cycle.get("proposals", {}).items()):
                summary = {
                    "proposal_id": proposal_id,
                    "state": entry.get("state"),
                    "changes": [],
                }
                item["proposals"].append(summary)
                try:
                    if entry.get("state") not in {
                        "prepared",
                        "applied",
                        "discarded",
                        "no_changes",
                        "applying",
                        "partial",
                        "index_pending",
                    }:
                        raise PCWError("Unsupported proposal state")
                    plan = self._load_plan(cycle_id, proposal_id, entry)
                    if (
                        plan.get("cycle_id") != cycle_id
                        or plan.get("proposal_id") != proposal_id
                        or plan.get("kind", "knowledge")
                        not in {"knowledge", "instructions", "project"}
                    ):
                        raise PCWError("Unsupported proposal identity or kind")
                    for material_id, change in plan["entries"].items():
                        before, after = change["before"], change["after"]
                        for snapshot in (before, after):
                            if (
                                snapshot is not None
                                and _revision(snapshot["text"])
                                != snapshot["fingerprint"]
                            ):
                                raise PCWError("Proposal snapshot fingerprint mismatch")
                        snapshot = after or before
                        summary["changes"].append(
                            {
                                "id": material_id,
                                "path": snapshot["path"],
                                "title": self._migration_title(snapshot["text"]),
                                "action": "delete"
                                if after is None
                                else "create"
                                if before is None
                                else "update",
                            }
                        )
                except (PCWError, OSError, ValueError, KeyError, TypeError) as exc:
                    blockers.append(
                        {
                            "cycle_id": cycle_id,
                            "proposal_id": proposal_id,
                            "reason": str(exc),
                        }
                    )
        return work, blockers

    def _project_description_status(self):
        candidates = []
        from .context_document import record_paths
        for path in record_paths(self.engine.root):
            self.engine._assert_safe(path)
            metadata, body = _parse(read_utf8(path))
            if metadata.get("type") == "project":
                candidates.append((path, body))
        legacy = self.engine.root / "PROJECT.md"
        self.engine._assert_safe(legacy)
        if not candidates and legacy.is_file():
            candidates = [(legacy, read_utf8(legacy))]
        if not candidates:
            return {"status": "missing", "content_reviewed": False}
        if len(candidates) > 1:
            return {"status": "ambiguous", "content_reviewed": False}
        path, body = candidates[0]
        # Placeholder identity is semantic; retain original bytes for revision checks.
        comparable = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        status = "empty" if not comparable else "placeholder" if comparable == EMPTY_PROJECT.strip() else "present"
        return {"path": path.relative_to(self.engine.root).as_posix(), "status": status, "content_reviewed": False}

    def _migration_copy(self, source, destination, manifest):
        """Resume one immutable snapshot; never overwrite a completed snapshot file."""
        self.control.safe(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for relative, digest in manifest.items():
            target = destination / relative
            self.control.safe(target)
            if digest is None:
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                if not target.is_file() or file_hash(target) != digest:
                    raise PCWError(f"Retained backup changed: {target}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=".snapshot-", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    shutil.copy2(source / relative, temporary)
                    if file_hash(temporary) != digest:
                        raise PCWError(
                            f"Source changed while taking backup: {source / relative}"
                        )
                    # Keep the original writable descriptor: Windows cannot
                    # FlushFileBuffers through a reopened read-only handle.
                    # copy2 may retain read-only metadata, so do not reopen it.
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def _relocation_alias(self, path):
        from contextopus.core.relocation import checked_alias

        return checked_alias(self.control.layout, Path(path))

    def _migration_verify_backup(self, operation):
        for field, manifest_key in (
            ("backup", "workspace_snapshot"),
            ("control_backup", "control_sources"),
        ):
            from contextopus.core.relocation import checked_alias

            root = checked_alias(self.control.layout, Path(operation[field]))
            self.control.safe(root)
            for relative, digest in operation[manifest_key].items():
                path = root / relative
                self.control.safe(path)
                if (
                    not path.is_dir()
                    if digest is None
                    else not path.is_file() or file_hash(path) != digest
                ):
                    raise PCWError(f"Migration backup verification failed: {path}")
