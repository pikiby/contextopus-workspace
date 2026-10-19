"""Recoverable file-and-index migration. Approval records remain immutable."""

from .text_io import read_utf8

import copy
import json
import os
from pathlib import Path
import uuid

import lancedb
import yaml

from .runtime import PCWError, Material, _parse, _id, _revision
from .sources import migrate_sources, needs_source_upgrade, declarations
from .shared import topic_name
from .workspaces import EMPTY_PROJECT, FORMAT_VERSION, encode, file_hash
from .records import project_record, task_record, document_id, validate_metadata
from .policies import extract_settings, policies
from .settings import configuration_text, save_configuration
from .configuration import WORKSPACE_POLICY_DEFAULTS
from .migration_identity import MigrationIdentity
from .context_identity import context_metadata, assert_unique_context_names
from .temporary_migration import (
    temporary_transforms, permanent_temporary_blockers, permanent_temporary_references,
    dependency_transforms, retarget_temporary, transform_content, write_transform,
)


class FormatMigrationMixin:
    def _format_problems(self, *, include_legacy_links=False):
        problems = []
        text = configuration_text(self.control.layout, self.engine.workspace)
        if text is None or not set(WORKSPACE_POLICY_DEFAULTS) <= json.loads(text).keys():
            problems.append("Workspace settings require migration to Core configuration")
        rules = self.engine.root / "INSTRUCTIONS.md"
        if rules.is_file() and extract_settings(read_utf8(rules))[1] != read_utf8(rules):
            problems.append("Legacy machine settings remain in INSTRUCTIONS.md")
        active = self.engine.state / "active.json"
        self.engine._assert_safe(active)
        if not active.is_file():
            problems.append("Shared index is missing")
        elif not (self.engine.state / "lancedb").is_dir():
            problems.append("Shared vector database is missing")
        found_ids = set()
        for path in sorted(self.engine.knowledge.rglob("*.md")):
            if any(
                p.startswith(".") for p in path.relative_to(self.engine.knowledge).parts
            ):
                continue
            self.engine._assert_safe(path)
            metadata, body = _parse(path.read_bytes().decode("utf-8"))
            if "id" not in metadata:
                problems.append(f"Missing YAML ID in {path.name}")
            else:
                _id(metadata["id"])
                if metadata["id"] in found_ids:
                    problems.append(f"Duplicate YAML ID: {metadata['id']}")
                found_ids.add(metadata["id"])
            try:
                validate_metadata(metadata)
            except PCWError as exc:
                problems.append(f"{path.name}: {exc}")
            if needs_source_upgrade(body):
                problems.append(f"Legacy sources in {path.name}")
            if include_legacy_links and any(not isinstance(link, str) or not link.startswith("[[")
                   for link in metadata.get("links", [])):
                problems.append(f"Legacy navigation links in {path.name}")
        if (self.engine.root / "PROJECT.md").exists() or (self.engine.root / "current").exists():
            problems.append("Legacy project/task storage remains")
        if (self.engine.root / "tmp").exists():
            problems.append("Legacy temporary work remains in tmp")
        return problems

    def _migration_receipt_path(self):
        path = self.control.root / "migration-v5.json"
        self.control.safe(path)
        return path

    def _check_previous_migration(self):
        """Continue a 0.11-era interrupted transition only from its verified state."""
        path = self.control.root / "migration.json"
        self.control.safe(path)
        if not path.exists():
            return
        previous = json.loads(read_utf8(path))
        if previous.get("status") == "complete":
            return
        if not {"workspace_snapshot", "control_sources", "sources"} <= previous.keys():
            raise PCWError(
                "Older interrupted migration needs recovery from its retained backup"
            )
        if previous["status"] == "snapshotting":
            if (
                self._migration_tree(self.engine.root) != previous["workspace_snapshot"]
                or self._v2_control() != previous["control_sources"]
            ):
                raise PCWError("Source changed during previous migration snapshot")
            return
        self._migration_verify_backup(previous)
        actual = self._v2_sources()
        expected = {
            k: v
            for k, v in previous["sources"].items()
            if k != ".pcw-runtime/active.json"
        }
        if "PROJECT.md" not in expected and actual.get("PROJECT.md") == _revision(
            EMPTY_PROJECT
        ):
            actual.pop("PROJECT.md")
        if actual != expected:
            raise PCWError(
                "Source changed during previous migration; inspect its backup"
            )
        ignored = {"format.json", "workspace-opened.json"}
        if {k: v for k, v in self._v2_control().items() if k not in ignored} != {
            k: v for k, v in previous["control_sources"].items() if k not in ignored
        }:
            raise PCWError("Control state changed during previous migration")

    def _v2_sources(self):
        result = self._source_files()
        # A shared pointer may change while another context is being migrated.
        result.pop(".pcw-runtime/active.json", None)
        return result

    def _v2_control(self):
        return {
            k: v
            for k, v in self._migration_tree(self.control.root, control=True).items()
            if k not in {"migration-v3.json", "migration-v4.json", "migration-v5.json"}
        }

    def _v2_transforms(self, version, identity=None):
        identity = identity or MigrationIdentity(self)
        self._migration_identity = identity
        files, ids, aliases = {}, {}, {}
        if version < 2:
            aliases.update({item['old_id']: item['new_id'] for item in identity.preview()
                            if item['workspace'] == self.engine.workspace})
        indexed_paths = None
        for path in sorted(self.engine.knowledge.rglob("*.md")):
            if any(
                p.startswith(".") for p in path.relative_to(self.engine.knowledge).parts
            ):
                continue
            self.engine._assert_safe(path)
            text = path.read_bytes().decode("utf-8")
            metadata, body = _parse(text)
            relative = path.relative_to(self.engine.root).as_posix()
            if version >= 2 and "id" not in metadata:
                if indexed_paths is None:
                    indexed_paths = {}
                    active_path = self.engine.state / "active.json"
                    if (
                        active_path.is_file()
                        and (self.engine.state / "lancedb").is_dir()
                    ):
                        active = json.loads(read_utf8(active_path))
                        table = lancedb.connect(
                            str(self.engine.state / "lancedb")
                        ).open_table(active["table"])
                        for row in (
                            table.search()
                            .where(f"workspace = '{_id(self.engine.workspace)}'")
                            .select(["material_id", "path"])
                            .limit(None)
                            .to_list()
                        ):
                            indexed_paths.setdefault(row["path"], set()).add(
                                row["material_id"]
                            )
                candidates = indexed_paths.get(relative, set())
                if len(candidates) != 1:
                    raise PCWError(
                        f"Cannot recover an unambiguous YAML ID for {relative}; restore it from history"
                    )
                ident = _id(next(iter(candidates)))
            else:
                ident = _id(metadata["id"] if version >= 2 else path.name)
            mapped = identity.mapping.get((self.engine.workspace, relative)) if version < 2 else None
            if mapped:
                aliases[ident] = mapped["new_id"]
                ident = mapped["new_id"]
            if ident in ids:
                raise PCWError(f"Duplicate material ID: {ident}")
            ids[ident] = relative
            if version < 2 and metadata.get("id"):
                alias = _id(metadata["id"])
                if alias in aliases and aliases[alias] != ident:
                    raise PCWError(f"Duplicate legacy ID: {alias}")
                aliases[alias] = ident
            files[relative] = {
                "id": ident,
                "metadata": metadata,
                "content": body,
                "before": text,
            }
        extra = []
        project_path = self.engine.root / "PROJECT.md"
        if project_path.is_file():
            text = read_utf8(project_path)
            ident, converted = project_record(self.engine.workspace, text)
            extra.append((project_path, ident, text, converted))
        for path in (self.engine.root / "current").rglob("*"):
            self.engine._assert_safe(path)
            if path.is_file() and (path.parent != self.engine.root / "current" or path.suffix != ".md"):
                raise PCWError(f"Unrecognized Current source: {path.name}; classify it before migration")
        for path in sorted((self.engine.root / "current").glob("*.md")):
            self.engine._assert_safe(path)
            text = read_utf8(path)
            metadata, _ = _parse(text)
            ident = _id(metadata.get("id", path.stem))
            extra.append((path, ident, text, task_record(ident, text)))
        # Enumerate physical entries: constructed uppercase/lowercase aliases can
        # both resolve to one file on case-insensitive filesystems.
        for path in sorted(self.engine.root.iterdir()):
            if path.name.casefold() == "current.md" and path.is_file():
                self.engine._assert_safe(path)
                text = read_utf8(path)
                metadata, _ = _parse(text)
                ident = _id(metadata.get("id", document_id(self.engine.workspace, path.name)))
                extra.append((path, ident, text, task_record(ident, text)))
        for path, ident, before, converted in extra:
            if ident in ids:
                raise PCWError(f"Duplicate migrated record ID: {ident}")
            relative = path.relative_to(self.engine.root).as_posix()
            metadata, body = _parse(converted)
            ids[ident] = relative
            files[relative] = {"id": ident, "metadata": metadata, "content": body,
                               "before": before, "converted": converted}
        for ident in ids:
            if ident in identity.owners and identity.owners[ident] != self.engine.workspace:
                raise PCWError(f"Duplicate ID {ident}; restore its actual identity before migration")
        occupied, transforms = set(), {}
        for relative, item in files.items():
            ident = item["id"]
            after_path = relative
            if "converted" in item:
                after_path = "knowledge/" + topic_name(item["content"], ident)
            elif relative not in identity.preserved_paths and (version < 2 or Path(relative).name == ident):
                after_path = (
                    Path(relative).parent / topic_name(item["content"], ident)
                ).as_posix()
            if (after_path != relative and after_path.casefold() == relative.casefold()
                    and after_path not in files):
                destination = self.engine.root / after_path
                self.engine._assert_safe(destination)
                if destination.is_file() and destination.samefile(self.engine.root / relative):
                    # macOS paths compare case-sensitively even when the volume
                    # aliases these names. Keep its actual directory entry so
                    # verification and cleanup never treat one file as two.
                    after_path = relative
            if after_path in occupied or (
                after_path != relative and after_path in files
            ):
                after_path = (
                    Path(after_path).with_suffix("").as_posix()
                    + "-"
                    + ident.removesuffix(".md")
                    + ".md"
                )
            if after_path in occupied or (
                after_path != relative and after_path in files
            ):
                raise PCWError(f"Migration filename collision: {after_path}")
            occupied.add(after_path)
            text = item.get("converted", item["before"])
            transforms[relative] = {
                "id": ident,
                "before": item["before"],
                "path": after_path,
                "text": text,
            }
        old_codec, final_codec = self._migration_link_codecs(transforms)
        for relative, item in transforms.items():
            try:
                item["text"] = self._normalize_snapshot(item["text"], item["id"], aliases,
                                                        old_codec, final_codec)
            except PCWError as exc:
                raise PCWError(f"Cannot migrate {relative}: {exc}") from exc
        return transforms, aliases

    def _migration_link_codecs(self, transforms, snapshots=()):
        identity = getattr(self, "_migration_identity", None) or MigrationIdentity(self)
        original, owners = dict(identity.catalog), dict(identity.owners)
        for row in identity.rows:
            if row["workspace"] == self.engine.workspace and row["path"] in transforms:
                item = transforms[row["path"]]
                original.pop(row["id"], None)
                owners.pop(row["id"], None)
                original[item["id"]] = Material(item["id"], row["path"], row["metadata"], row["body"])
                owners[item["id"]] = self.engine.workspace
                identity.aliases[self.engine.workspace][row["old_id"]] = item["id"]
        final = {}
        final.update(original)
        for relative, item in transforms.items():
            ident = item["id"]
            metadata, body = _parse(item["text"])
            if ident not in original:
                original[ident] = Material(ident, relative, metadata, body)
            final[ident] = Material(ident, item["path"], metadata, body)
            owners[ident] = self.engine.workspace
        for ident, before, after in snapshots:
            if ident in owners and owners[ident] != self.engine.workspace:
                raise PCWError(f"Pending migration snapshot conflicts with foreign ID {ident}")
            for target, snapshot in ((original, before or after), (final, after or before)):
                if snapshot:
                    metadata, body = _parse(snapshot["text"])
                    target[ident] = Material(ident, snapshot["path"], metadata, body)
                    owners[ident] = self.engine.workspace
        return identity.codec(original, owners), identity.codec(final, owners)

    def _normalize_snapshot(self, text, ident, aliases, codec=None, render_codec=None):
        metadata, body = _parse(text)
        metadata = context_metadata(metadata, self.engine.workspace)
        metadata["id"] = ident
        metadata.setdefault("type", "knowledge")
        validate_metadata(metadata)
        links = metadata.get("links", [])
        if not isinstance(links, list):
            raise PCWError("links must be a list")
        display_aliases = codec.display_aliases(links, ident) if codec else {}
        # The migration codec already resolves local aliases and exact foreign
        # paths to canonical IDs. Applying local aliases again could redirect a
        # foreign canonical ID which happens to equal a local legacy filename.
        resolved = (codec.decode(links, ident, self.engine.workspace) if codec else
                    [aliases.get(_id(i), i) for i in links])
        metadata["links"] = list(dict.fromkeys(i for i in resolved if i != ident))
        if render_codec:
            return render_codec.record_text(metadata, migrate_sources(body), aliases=display_aliases)
        return (
            "---\n"
            + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
            + "---\n"
            + migrate_sources(body)
        )

    def _proposal_overlays(self, transforms, aliases, instruction_transition=None, *, preserve_existing_blockers=False):
        result = {}
        mapped = {i["id"]: i["path"] for i in transforms.values()}
        for cycle_id, cycle in self._records()["cycles"].items():
            for proposal_id, entry in cycle.get("proposals", {}).items():
                if entry["state"] not in {
                    "prepared",
                    "applying",
                    "partial",
                    "index_pending",
                }:
                    continue
                plan = self._load_plan(cycle_id, proposal_id, entry)
                retained = self._retained_proposal_blocker(plan) if preserve_existing_blockers else None
                if retained:
                    result[cycle_id + "/" + proposal_id] = retained
                    continue
                plan = self._execution_plan(plan)
                plan = copy.deepcopy(plan)
                if plan.get("kind") == "instructions":
                    change = plan["entries"]["INSTRUCTIONS.md"]
                    before_settings, before_text = extract_settings(change["before"]["text"])
                    after_settings, after_text = extract_settings(change["after"]["text"])
                    completed_text_write = (entry["state"] != "prepared" and instruction_transition
                                            and instruction_transition["before"] == change["after"]["text"])
                    if before_settings != after_settings and not completed_text_write:
                        result[cycle_id + "/" + proposal_id] = {
                            "revision": entry["revision"],
                            "blocked": "Legacy instructions proposal also changes machine settings. Its original proposal and approval revision are retained. Read settings_read, prepare the settings delta with settings_preview/settings_apply, then use instructions_prepare for the complete free text and show both revisions again.",
                        }
                        continue
                    for key, text in (("before", before_text), ("after", after_text)):
                        change[key]["text"] = text
                        change[key]["fingerprint"] = _revision(text)
                    result[cycle_id + "/" + proposal_id] = {"revision": entry["revision"], "plan": plan}
                    continue
                if (instruction_transition and instruction_transition["before"] is not None
                        and plan.get("instructions_revision") == _revision(instruction_transition["before"])):
                    plan["instructions_revision"] = _revision(instruction_transition["after"])
                if plan.get("kind") == "project":
                    project = transforms.get("PROJECT.md")
                    if project is None:
                        raise PCWError("Pending legacy PROJECT proposal has no migration target")
                    change = plan["entries"].pop("PROJECT.md")
                    for snapshot in change.values():
                        if snapshot:
                            _, snapshot["text"] = project_record(self.engine.workspace, snapshot["text"])
                    plan["entries"][project["id"]] = change
                    plan["kind"] = "knowledge"
                # Only technical identity fields are translated. Original
                # proposal bytes and their approval digest remain untouched.
                entries = {}
                original_entry_ids = set(plan["entries"])
                for ident, change in plan["entries"].items():
                    if change.get("after") and permanent_temporary_references(change["after"]["text"], dependency_transforms(self, temporary_transforms(self)),
                                                                             known_cycles=self._records().get("cycles", {}),
                                                                             source_path=self.engine.root / change["after"]["path"]):
                        raise PCWError(f"Pending proposal {proposal_id} would leave permanent material {ident} dependent on temporary work; revise the dependency before migration")
                    target = aliases.get(ident, ident)
                    if target in entries:
                        raise PCWError("Pending proposal identities become ambiguous during migration")
                    entries[target] = change
                plan["entries"] = entries
                plan["created_ids"] = {key: aliases.get(value, value)
                                       for key, value in plan.get("created_ids", {}).items()}
                if plan.get("task_id"):
                    plan["task_id"] = aliases.get(plan["task_id"], plan["task_id"])
                for transform in plan.get("transformations", []):
                    for key in ("sources", "targets"):
                        if any(value not in original_entry_ids for value in transform[key]):
                            raise PCWError("Pending transformation must reference its reviewed local entries")
                        transform[key] = [aliases.get(value, value) for value in transform[key]]
                if "link_targets" in plan:
                    targets = {}
                    for ident, target in plan["link_targets"].items():
                        workspace = target.get("workspace", self.engine.workspace)
                        if ("workspace" not in target and ident in aliases
                                and ident in self._migration_identity.owners
                                and self._migration_identity.owners[ident] != self.engine.workspace):
                            raise PCWError("Ambiguous pending link target owner; review its exact context and path")
                        final_id = aliases.get(ident, ident) if workspace == self.engine.workspace else ident
                        target["id"] = final_id
                        if final_id in mapped and target.get("workspace", self.engine.workspace) == self.engine.workspace:
                            target["path"] = mapped[final_id]
                        targets[final_id] = target
                    plan["link_targets"] = targets
                original_paths = {item["id"]: path for path, item in transforms.items()}
                original_snapshots = []
                snapshots = []
                for ident, change in plan["entries"].items():
                    before, after = copy.deepcopy(change["before"]), copy.deepcopy(change["after"])
                    original_snapshots.append((ident, copy.deepcopy(after or before), copy.deepcopy(after or before)))
                    for key, snapshot in (("before", before), ("after", after)):
                        if not snapshot:
                            continue
                        if snapshot["path"] == original_paths.get(ident):
                            snapshot["path"] = mapped[ident]
                        elif Path(snapshot["path"]).name == ident:
                            snapshot["path"] = str(Path(snapshot["path"]).parent /
                                                   topic_name(_parse(snapshot["text"])[1], ident))
                    snapshots.append((ident, before, after))
                old_codec, current_codec = self._migration_link_codecs(transforms)
                after_source_codec, _ = self._migration_link_codecs(transforms, original_snapshots)
                _, final_codec = self._migration_link_codecs(transforms, snapshots)
                normalized_paths = {ident: {"before": before, "after": after} for ident, before, after in snapshots}
                for ident, change in plan["entries"].items():
                    for key in ("before", "after"):
                        snapshot = change[key]
                        if snapshot is None:
                            continue
                        source_codec = old_codec if key == "before" else after_source_codec
                        resolved_links = source_codec.decode(
                            _parse(snapshot["text"])[0].get("links", []), ident, self.engine.workspace)
                        if "link_ids" in snapshot:
                            if not isinstance(snapshot["link_ids"], list):
                                raise PCWError("Pending reviewed target IDs must be a list")
                            # Respect IDs already established by exact native
                            # paths. Validate the old graph before adapting its
                            # technical representation, rather than repairing a
                            # changed approval graph implicitly.
                            expected_links = [_id(link) if link in resolved_links else aliases.get(_id(link), link)
                                              for link in snapshot["link_ids"]]
                            if expected_links != resolved_links:
                                raise PCWError("Pending proposal navigation differs from its reviewed target IDs")
                        snapshot["path"] = normalized_paths[ident][key]["path"]
                        snapshot["text"] = self._normalize_snapshot(
                            snapshot["text"], ident, aliases,
                            source_codec,
                            current_codec if key == "before" else final_codec
                        )
                        snapshot["fingerprint"] = _revision(snapshot["text"])
                        if "link_ids" in snapshot:
                            snapshot["link_ids"] = resolved_links
                result[cycle_id + "/" + proposal_id] = {
                    "revision": entry["revision"],
                    "plan": plan,
                }
        return result

    def _retained_proposal_blocker(self, plan):
        """Carry an already-blocked approval through a later technical upgrade."""
        key = plan["cycle_id"] + "/" + plan["proposal_id"]
        revision = _revision(encode(plan))
        for name in ("migration-v5.json", "migration-v4.json", "migration-v3.json", "migration-v2.json"):
            path = self.control.root / name
            self.control.safe(path)
            if not path.is_file():
                continue
            item = json.loads(read_utf8(path)).get("overlays", {}).get(key)
            if item and item.get("revision") == revision:
                return copy.deepcopy(item) if item.get("blocked") else None
        return None

    def _execution_plan(self, plan):
        # Immutable approvals keep their original digest. Technical overlays adapt
        # paths/metadata only; they never apply a proposal during migration.
        for name in ("migration-v5.json", "migration-v4.json", "migration-v3.json", "migration-v2.json"):
            receipt = self.control.root / name
            self.control.safe(receipt)
            if not receipt.exists():
                continue
            operation = json.loads(read_utf8(receipt))
            item = operation.get("overlays", {}).get(plan["cycle_id"] + "/" + plan["proposal_id"])
            if item and item["revision"] == _revision(encode(plan)):
                if item.get("blocked"):
                    raise PCWError(item["blocked"])
                return copy.deepcopy(item["plan"])
            transition = operation.get("instruction_transition")
            if (transition and transition["before"] is not None
                    and plan.get("instructions_revision") == _revision(transition["before"])):
                # Applied/no-change reviews still authorize closing their original
                # cycle after a technical extraction of machine frontmatter.
                adapted = copy.deepcopy(plan)
                adapted["instructions_revision"] = _revision(transition["after"])
                return adapted
        return plan

    def _verify_transition(self, operation, *, ignored_receipts=()):
        if "identity_catalog" in operation:
            from .migration_identity import catalog_fingerprints
            actual = catalog_fingerprints(self)
            expected_catalog = operation["identity_catalog"]
            workspace = self.engine.workspace
            if ({k: v for k, v in actual.items() if k != workspace} !=
                    {k: v for k, v in expected_catalog.items() if k != workspace}):
                raise PCWError("Foreign identity catalog changed during migration; inspect retained backup")
        current = self._v2_sources()
        expected = operation["sources"]
        all_transforms = {**operation["transforms"], **operation.get("temporary_transforms", {})}
        managed = set(all_transforms) | {i["path"] for i in all_transforms.values()}
        managed.add("PROJECT.md")
        transition = operation.get("instruction_transition")
        if transition:
            managed.add("INSTRUCTIONS.md")
            current_rules = self.engine.root / "INSTRUCTIONS.md"
            actual = read_utf8(current_rules) if current_rules.is_file() else None
            if actual not in (transition["before"], transition["after"]):
                raise PCWError("Instructions changed during settings migration")
        configuration = operation.get("configuration_transition")
        if configuration and configuration_text(self.control.layout, self.engine.workspace) not in (
                configuration["before"], configuration["after"]):
            raise PCWError("Core Workspace configuration changed during migration; inspect retained snapshot")
        initialization = operation.get("instruction_initialization", {})
        if initialization.get("required"):
            managed.add("INSTRUCTIONS.md")
            rules = self.engine.root / "INSTRUCTIONS.md"
            if rules.exists() and read_utf8(rules) not in (
                    initialization["content"], (transition or {}).get("after")):
                raise PCWError("Reviewed instructions changed during migration")
        if {k: v for k, v in current.items() if k not in managed} != {
            k: v for k, v in expected.items() if k not in managed
        }:
            raise PCWError("Source changed during migration; inspect retained backup")
        for old_path, item in {**operation["transforms"], **operation.get("temporary_transforms", {})}.items():
            old = self.engine.root / old_path
            new = self.engine.root / item["path"]
            self.engine._assert_safe(old)
            self.engine._assert_safe(new)
            old_text = transform_content(old, item) if old.is_file() else None
            new_text = transform_content(new, item) if new.is_file() else None
            if old == new:
                valid = old_text in (item["before"], item["text"])
            else:
                valid = (
                    old_text in (None, item["before"])
                    and new_text in (None, item["text"])
                    and (old_text is not None or new_text is not None)
                )
            if not valid:
                raise PCWError(f"Material changed during migration: {old_path}")
        ignored = {"format.json", "workspace-opened.json", "migration-v3.json", "migration-v4.json", "migration-v5.json", *ignored_receipts}
        if {k: v for k, v in self._v2_control().items() if k not in ignored and not k.startswith("history/")} != {
            k: v for k, v in operation["control_sources"].items() if k not in ignored and not k.startswith("history/")
        }:
            raise PCWError("Cycle or control state changed during migration")

    def _pending_format_two(self, target_version=2):
        path = self.control.root / f"migration-v{target_version}.json"
        self.control.safe(path)
        operation = json.loads(read_utf8(path)) if path.exists() else None
        if not operation or operation["status"] == "complete":
            return None
        if operation["status"] == "snapshotting":
            control = {k: v for k, v in self._v2_control().items() if k != f"migration-v{target_version}.json"}
            if self._migration_tree(self.engine.root) != operation["workspace_snapshot"] or control != operation["control_sources"]:
                raise PCWError("Source changed during format-2 snapshot")
        else:
            self._migration_verify_backup(operation)
            self._verify_transition(operation, ignored_receipts=(f"migration-v{target_version}.json",))
        return operation

    def _resume_format_two(self, operation, target_version=2):
        """Complete recorded v2 transformations before interpreting any filenames.

        The retained receipt supplies identities and approval overlays. Guessing
        IDs from a half-renamed tree would silently change links and approvals.
        """
        receipt = self.control.root / f"migration-v{target_version}.json"
        if operation["status"] == "snapshotting":
            self._migration_copy(self.engine.root, self._relocation_alias(operation["backup"]), operation["workspace_snapshot"])
            self._migration_copy(self.control.root, self._relocation_alias(operation["control_backup"]), operation["control_sources"])
            self._migration_verify_backup(operation)
            operation["status"] = "applying"
            self.control.write(receipt, encode(operation))
        self._verify_transition(operation, ignored_receipts=(f"migration-v{target_version}.json",))
        from contextopus.core import locking
        configuration = operation.get("configuration_transition")
        if configuration:
            save_configuration(self.control.layout, self.engine.workspace, configuration["after"])
        self.engine.state.mkdir(parents=True, exist_ok=True)
        with (self.engine.state / "lock").open("a") as lock:
            locking.flock(lock, locking.LOCK_EX)
            for old_path, item in {**operation["transforms"], **operation.get("temporary_transforms", {})}.items():
                old, new = self.engine.root / old_path, self.engine.root / item["path"]
                self.engine._assert_safe(old)
                self.engine._assert_safe(new)
                if not new.is_file() or transform_content(new, item) != item["text"]:
                    write_transform(self, new, item)
                if old != new and old.exists():
                    old.unlink()
                    from contextopus.core.storage import sync_directory
                    sync_directory(old.parent)
            initialization = operation.get("instruction_initialization", {})
            if initialization.get("required") and not (self.engine.root / "INSTRUCTIONS.md").exists():
                self.engine._write(self.engine.root / "INSTRUCTIONS.md", initialization["content"])
            instruction = operation.get("instruction_transition")
            if instruction:
                self.engine._write(self.engine.root / "INSTRUCTIONS.md", instruction["after"])
        result = self.engine.reconcile()
        if result["pending_count"]:
            return {"status": "index_pending", "recovering_format": target_version, "index": result}
        self._verify_transition(operation, ignored_receipts=(f"migration-v{target_version}.json",))
        self.control.write(self._format_path(), encode({"format_version": target_version}))
        operation["status"] = "complete"
        self.control.write(receipt, encode(operation))
        return None

    def _settings_transition(self, version, initialization):
        path = self.engine.root / "INSTRUCTIONS.md"
        before_rules = read_utf8(path) if path.is_file() else None
        content = before_rules if before_rules is not None else initialization.get("content")
        if content is None:
            return None, None
        imported, after_rules = extract_settings(content)
        before_configuration = configuration_text(self.control.layout, self.engine.workspace)
        configured = json.loads(before_configuration) if before_configuration else {}
        if version >= 4:
            legacy_keys = set(WORKSPACE_POLICY_DEFAULTS) - {"cleanup_approval_required"}
            if not legacy_keys <= configured.keys():
                raise PCWError("Canonical Workspace settings are missing; restore them instead of resetting permissions")
            imported = {key: configured.get(key, default) for key, default in WORKSPACE_POLICY_DEFAULTS.items()}
        return ({"before": before_configuration, "after": encode({**configured, **imported}),
                 "effective": imported},
                {"before": before_rules, "after": after_rules})

    def upgrade_plan(self, instructions_content: str | None = None):
        with self.control.guard():
            version = self._format()
            if version not in (0, 1, 2, 3, 4, 5):
                raise PCWError("Unsupported future workspace format")
            previous = self._migration_receipt_path()
            operation = json.loads(read_utf8(previous)) if previous.exists() else None
            pending = (
                operation if operation and operation["status"] != "complete" else None
            )
            work, blockers = self._migration_work()
            from .edit_reservations import EditReservations
            editing = EditReservations(self.control).rows()
            if editing:
                blockers.append({"reason": "Active material reservations must be released or expire before migration",
                                 "reservations": editing})
            temporary = {}
            try:
                assert_unique_context_names(self.control.layout)
                temporary = copy.deepcopy(pending.get("temporary_transforms", {})) if pending else temporary_transforms(self)
                blockers.extend(permanent_temporary_blockers(self, temporary))
            except (PCWError, OSError, ValueError, UnicodeError) as exc:
                blockers.append({"reason": str(exc)})
            for path in (self.control.root / "settings").glob("*.json"):
                self.control.safe(path)
                if json.loads(read_utf8(path)).get("state") == "applying":
                    blockers.append({"reason": "Complete the pending settings operation before upgrading",
                                     "next_action": "settings_apply", "preview_revision": path.stem})
            initialization = self._instruction_initialization(instructions_content)
            if initialization.get("blocked"):
                blockers.append(
                    {
                        "reason": initialization["reason"],
                        "required": "instructions_initialization",
                    }
                )
            problems = (
                self._format_problems(include_legacy_links=True) if version == FORMAT_VERSION else ["Old workspace format"]
            )
            for path in sorted(self.engine.knowledge.rglob("*.md")):
                self.engine._assert_safe(path)
                metadata, _ = _parse(read_utf8(path))
                try:
                    if metadata != context_metadata(metadata, self.engine.workspace):
                        problems.append(f"Missing derived context tag in {path.name}")
                except PCWError as exc:
                    blockers.append({"reason": str(exc), "path": str(path)})
            if any(p != t["path"] or t["before"] != t["text"] for p, t in temporary.items()):
                problems.append("Temporary work requires readable YAML card migration")
            if version == FORMAT_VERSION and not problems:
                try:
                    with self.engine.read_guard():
                        if self.engine.pending:
                            problems.append(
                                "Shared index does not match current knowledge"
                            )
                except (PCWError, OSError, ValueError) as exc:
                    problems.append(str(exc))
            state = {
                "from_version": version,
                "to_version": FORMAT_VERSION,
                "sources": self._v2_sources(),
                "control_sources": self._v2_control(),
                "instruction_initialization": initialization,
                "temporary_transforms": temporary,
            }
            try:
                configuration, instruction = self._settings_transition(version, initialization)
                state["configuration_transition"] = configuration
                state["instruction_transition"] = instruction
            except (PCWError, ValueError) as exc:
                blockers.append({"reason": str(exc)})
            transforms, aliases = ({}, {})
            identity = None
            predecessor = None
            predecessor_version = None
            proposal_overlays = pending.get("overlays", {}) if pending else {}
            try:
                if pending:
                    self._verify_transition(pending)
                    self._migration_verify_backup(pending) if pending[
                        "status"
                    ] != "snapshotting" else None
                else:
                    predecessor = self._pending_format_two(4)
                    predecessor_version = 4 if predecessor else 3
                    predecessor = predecessor or self._pending_format_two(3)
                    if not predecessor:
                        predecessor_version = 2
                        predecessor = self._pending_format_two()
                    if operation is None and not predecessor:
                        self._check_previous_migration()
                    if not predecessor:
                        identity = MigrationIdentity(self)
                        transforms, aliases = self._v2_transforms(version, identity)
                        retarget_temporary(temporary, transforms, aliases, identity.catalog)
                        proposal_overlays = self._proposal_overlays(
                            transforms, aliases, state.get("instruction_transition"),
                            preserve_existing_blockers=version >= 4)
            except (PCWError, OSError, ValueError) as exc:
                blockers.append({"reason": str(exc)})
            if identity:
                state.update(identity.binding())
            return {
                **state,
                "revision": _revision(encode(state)),
                "workspace": str(self.engine.root),
                "compatible": not problems and not pending,
                "ready": not blockers,
                "problems": problems,
                "pending_work": work,
                "preserved_proposal_blockers": [{"proposal": key, "revision": item["revision"], "reason": item["blocked"]}
                                                for key, item in proposal_overlays.items() if item.get("blocked")],
                "blockers": blockers,
                "resume_revision": pending["revision"] if pending else None,
                "recover_format_two_first": predecessor["revision"] if predecessor else None,
                "recover_format_first": predecessor_version if predecessor else None,
                "backup": pending["backup"] if pending else None,
                "changes": []
                if not problems and not pending
                else [
                    "Preserve source/control snapshots",
                    "Migrate YAML IDs, descriptive paths and sources",
                    "Reconcile and verify the shared category index",
                    "Move Current and PROJECT into typed records; record format 5",
                    "Retain pending work and original approvals while moving tmp to .tmp with readable YAML cards",
                    "Tag permanent records with their context and reject ambiguous context names",
                    "Move legacy machine settings into Core configuration and preserve free instruction text",
                ],
                "renames": [
                    {"id": t["id"], "before": p, "after": t["path"]}
                    for p, t in transforms.items()
                    if p != t["path"]
                ],
                "temporary_renames": [{"before": p, "after": t["path"]} for p, t in temporary.items() if p != t["path"]],
                "temporary_source_diagnostics": [{"path": p, **t["source_diagnostic"]}
                                                 for p, t in temporary.items() if t.get("source_diagnostic")],
                "temporary_handles": [{"id": _parse(t["text"])[0]["id"],
                                       "before_path": p, "after_path": t["path"],
                                       "before_revision": _revision(t["before"]),
                                       "after_revision": _revision(t["text"]),
                                       "reread_required": t["before"] != t["text"]}
                                      for p, t in temporary.items()
                                      if Path(p).parent.name == "materials" and Path(p).suffix.casefold() == ".md"],
                "instructions_preserved": True,
                "pending_work_preserved": True,
                "project_description": self._project_description_status(),
                "next_action": "inspect_blockers"
                if blockers
                else "upgrade_apply"
                if problems or pending
                else "none",
            }

    def upgrade_apply(
        self,
        expected_revision: str,
        user_request: str = "",
        automatic: bool = False,
        instructions_content: str | None = None,
        instructions_confirmation: str | None = None,
    ):
        if not automatic and (
            not isinstance(user_request, str) or not user_request.strip()
        ):
            raise PCWError(
                "Explicit migration request or automatic format-upgrade procedure required"
            )
        with self.control.guard():
            from .edit_reservations import EditReservations
            EditReservations(self.control).check_write()
            receipt = self._migration_receipt_path()
            operation = json.loads(read_utf8(receipt)) if receipt.exists() else None
            if (
                operation
                and operation["revision"] == expected_revision
                and operation["status"] == "complete"
            ):
                self._require_format()
                return {
                    "status": "complete",
                    "format_version": FORMAT_VERSION,
                    "replayed": True,
                    "backup": operation["backup"],
                    "control_backup": operation["control_backup"],
                    "pending_work_preserved": True,
                    "project_description": self._project_description_status(),
                }
            if operation and operation["status"] != "complete":
                if operation["revision"] != expected_revision:
                    raise PCWError("Resume the pending migration revision")
            else:
                plan = self.upgrade_plan(instructions_content)
                if plan["revision"] != expected_revision:
                    raise PCWError(
                        "Workspace changed since migration review; inspect a fresh plan"
                    )
                if not plan["ready"]:
                    raise PCWError(
                        "Migration preflight blocked: " + encode(plan["blockers"])
                    )
                if plan.get("recover_format_two_first"):
                    target = plan.get("recover_format_first", 2)
                    recovered = self._resume_format_two(self._pending_format_two(target), target)
                    if recovered:
                        return recovered
                    fresh = self.upgrade_plan(instructions_content)
                    return self.upgrade_apply(fresh["revision"], user_request, automatic,
                                              instructions_content, instructions_confirmation)
                if not plan["changes"]:
                    return {
                        "status": "current",
                        "format_version": FORMAT_VERSION,
                        "project_description": plan["project_description"],
                        "pending_work": plan["pending_work"],
                        "blockers": plan["blockers"],
                    }
                initialization = plan["instruction_initialization"]
                if initialization.get("required") and (
                    not isinstance(instructions_confirmation, str)
                    or not instructions_confirmation.strip()
                ):
                    raise PCWError(
                        "Actual human confirmation of the reviewed complete instructions is required, including automatic upgrades"
                    )
                transforms, aliases = self._v2_transforms(plan["from_version"])
                binding = self._migration_identity.binding()
                if any(plan.get(field) != value for field, value in binding.items()):
                    raise PCWError("Identity catalog changed since migration review; inspect a fresh plan")
                backup = self.control.root / ".backups" / uuid.uuid4().hex
                operation = {
                    "revision": expected_revision,
                    "status": "snapshotting",
                    "sources": plan["sources"],
                    "control_sources": plan["control_sources"],
                    "workspace_snapshot": self._migration_tree(self.engine.root),
                    "backup": str(backup / "workspace"),
                    "control_backup": str(backup / "control"),
                    "transforms": transforms,
                    "temporary_transforms": plan["temporary_transforms"],
                    "overlays": self._proposal_overlays(transforms, aliases, plan.get("instruction_transition"),
                                                         preserve_existing_blockers=plan["from_version"] >= 4),
                    "pending_work": plan["pending_work"],
                    "instruction_initialization": initialization,
                    "configuration_transition": plan.get("configuration_transition"),
                    "instruction_transition": plan.get("instruction_transition"),
                    "instructions_confirmation": instructions_confirmation
                    if initialization.get("required")
                    else None,
                }
                for field in ("identity_map", "identity_catalog", "identity_map_scope"):
                    if field in plan:
                        operation[field] = plan[field]
                self.control.write(receipt, encode(operation))
            assert_unique_context_names(self.control.layout)
            if operation["status"] == "snapshotting":
                if (
                    self._migration_tree(self.engine.root)
                    != operation["workspace_snapshot"]
                    or self._v2_control() != operation["control_sources"]
                ):
                    raise PCWError("Source changed during snapshot")
                self._migration_copy(
                    self.engine.root,
                    self._relocation_alias(operation["backup"]),
                    operation["workspace_snapshot"],
                )
                self._migration_copy(
                    self.control.root,
                    self._relocation_alias(operation["control_backup"]),
                    operation["control_sources"],
                )
                self._migration_verify_backup(operation)
                operation["status"] = "applying"
                self.control.write(receipt, encode(operation))
            self._migration_verify_backup(operation)
            dependencies = permanent_temporary_blockers(self, operation.get("temporary_transforms", {}))
            if dependencies:
                raise PCWError("Migration dependency preflight blocked; original snapshots are retained: " + encode(dependencies))
            self._verify_transition(operation)
            if not operation.get("history_baseline_saved"):
                old_git = (operation.get("configuration_transition") or {}).get("effective", {}).get("git_history_enabled", False)
                if old_git:
                    operation["history_baseline"] = self._history_record("pre-migration-" + expected_revision, force=True)
                operation["history_baseline_saved"] = True
                self.control.write(receipt, encode(operation))
            configuration = operation.get("configuration_transition")
            if configuration:
                save_configuration(self.control.layout, self.engine.workspace, configuration["after"])
            # Use the same root-wide lock as all index writers during the file
            # transition, before exposing the new category in the shared table.
            self.engine._assert_safe(self.engine.state)
            self.engine.state.mkdir(parents=True, exist_ok=True)
            from contextopus.core import locking as fcntl

            with (self.engine.state / "lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for old_path, item in {**operation["transforms"], **operation.get("temporary_transforms", {})}.items():
                    old, new = (
                        self.engine.root / old_path,
                        self.engine.root / item["path"],
                    )
                    if (
                        not new.exists()
                        or transform_content(new, item) != item["text"]
                    ):
                        write_transform(self, new, item)
                    if old != new and old.exists():
                        old.unlink()
                        from contextopus.core.storage import sync_directory

                        sync_directory(old.parent)
                initialization = operation.get("instruction_initialization", {})
                if initialization.get("required"):
                    rules = self.engine.root / "INSTRUCTIONS.md"
                    if not rules.exists():
                        self.engine._write(rules, initialization["content"])
                instruction = operation.get("instruction_transition")
                if instruction:
                    self.engine._write(self.engine.root / "INSTRUCTIONS.md", instruction["after"])
                legacy_tmp = self.engine.root / "tmp"
                if legacy_tmp.is_dir():
                    for directory in sorted((p for p in legacy_tmp.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                        if not any(directory.iterdir()):
                            directory.rmdir()
                    if not any(legacy_tmp.iterdir()):
                        legacy_tmp.rmdir()
                current = self.engine.root / "current"
                if current.is_dir() and not any(current.iterdir()):
                    current.rmdir()
                fcntl.flock(lock, fcntl.LOCK_UN)
            with self.engine._guard():
                result = self.engine.reconcile()
                if result["pending_count"]:
                    operation["status"] = "index_pending"
                    self.control.write(receipt, encode(operation))
                    return {
                        "status": "index_pending",
                        "resume_revision": expected_revision,
                        "backup": operation["backup"],
                        "index": result,
                    }
            self._verify_transition(operation)
            self.control.write(self._format_path(), encode({"format_version": FORMAT_VERSION}))
            self._require_format()
            if operation.get("instruction_initialization", {}).get("required"):
                self.control.write(
                    self.control.root / "instructions-initialized.json",
                    encode(
                        {"initialized": True, "migration_revision": expected_revision}
                    ),
                )
            history = self._history_record("migration-" + expected_revision)
            operation["status"] = "complete"
            operation["pending_work_preserved"] = True
            self.control.write(receipt, encode(operation))
            return {
                "status": "complete",
                "format_version": FORMAT_VERSION,
                "backup": operation["backup"],
                "control_backup": operation["control_backup"],
                "pending_work_preserved": True,
                "project_description": self._project_description_status(),
                "history": history,
            }
