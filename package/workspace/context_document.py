"""Canonical context source and its explicitly bound shared instructions."""
from .runtime import PCWError, _parse, _revision
from .records import record_text


def record_paths(root):
    paths = sorted((root / "knowledge").rglob("*.md"))
    context = root / "Context.md"
    if context.is_file():
        paths.append(context)
    return [p for p in paths if not any(x.startswith(".") for x in p.relative_to(root).parts)]


def instruction_name(workspace):
    return "workspace." + workspace + ".md"


def library(runtime):
    from contextopus.core.documents.instructions import InstructionLibrary
    return InstructionLibrary(runtime.control.layout)


def bindings(runtime):
    path = runtime.engine.root / "Context.md"
    runtime.engine._assert_safe(path)
    if not path.is_file():
        return None
    metadata, _ = _parse(path.read_bytes().decode("utf-8"))
    result = metadata.get("instructions")
    if result is None:
        return None
    if not isinstance(result, dict) or not isinstance(result.get("workspace"), str):
        raise PCWError("Context.md instructions must map workspace and optional roles to wikilinks")
    return result


def read_instructions(runtime):
    import json
    if not hasattr(runtime.control, "root"):
        return None
    marker = runtime.control.root / "format.json"
    if (runtime.engine.root / "INSTRUCTIONS.md").is_file() and (not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("format_version", 0) < 6):
        return None
    references = bindings(runtime)
    if references is None:
        return None
    store = library(runtime)
    try:
        primary = store.read(references["workspace"])
        required = store.resolve(list(references.values()))
    except (ValueError, OSError) as exc:
        raise PCWError("Cannot resolve Context.md instructions: " + str(exc)) from exc
    return {"path": "INSTRUCTIONS.md", "source_path": "core/instructions/" + primary["path"],
            "content": primary["content"], "revision": _revision(primary["content"]),
            "required_revision": required["revision"],
            "primary": {key: value for key, value in primary.items() if key != "content"},
            "documents": [document for document in required["instructions"] if document["id"] != primary["id"]]}


def save_instructions(runtime, content):
    references = bindings(runtime)
    if references is None:
        return runtime.engine._write(runtime.engine.root / "INSTRUCTIONS.md", content)
    store = library(runtime)
    current = store.read(references["workspace"])
    if current["content"] == content:
        return current
    return store.save(current["path"], current["metadata"], content,
                      expected_revision=current["revision"])


def ensure_instruction(runtime, content):
    """Only creation/migration calls this; ordinary reads never copy rules."""
    store = library(runtime)
    name = instruction_name(runtime.engine.workspace)
    try:
        current = store.read(name)
    except FileNotFoundError:
        return store.save(name, {"id": name.removesuffix(".md"), "type": "instruction",
                          "title": "Workspace rules: " + runtime.engine.workspace,
                          "description": "The complete user rules for this context."}, content)
    if current["content"] != content:
        raise PCWError("Shared instruction destination already contains different rules; review it before migration")
    return current


def bind_project(runtime, text):
    metadata, body = _parse(text)
    references = metadata.setdefault("instructions", {})
    if not isinstance(references, dict):
        raise PCWError("Context.md instructions must be a mapping")
    references.setdefault("workspace", "[[" + instruction_name(runtime.engine.workspace).removesuffix(".md") + "]]")
    return record_text(metadata, body)
