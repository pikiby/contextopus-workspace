"""Bounded source declarations. Reading a declaration never executes its contents."""

import re

import yaml

from .runtime import PCWError


_BLOCK = re.compile(r"(?m)^```ya?ml[ \t]*\r?\n(.*?)^```[ \t]*\r?$", re.S)


def declarations(content):
    result = []
    for match in _BLOCK.finditer(content):
        if not re.search(r"(?m)^sources\s*:", match[1]):
            continue
        try:
            data = yaml.safe_load(match[1])
        except yaml.YAMLError as exc:
            raise PCWError("Invalid sources YAML") from exc
        values = data.get("sources") if isinstance(data, dict) else None
        if not isinstance(values, list):
            raise PCWError("sources must be a list")
        for value in values:
            if not isinstance(value, dict) or any(
                not isinstance(value.get(key), str) or not value[key].strip()
                for key in ("reference", "instruction")
            ):
                raise PCWError(
                    "Each source requires nonempty reference and instruction"
                )
            result.append(value)
    return result


def migrate_sources(content):
    def convert(match):
        if not re.search(r"(?m)^required_sources\s*:", match[1]):
            return match[0]
        try:
            data = yaml.safe_load(match[1])
        except yaml.YAMLError as exc:
            raise PCWError("Cannot migrate malformed required_sources YAML") from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("required_sources"), list)
            or "sources" in data
        ):
            raise PCWError("Ambiguous required_sources block; inspect before migration")
        for old in data["required_sources"]:
            if not isinstance(old, dict) or any(
                not isinstance(old.get(key), str) or not old[key].strip()
                for key in ("url", "extract")
            ):
                raise PCWError("Legacy source requires url and extract")
            if "reference" in old or "instruction" in old:
                raise PCWError("Conflicting legacy source keys")
        # Change key tokens, retaining comments, scalar spelling, line endings
        # and surrounding text. Re-serializing YAML would discard comments that
        # may carry source conditions.
        tree = yaml.compose(match[1])
        edits = set()
        for key, sequence in tree.value:
            if key.value != "required_sources":
                continue
            edits.add((key.start_mark.index, key.end_mark.index, "sources"))
            for item in sequence.value:
                for field, value in item.value:
                    replacement = {"url": "reference", "extract": "instruction"}.get(
                        field.value
                    )
                    if replacement:
                        edits.add(
                            (field.start_mark.index, field.end_mark.index, replacement)
                        )
        body = match[1]
        for start, end, replacement in sorted(edits, reverse=True):
            body = body[:start] + replacement + body[end:]
        offset = match.start(1) - match.start()
        return match[0][:offset] + body + match[0][offset + len(match[1]) :]

    migrated = _BLOCK.sub(convert, content)
    declarations(migrated)
    return migrated


def needs_source_upgrade(content):
    return any(
        re.search(r"(?m)^required_sources\s*:", match[1])
        for match in _BLOCK.finditer(content)
    )
