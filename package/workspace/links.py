"""Native Obsidian paths on disk, stable YAML identities in the record engine.

This is a representation codec, not a second graph or an identity registry.
Migration can use one codec to resolve old paths and another to render new paths.
"""

from __future__ import annotations

import copy
import posixpath
from pathlib import Path, PurePosixPath

import yaml

from .runtime import PCWError, _id, _parse
from .layout import WorkspaceLayout as StorageLayout


class LinkCodec:
    def __init__(self, work_root, catalog, owners):
        self.work_root = Path(work_root).resolve()
        self.catalog, self.owners = catalog, owners
        # An explicitly selected root that is itself a vault defines its own
        # boundary, even when another vault contains that directory.
        candidates = ([self.work_root] if (self.work_root / ".obsidian").is_dir()
                      else [p for p in self.work_root.parents if (p / ".obsidian").is_dir()])
        if len(candidates) > 1:
            raise PCWError("Several enclosing Obsidian vaults exist; select one unambiguous vault root")
        self.vault_root = candidates[0] if candidates else self.work_root
        base = StorageLayout(self.work_root).workspace_base
        self.paths = {}
        self.by_path = {}
        for ident, material in catalog.items():
            path = (base / owners[ident] / material.path).relative_to(self.vault_root).as_posix()
            self.paths[ident] = path
            self.by_path.setdefault(path, []).append(ident)

    def decode(self, values, own_id, source_workspace=None, source_path=None):
        if not isinstance(values, list):
            raise PCWError("links must be a list of IDs or quoted wikilinks")
        result = []
        for value in values:
            if not isinstance(value, str):
                raise PCWError("links must contain IDs or quoted wikilinks")
            if not value.startswith("[["):
                target = _id(value)
            else:
                if not value.endswith("]]"):
                    raise PCWError(f"Invalid wikilink: {value}")
                path = value[2:-2].split("|", 1)[0]
                if not path or any(c in path for c in "[]#^\\") or path.startswith("/"):
                    raise PCWError(f"Navigation links must identify whole notes inside the vault: {value}")
                parts = PurePosixPath(path).parts
                path = path if path.endswith(".md") else path + ".md"
                if '..' in parts:
                    if own_id not in self.paths:
                        raise PCWError(f"Relative wikilink has no source note: {value}")
                    relative = posixpath.normpath(str(PurePosixPath(self.paths[own_id]).parent / path))
                    if relative == '..' or relative.startswith('../'):
                        raise PCWError(f"Wikilink path must stay inside the vault: {value}")
                    matches = self.by_path.get(relative, [])
                else:
                    matches = self.by_path.get(path, [])
                # Obsidian also writes source-relative or shortest unique links.
                if not matches and '..' not in parts and own_id in self.paths:
                    parent = PurePosixPath(self.paths[own_id]).parent
                    matches = self.by_path.get((parent / path).as_posix(), [])
                if not matches and '..' not in parts:
                    matches = [ident for actual, ids in self.by_path.items()
                               if actual.endswith("/" + path) for ident in ids]
                if len(matches) != 1:
                    problem = "Ambiguous" if matches else "Unresolved"
                    raise PCWError(f"{problem} wikilink {value}; repair its real vault path")
                target = matches[0]
            if target != own_id and target not in result:
                result.append(target)
        return result

    def display_aliases(self, values, own_id):
        aliases = {}
        for value in values:
            if isinstance(value, str) and value.startswith('[[') and '|' in value:
                targets = self.decode([value], own_id)
                if targets:
                    aliases[targets[0]] = value[2:-2].split('|', 1)[1]
        return aliases

    def encode(self, values, *, aliases=None):
        result = []
        for ident in values:
            if ident not in self.paths:
                raise PCWError(f"Cannot render missing link target: {ident}")
            path = self.paths[ident]
            if any(c in path for c in "[]|#^\n\r"):
                raise PCWError(f"Target path cannot be represented by a native wikilink: {path}")
            title = (aliases or {}).get(ident, PurePosixPath(path).stem)
            result.append(f"[[{path}|{title}]]")
        return result

    def record_text(self, metadata, body, *, aliases=None):
        result = copy.deepcopy(metadata)
        result["links"] = self.encode(result.get("links", []), aliases=aliases)
        return "---\n" + yaml.safe_dump(result, sort_keys=False, allow_unicode=True) + "---\n" + body

    def rewrite(self, text, own_id, source_workspace=None, render_codec=None):
        metadata, body = _parse(text)
        aliases = self.display_aliases(metadata.get("links", []), own_id)
        metadata["links"] = self.decode(metadata.get("links", []), own_id, source_workspace)
        return (render_codec or self).record_text(metadata, body, aliases=aliases)
