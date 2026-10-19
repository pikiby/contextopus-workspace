"""Read-only preparation of recoverable temporary tree transitions."""
import re
import base64
import os
import tempfile
import posixpath
from pathlib import Path
from urllib.parse import unquote
from .runtime import PCWError, _id, _parse
from .records import record_text
from .temporary_materials import card_name, card_name_key, source_references, is_contextopus_temporary_reference
from .sources import declarations, migrate_sources


def temporary_transforms(runtime):
    transforms, occupied, identities = {}, set(), set()
    for base in ("tmp", ".tmp"):
        for path in sorted((runtime.engine.root / base).rglob("*")):
            runtime.engine._assert_safe(path)
            if not path.is_file():
                continue
            relative = path.relative_to(runtime.engine.root).as_posix()
            raw = path.read_bytes()
            encoding = None
            try:
                before = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                if path.parent.name == "materials" and path.suffix.casefold() == ".md":
                    raise PCWError(f"Temporary Markdown card is not UTF-8: {relative}; retain and classify it before migration") from exc
                encoding = "base64"
                before = base64.b64encode(raw).decode("ascii")
            destination = Path(".tmp") / path.relative_to(runtime.engine.root / base)
            text = before
            if path.parent.name == "materials" and path.suffix.casefold() == ".md":
                metadata, body = _parse(before)
                ident = _id(metadata.get("id", path.name))
                if ident in identities:
                    raise PCWError(f"Duplicate temporary YAML ID: {ident}")
                identities.add(ident)
                metadata["id"] = ident
                metadata.setdefault("type", "knowledge")
                metadata.setdefault("links", [])
                text = record_text(metadata, body)
                if "id" not in _parse(before)[0] or path.name == ident:
                    destination = destination.with_name(card_name(None, body))
                else:
                    destination = destination.with_name(card_name(path.name, body))
                if card_name_key(destination.as_posix()) in occupied:
                    destination = destination.with_name(destination.stem + "-" + ident.removesuffix(".md") + ".md")
            key = card_name_key(destination.as_posix())
            if key in occupied:
                raise PCWError(f"Temporary migration filename collision: {destination}")
            occupied.add(key)
            target = runtime.engine.root / destination
            runtime.engine._assert_safe(target)
            if target != path and target.exists():
                raise PCWError(f"Temporary migration destination already exists: {destination}")
            transforms[relative] = {"before": before, "path": destination.as_posix(), "text": text,
                                    "source_absolute": str(path), "destination_absolute": str(target)}
            if encoding:
                transforms[relative]["encoding"] = encoding
    paths = {old: item["path"] for old, item in transforms.items()}
    # Rewrite native paths only in working material; immutable proposal/review
    # artifacts remain byte-for-byte intact and use execution overlays instead.
    for old, item in transforms.items():
        if Path(old).parent.name != "materials" or Path(old).suffix.casefold() != ".md":
            continue
        for source, target in sorted(paths.items(), key=lambda pair: -len(pair[0])):
            item["text"] = item["text"].replace("[[" + source + "]]", "[[" + target + "]]")
            item["text"] = item["text"].replace("[[" + source + "|", "[[" + target + "|")
    return transforms


def permanent_temporary_references(text, transforms, *, allow_any_transient=True, allow_malformed=False, known_cycles=(), source_path=None):
    temporary_ids, names, paths, absolute_paths = set(), set(), set(), set()
    for old, item in transforms.items():
        paths.update((old, item["path"]))
        absolute_paths.update(item[key] for key in ("source_absolute", "destination_absolute") if key in item)
        if Path(old).parent.name == "materials" and Path(old).suffix.casefold() == ".md":
            temporary_ids.add(_parse(item["text"])[0]["id"])
            names.update((Path(old).stem, Path(item["path"]).stem))
    metadata, body = _parse(text)
    embedded, body = _parse(body)
    links = metadata.get("links", [])
    embedded_links = embedded.get("links", [])
    if not isinstance(links, list) or not isinstance(embedded_links, list):
        raise PCWError("links must be a list")
    references = links + embedded_links + re.findall(r"\[\[([^]\n]+)\]\]", body)
    references += re.findall(r"\]\(([^)\n]+)\)", body)
    try:
        references += source_references(metadata) + source_references(embedded)
        references += [item["reference"] for item in declarations(migrate_sources(body))]
    except PCWError:
        if not allow_malformed:
            raise
        # Foreign drafts and records remain byte-for-byte untouched. Dependency
        # discovery still recognizes reference tokens when an instruction scalar
        # makes an old declaration invalid YAML.
        references += re.findall(r"(?m)^\s*(?:-\s*)?(?:reference|url):\s*(.+?)\s*$", text)
        references = [value.strip("\"'") for value in references]
    found = []
    for reference in references:
        target = str(reference).strip().removeprefix("[[").removesuffix("]]").split("|", 1)[0]
        target = re.sub(r'\s+["\'].*$', "", target).strip("<>")
        target = unquote(target).removeprefix("file://").split("#", 1)[0]
        if target.startswith(("https://", "http://")):
            continue
        managed = is_contextopus_temporary_reference(target, known_cycles=known_cycles)
        actual_path = target in absolute_paths or target in paths
        if source_path is not None and not Path(target).is_absolute():
            actual_path = actual_path or os.path.abspath(source_path.parent / target) in absolute_paths
        if not Path(target).is_absolute() and managed:
            actual_path = actual_path or any(target.endswith("/" + path) for path in paths)
            actual_path = actual_path or any(path.endswith("/" + target) for path in absolute_paths)
        if (target in temporary_ids or target.removesuffix(".md") in names
                or actual_path or allow_any_transient and managed):
            found.append(target)
    return found


def dependency_transforms(runtime, transforms):
    result = dict(transforms)
    for path, card in getattr(runtime, "_temporary_cards", lambda: [])():
        relative = path.relative_to(runtime.engine.root).as_posix() if path.is_relative_to(runtime.engine.root) else card["path"]
        metadata = dict(card["metadata"])
        metadata.setdefault("id", card["id"])
        metadata.setdefault("type", "knowledge")
        result.setdefault(relative, {"path": relative, "text": record_text(metadata, card["content"]),
                                     "source_absolute": str(path), "destination_absolute": str(path)})
    return result


def permanent_temporary_blockers(runtime, transforms):
    blockers = []
    global_transforms = dependency_transforms(runtime, transforms)
    known_cycles = set(getattr(runtime, "_records", lambda: {"cycles": {}})().get("cycles", {}))
    roots = [runtime.engine.root]
    if transforms and hasattr(runtime, "control") and runtime.engine.root.parent.is_dir():
        roots.extend(path for path in runtime.engine.root.parent.iterdir()
                     if path != runtime.engine.root and path.is_dir() and not path.name.startswith("."))
    for root in roots:
        inventory = global_transforms if root == runtime.engine.root else transforms
        for path in sorted((root / "knowledge").rglob("*.md")):
            runtime.engine._assert_safe(path)
            try:
                references = permanent_temporary_references(path.read_bytes().decode("utf-8"), inventory,
                    allow_any_transient=root == runtime.engine.root, allow_malformed=root != runtime.engine.root,
                    known_cycles=known_cycles if root == runtime.engine.root else (), source_path=path)
            except (PCWError, UnicodeError) as exc:
                blockers.append({"reason": str(exc), "workspace": root.name,
                                 "path": path.relative_to(root).as_posix()})
                continue
            for target in references:
                blockers.append({"reason": "Permanent knowledge references temporary work; promote or explicitly revise the dependency before migration",
                                 "workspace": root.name, "path": path.relative_to(root).as_posix(), "target": target})
    return blockers


def retarget_temporary(transforms, permanent, aliases, known_permanent_ids=()):
    """Adapt working-card navigation while leaving immutable reviews untouched."""
    paths = {old: item['path'] for old, item in {**permanent, **transforms}.items()}
    permanent_ids = set(known_permanent_ids) | {item["id"] for item in permanent.values() if "id" in item}
    for old, item in transforms.items():
        if Path(old).parent.name == "materials" and Path(old).suffix.casefold() == ".md":
            ident = _parse(item["text"])[0]["id"]
            if ident in permanent_ids:
                raise PCWError(f"Temporary ID conflicts with permanent knowledge: {ident}")
    stems = {}
    for source, target in paths.items():
        stems.setdefault(Path(source).stem, set()).add(target)
    unambiguous = {stem: next(iter(targets)) for stem, targets in stems.items() if len(targets) == 1}
    for old, item in transforms.items():
        if Path(old).parent.name != 'materials' or Path(old).suffix.casefold() != '.md':
            continue
        metadata, body = _parse(item['text'])
        links = metadata.get('links', [])
        if not isinstance(links, list) or any(not isinstance(value, str) for value in links):
            raise PCWError('Temporary card links must be a list of strings')
        metadata['links'] = [aliases.get(value, value) for value in links]
        addresses = {**unambiguous, **paths}
        addresses.update({posixpath.relpath(source, str(Path(old).parent)): target for source, target in paths.items()})
        for field in ("sources", "required_sources"):
            values = metadata.get(field)
            if isinstance(values, list):
                for index, source in enumerate(values):
                    if isinstance(source, str) and source in addresses:
                        values[index] = addresses[source]
                    elif isinstance(source, dict):
                        for key in ("reference", "url"):
                            if isinstance(source.get(key), str) and source[key] in addresses:
                                source[key] = addresses[source[key]]
        try:
            text = migrate_sources(record_text(metadata, body))
        except PCWError as exc:
            # Drafts are unfinished work, not declarations already accepted as
            # knowledge. Keep malformed source prose for explicit later repair.
            text = record_text(metadata, body)
            item["source_diagnostic"] = {"status": "needs_correction", "reason": str(exc)}
        for source, target in sorted(addresses.items(), key=lambda pair: -len(pair[0])):
            for old_target, new_target in ((source, target), (source.removesuffix('.md'), target.removesuffix('.md'))):
                text = text.replace('[[' + old_target + ']]', '[[' + new_target + ']]')
                text = text.replace('[[' + old_target + '|', '[[' + new_target + '|')
                text = text.replace('](' + old_target + ')', '](' + new_target + ')')
        item['text'] = _retarget_source_declarations(text, addresses)


def _retarget_source_declarations(text, paths):
    """Change only source-address scalar tokens, preserving authored conditions."""
    import json
    import yaml
    pattern = re.compile(r'(?m)^```ya?ml[ \t]*\r?\n(.*?)^```[ \t]*\r?$', re.S)
    def rewrite(match):
        if not re.search(r'(?m)^(?:sources|required_sources)\s*:', match[1]):
            return match[0]
        try:
            tree = yaml.compose(match[1])
        except yaml.YAMLError:
            # An invalid instruction scalar must not force semantic edits to an
            # unfinished draft. Only exact, independently parseable addresses
            # may be adapted to their reviewed new paths.
            def address(line):
                try:
                    value = yaml.safe_load(line[2])
                except yaml.YAMLError as exc:
                    if any(source in line[2] for source in paths):
                        raise PCWError("Malformed source address cannot be retargeted safely") from exc
                    return line[0]
                if isinstance(value, str) and value in paths:
                    comment = re.search(r"\s+#.*$", line[2])
                    return line[1] + json.dumps(paths[value], ensure_ascii=False) + (comment[0] if comment else "")
                return line[0]
            block = re.sub(r"(?m)^(\s*(?:-\s*)?(?:reference|url):[ \t]*)([^\r\n]+)(?=\r?$)", address, match[1])
            offset = match.start(1) - match.start()
            return match[0][:offset] + block + match[0][offset + len(match[1]):]
        edits = []
        def address_node(node):
            if isinstance(node, yaml.ScalarNode):
                if node.value in paths:
                    edits.append((node.start_mark.index, node.end_mark.index, json.dumps(paths[node.value], ensure_ascii=False)))
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    address_node(child)
            elif isinstance(node, yaml.MappingNode):
                for key, value in node.value:
                    if key.value in {'reference', 'url'}:
                        address_node(value)
        if isinstance(tree, yaml.MappingNode):
            for key, values in tree.value:
                if key.value in {'sources', 'required_sources'}:
                    address_node(values)
        block = match[1]
        for start, end, replacement in sorted(edits, reverse=True):
            block = block[:start] + replacement + block[end:]
        offset = match.start(1) - match.start()
        return match[0][:offset] + block + match[0][offset + len(match[1]):]
    return pattern.sub(rewrite, text)


def transform_content(path, item):
    raw = path.read_bytes()
    return base64.b64encode(raw).decode("ascii") if item.get("encoding") == "base64" else raw.decode("utf-8")


def write_transform(runtime, path, item):
    """Apply opaque resources byte-for-byte with the same durable atomic replace."""
    if item.get("encoding") != "base64":
        runtime.engine._write(path, item["text"])
        return
    runtime.engine._assert_safe(path)
    payload = base64.b64decode(item["text"], validate=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".migration-write-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        from contextopus.core.storage import sync_directory
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
