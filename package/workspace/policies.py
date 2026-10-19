"""Machine-readable workspace settings. Missing permissions stay restrictive."""

from __future__ import annotations

import yaml
import re

from .configuration import WORKSPACE_POLICY_DEFAULTS, WORKSPACE_CREATION_DEFAULTS

from .runtime import PCWError


DEFAULTS = WORKSPACE_POLICY_DEFAULTS


def policies(content: str, *, creation: bool = False) -> dict:
    """Import legacy frontmatter only; runtime permissions come from Core settings."""
    if not isinstance(content, str):
        raise PCWError("INSTRUCTIONS content must be text")
    result = dict(DEFAULTS)
    if creation:
        result["cleanup_approval_required"] = WORKSPACE_CREATION_DEFAULTS["cleanup_approval_required"]
    supplied = set()
    lines = content.splitlines()
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
            node = yaml.compose("\n".join(lines[1:end]), Loader=yaml.SafeLoader)
        except (ValueError, yaml.YAMLError) as exc:
            raise PCWError("Invalid INSTRUCTIONS.md frontmatter") from exc
        if node is not None and not isinstance(node, yaml.MappingNode):
            raise PCWError("INSTRUCTIONS.md frontmatter must be a mapping")
        for key, value in node.value if node else []:
            if not isinstance(key, yaml.ScalarNode) or key.value not in DEFAULTS:
                continue
            name = key.value
            if name in supplied:
                raise PCWError(f"Duplicate setting: {name}")
            supplied.add(name)
            if name == "material_max_tokens":
                if (
                    isinstance(value, yaml.ScalarNode)
                    and value.tag == "tag:yaml.org,2002:null"
                ):
                    result[name] = None
                elif (
                    isinstance(value, yaml.ScalarNode)
                    and value.tag == "tag:yaml.org,2002:int"
                    and value.value.isascii()
                    and value.value.isdecimal()
                    and int(value.value) > 0
                ):
                    result[name] = int(value.value)
                else:
                    raise PCWError(
                        "material_max_tokens must be a positive integer or null"
                    )
            else:
                if (
                    not isinstance(value, yaml.ScalarNode)
                    or value.tag != "tag:yaml.org,2002:bool"
                    or value.value.lower() not in {"true", "false"}
                ):
                    raise PCWError(f"{name} must be one boolean true or false")
                result[name] = value.value.lower() == "true"
    # Existing clients supplied the original six settings. Creation uses the
    # current cleanup preset when omitted; legacy imports keep their safe fallback.
    if creation and not (set(DEFAULTS) - {"cleanup_approval_required"}) <= supplied:
        raise PCWError(
            "Creation requires the original policy settings explicitly supplied in INSTRUCTIONS, including the source-link choice: "
            + ", ".join(DEFAULTS)
        )
    return result


def extract_settings(content: str, *, creation: bool = False) -> tuple[dict, str]:
    """Remove only known legacy keys, retaining all other lines and newline bytes."""
    if not isinstance(content, str):
        raise PCWError("INSTRUCTIONS content must be text")
    defaults = dict(DEFAULTS)
    if creation:
        defaults["cleanup_approval_required"] = WORKSPACE_CREATION_DEFAULTS["cleanup_approval_required"]
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return dict(defaults), content
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
    if end is None:
        return dict(defaults), content
    frontmatter = "".join(lines[1:end])
    try:
        node = yaml.compose(frontmatter, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        if re.search(r"^\s*(?:" + "|".join(DEFAULTS) + r")\s*:", frontmatter, re.MULTILINE):
            policies(content)  # Preserve legacy validation for actual machine settings.
        return dict(defaults), content
    if not isinstance(node, yaml.MappingNode):
        return dict(defaults), content
    known = any(isinstance(key, yaml.ScalarNode) and key.value in DEFAULTS for key, _ in node.value)
    if not known:
        return dict(defaults), content
    settings = policies(content)
    if creation and not any(key.value == "cleanup_approval_required" for key, _ in node.value
                            if isinstance(key, yaml.ScalarNode)):
        settings["cleanup_approval_required"] = defaults["cleanup_approval_required"]
    removed = set()
    for key, value in node.value if node else []:
        if isinstance(key, yaml.ScalarNode) and key.value in DEFAULTS:
            stop = value.end_mark.line + int(value.end_mark.column > 0)
            removed.update(range(key.start_mark.line + 1, stop + 1))
    if not removed:
        return settings, content
    retained = [line for i, line in enumerate(lines[1:end], 1) if i not in removed]
    if any(line.strip() for line in retained):
        try:
            yaml.compose("".join(retained), Loader=yaml.SafeLoader)
        except yaml.YAMLError as exc:
            raise PCWError("User frontmatter depends on legacy machine settings; separate those YAML references before migration") from exc
        text = lines[0] + "".join(retained) + "".join(lines[end:])
    else:
        text = "".join(lines[end + 1:])
    return settings, text
