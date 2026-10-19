"""Explicit record provenance stored in ordinary Git commit trailers."""

from .runtime import PCWError, _id, _parse
import json


def transformations(values, entries, resolve, replacements):
    """Validate declared transformations; never infer semantic merges from a diff."""
    if values is None:
        # Older clients explicitly declared link replacement, not necessarily a merge.
        return [{"kind": "replace", "sources": [old], "targets": [new]}
                for old, new in replacements.items()]
    if not isinstance(values, list) or len(values) > 100:
        raise PCWError("transformations must contain at most 100 declarations")
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {"kind", "sources", "targets"}:
            raise PCWError("A transformation requires kind, sources and targets")
        kind = value["kind"]
        if not isinstance(kind, str) or kind not in {"merge", "split", "replace"}:
            raise PCWError("Transformation kind must be merge, split or replace")
        groups = []
        for field, side in (("sources", "before"), ("targets", "after")):
            raw = value[field]
            if not isinstance(raw, list) or not raw or len(raw) > 100:
                raise PCWError("Transformation endpoints must be nonempty ID lists")
            ids = [resolve(x) for x in raw]
            if len(ids) != len(set(ids)) or any(not entries.get(x, {}).get(side) for x in ids):
                raise PCWError("Transformation endpoints must name distinct reviewed before/after records")
            groups.append(ids)
        sources, targets = groups
        valid = {"merge": len(sources) >= 2 and len(targets) == 1,
                 "split": len(sources) == 1 and len(targets) >= 2,
                 "replace": len(sources) == len(targets) == 1 and sources != targets}
        if not valid[kind]:
            raise PCWError("Transformation cardinality does not match its kind")
        if any(entries[x]["after"] is not None and x not in targets for x in sources):
            raise PCWError("A transformed source must be deleted or retained among its targets")
        result.append({"kind": kind, "sources": sources, "targets": targets})
    if any(not any(old in t["sources"] and new in t["targets"] for t in result)
           for old, new in replacements.items()):
        raise PCWError("Transformations must cover the declared replacements")
    return result


def event_from_plan(operation_id, plan=None, files=None):
    plan = plan or {}
    kind = plan.get("kind", "knowledge" if plan else operation_id.split("-", 1)[0])
    changes = []
    for ident, entry in sorted(plan.get("entries", {}).items()):
        if entry["before"] == entry["after"]:
            continue
        sides = {}
        for side in ("before", "after"):
            snapshot = entry[side]
            sides[side] = ({"path": snapshot["path"], "revision": snapshot["fingerprint"]}
                           if snapshot else None)
        changes.append({"id": ident, **sides})
    # Initial/migration snapshots have no reviewed knowledge proposal. Index their
    # record membership without pretending every record was semantically changed.
    snapshot_ids = []
    if not plan:
        for path, text in (files or {}).items():
            if path == ".contextopus-settings.json":
                continue  # Virtual historical representation has no material identity.
            snapshot_ids.append(_parse(text)[0].get("id", path.rsplit("/", 1)[-1])
                                if path.startswith("knowledge/") else path)
    return {"version": 1, "operation_id": operation_id, "kind": kind,
            "cycle_id": plan.get("cycle_id"), "summary": plan.get("summary"),
            "changes": changes, "snapshot_record_ids": snapshot_ids,
            "transformations": plan.get("transformations", [])}


def commit_message(event):
    summary = event.get("summary") or {
        "initial": "Initial context snapshot", "migration": "Upgrade context format",
        "instructions": "Update context instructions",
    }.get(event["kind"], f"Update {len(event['changes'])} permanent records")
    ids = {x["id"] for x in event["changes"]} | set(event.get("snapshot_record_ids", []))
    for transform in event["transformations"]:
        ids.update(transform["sources"] + transform["targets"])
    trailers = ["Workspace-Operation: " + event["operation_id"]]
    trailers += ["Workspace-Record: " + _id(ident) for ident in sorted(ids)]
    trailers.append("Workspace-Event: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    return f"Contextopus: {summary} [{event['operation_id']}]\n\n" + "\n".join(trailers) + "\n"


def read_event(message):
    """Old or external commits have no declared provenance; do not invent it."""
    for line in reversed(message.splitlines()):
        if line.startswith("Workspace-Event: "):
            try:
                event = json.loads(line[len("Workspace-Event: "):])
                if (isinstance(event, dict) and event.get("version") == 1
                        and isinstance(event.get("operation_id"), str)
                        and isinstance(event.get("changes"), list)
                        and all(isinstance(x, dict) and isinstance(x.get("id"), str) for x in event["changes"])
                        and isinstance(event.get("transformations"), list)
                        and all(isinstance(t, dict) and t.get("kind") in ("merge", "split", "replace")
                                and all(isinstance(t.get(k), list) and all(isinstance(x, str) for x in t[k])
                                        for k in ("sources", "targets")) for t in event["transformations"])
                        and isinstance(event.get("snapshot_record_ids", []), list)):
                    return event
            except (ValueError, TypeError):
                pass
    return None
