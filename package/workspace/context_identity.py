"""Context identity derived from storage ownership, including visual YAML tags."""

import unicodedata

from .runtime import PCWError


def normalized_context_name(name):
    """Portable comparison: compatibility Unicode normalization and case folding."""
    return unicodedata.normalize("NFKC", name).casefold()


def context_tag(name):
    return "context/" + name


def context_metadata(metadata, name):
    """Copy metadata, deriving the reserved context/ tag from the actual owner."""
    result = dict(metadata)
    tags = result.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise PCWError("tags must be a string or a list of strings")
    result["tags"] = [tag for tag in tags if not tag.startswith("context/")]
    result["tags"].append(context_tag(name))
    return result


def assert_unique_context_names(layout, requested=None):
    """Reject ambiguous names. Call within layout/control guard before mutation."""
    base = layout.workspace_base
    names = [path.name for path in base.iterdir()
             if not path.name.startswith(".") and path.is_dir()] if base.is_dir() else []
    if requested is not None and requested not in names:
        names.append(requested)
    seen = {}
    for name in sorted(names):
        key = normalized_context_name(name)
        if key in seen:
            raise PCWError(f"Context name collision: {seen[key]!r} and {name!r}")
        seen[key] = name
