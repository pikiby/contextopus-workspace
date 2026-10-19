"""Workspace record metadata and lossless legacy document conversion."""

import uuid
import json
import yaml

from .runtime import PCWError, _parse, _id, _revision


def validate_metadata(metadata):
    # Categories are user/agent vocabulary, not a closed application enum.
    for field in ("type", "status"):
        value = metadata.get(field)
        if field == "status" and value is None:
            continue
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise PCWError(f"{field} must be nonempty trimmed text")


def validate_description(metadata, *, required=False):
    """Check the author's supplied data; meaning is reviewed by the author."""
    value = metadata.get("description")
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise PCWError("description must be nonempty author-written text")


def record_text(metadata, body):
    validate_metadata(metadata)
    return "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n" + body


def document_id(workspace, name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"contextopus:workspace:{workspace}:{name}").hex


def project_record(workspace, text, *, description=None, require_description=False):
    from .context_identity import context_metadata
    metadata, body = _parse(text)
    metadata = context_metadata(metadata, workspace)
    metadata.setdefault("id", document_id(workspace, "project"))
    _id(metadata["id"])
    metadata.update(type="project")
    metadata.setdefault("links", [])
    if description is not None:
        metadata["description"] = description
    if require_description or description is not None:
        validate_description(metadata, required=True)
    return metadata["id"], record_text(metadata, body)


def task_record(ident, text):
    metadata, body = _parse(text)
    # Earlier Current files put their entire content in YAML. Move every field
    # into searchable Markdown, retaining arbitrary/unknown fields as well.
    fields = {k: v for k, v in metadata.items() if k not in {"id", "type", "links", "status", "description", "tags"}}
    sections = [f"# {fields.get('goal') or 'Task state'}\n"]
    sections.extend(f"\n## {key}\n\n{value if isinstance(value, str) else yaml.safe_dump(value, allow_unicode=True)}\n" for key, value in fields.items())
    if body:
        sections.append("\n" + body)
    result = {"id": ident, "type": "task", "links": metadata.get("links", [])}
    if "status" in metadata:
        result["status"] = metadata["status"]
    if "description" in metadata:
        result["description"] = metadata["description"]
    if "tags" in metadata:
        result["tags"] = metadata["tags"]
    return record_text(result, "".join(sections))


def record_revision(material):
    """Authoring conflicts include metadata; vectors remain keyed by body revision."""
    return _revision(json.dumps(material.metadata, sort_keys=True, ensure_ascii=False) + "\n" + material.content)
