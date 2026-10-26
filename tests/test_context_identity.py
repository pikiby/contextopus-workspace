"""Context tags are a visualization of ownership, with root-wide unique names."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from pcw_runtime import PCWError
from pcw_runtime.cycles import CycleRuntime
from workspace.context_identity import (
    assert_unique_context_names, context_metadata, normalized_context_name,
)
from workspace.layout import WorkspaceLayout as StorageLayout
from pcw_runtime.runtime import _parse
from workspace.records import task_record
from test_runtime import Factory
from test_workspace_v3 import create
from test_shared_v4 import context, save


def expose_foreign_context_names(monkeypatch, base, names):
    """Inspect case-distinct foreign entries even when this filesystem aliases them."""
    original = Path.iterdir

    def entries(path):
        if path == base:
            return iter(base / name for name in names)
        return original(path)

    monkeypatch.setattr(Path, "iterdir", entries)


def test_context_metadata_preserves_user_tags_and_identity():
    original = {"id": "stable.md", "tags": ["team/red", "context/wrong", "custom"]}
    derived = context_metadata(original, "actual")
    assert derived == {"id": "stable.md", "tags": ["team/red", "custom", "context/actual"]}
    assert original["tags"] == ["team/red", "context/wrong", "custom"]
    assert normalized_context_name("Straße") == normalized_context_name("STRASSE")
    assert normalized_context_name("Ａlpha") == normalized_context_name("alpha")
    task_metadata, task_body = _parse(task_record("stable.md", "---\ntags: [custom]\ngoal: Preserve tags\n---\n"))
    assert task_metadata["tags"] == ["custom"]
    assert "## tags" not in task_body


def test_unicode_collision_in_existing_storage(tmp_path, monkeypatch):
    layout = StorageLayout(tmp_path)
    # Deliberately inspect names made on another filesystem, even when the local
    # creation API accepts a narrower set of names.
    (layout.workspace_base / "Straße").mkdir(parents=True)
    alternative = layout.workspace_base / "STRASSE"
    if alternative.exists():
        expose_foreign_context_names(monkeypatch, layout.workspace_base, ("Straße", "STRASSE"))
    else:
        alternative.mkdir()
    with layout.guard(), pytest.raises(PCWError, match="Context name collision"):
        assert_unique_context_names(layout)


def test_case_collision_creation_and_existing_collision(tmp_path, monkeypatch):
    first = context(tmp_path, "Alpha")
    second = CycleRuntime(tmp_path, "alpha", provider_factory=Factory())
    try:
        with pytest.raises(PCWError, match="Context name collision"):
            create(second)
        base = first.control.layout.workspace_base
        actual = [path for path in base.iterdir() if not path.name.startswith(".") and path.is_dir()]
        assert [path.name for path in actual] == ["Alpha"]
        if second.engine.root.exists():
            assert second.engine.root.samefile(first.engine.root)
            expose_foreign_context_names(monkeypatch, base, ("Alpha", "alpha"))
        else:
            second.engine.root.mkdir()
        with pytest.raises(PCWError, match="Context name collision"):
            first.list()
    finally:
        first.close()
        second.close()


def test_concurrent_case_variants_allow_only_one_context(tmp_path):
    from workspace.analytics import EventStore
    store = EventStore(tmp_path)
    store.storage_apply(store.storage_prepare()['revision'])
    runtimes = [CycleRuntime(tmp_path, name, provider_factory=Factory()) for name in ("Alpha", "alpha")]
    def attempt(runtime):
        try:
            create(runtime)
            return "created"
        except PCWError as exc:
            assert "Context name collision" in str(exc)
            return "collision"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(attempt, runtimes)) == ["collision", "created"]
        base = runtimes[0].control.layout.workspace_base
        actual = [path for path in base.iterdir() if not path.name.startswith(".") and path.is_dir()]
        assert len(actual) == 1 and actual[0].name in {"Alpha", "alpha"}
    finally:
        for runtime in runtimes:
            runtime.close()


def test_write_read_reconcile_and_move_derive_tags(tmp_path):
    runtime = context(tmp_path, "alpha")
    try:
        item = save(runtime, "# Tagged\nUseful facts")
        path = runtime.engine.root / item["path"]
        metadata, body = _parse(path.read_text(encoding="utf-8"))
        assert metadata["tags"] == ["context/alpha"]
        # An external editor can supply unrelated tags and a stale ownership tag.
        import yaml
        metadata["tags"] = ["custom", "context/stale"]
        path.write_text("---\n" + yaml.safe_dump(metadata) + "---\n" + body, encoding="utf-8")
        before = path.read_bytes()
        observed = runtime.read(material_id=item["id"])
        assert observed["tags"] == ["custom", "context/alpha"]
        assert path.read_bytes() == before  # Reading never repairs source files.
        runtime.engine.reconcile()
        rows = runtime.engine._rows()
        assert all(json.loads(row["metadata"])["tags"] == ["custom", "context/alpha"]
                   for row in rows if row["material_id"] == item["id"])
        cycle = runtime.startup("update-tagged-record")["cycle_id"]
        proposal = runtime.proposal_prepare(cycle, expected_task_revision=runtime.status(cycle)["task_revision"], changes=[{
            "material_id": item["id"], "expected_revision": observed["revision"],
            "content": "# Tagged\nUpdated useful facts",
            "description": "Updated useful facts with their context and custom tags preserved",
        }])
        assert runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])["status"] == "applied"
        updated = runtime.read(material_id=item["id"])
        assert updated["id"] == item["id"]
        assert updated["tags"] == ["custom", "context/alpha"]
        assert _parse(path.read_text(encoding="utf-8"))[0]["tags"] == ["custom", "context/alpha"]
        moved = runtime.engine.rename(item["id"], "renamed.md")
        assert moved["id"] == item["id"]
        assert _parse((runtime.engine.root / "knowledge/renamed.md").read_text(encoding="utf-8"))[0]["tags"] == ["custom", "context/alpha"]
    finally:
        runtime.close()
