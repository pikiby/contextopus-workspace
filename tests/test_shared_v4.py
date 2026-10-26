import json
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import copy

import pytest

from pcw_runtime import PCWRuntime, PCWError
from pcw_runtime.cycles import CycleRuntime
from pcw_runtime.runtime import _parse
from pcw_runtime.sources import declarations, migrate_sources
from pcw_runtime.shared import topic_name
from test_runtime import Factory
from test_workspace_v3 import create


def context(root, name, factory=None):
    runtime = CycleRuntime(root, name, provider_factory=factory or Factory())
    create(runtime, knowledge_approval_required=False, material_max_tokens=None)
    return runtime


def save(runtime, text, links=None):
    cycle = runtime.startup("save-" + str(len(runtime._records()["cycles"])))["cycle_id"]
    prepared = runtime.proposal_prepare(cycle, expected_task_revision=runtime.status(cycle)["task_revision"], changes=[{"key": "new", "content": text,
                                               "description": "Facts used to verify shared context retrieval and links",
                                               "links": links or []}])
    assert runtime.proposal_apply(cycle, prepared["proposal_id"], prepared["revision"])["status"] == "applied"
    return runtime.read(material_id=prepared["created_ids"]["new"])


def test_categories_prefilter_links_rules_and_shared_reconcile(tmp_path):
    a, b = context(tmp_path, "alpha"), context(tmp_path, "beta")
    try:
        one = save(a, "# Alpha procedure\nanswer")
        two = save(b, "# Beta procedure\nanswer", [one["id"]])
        assert a.engine.state == b.engine.state == tmp_path / ".pcw/.rag"
        assert not (a.engine.root / ".pcw-runtime").exists()
        assert a.search(types=["knowledge"], query="answer", N=1, d_max=2)["results"][0]["id"] == one["id"]
        assert a.search(types=["knowledge"], query="answer", categories=["beta"], N=1, d_max=2)["results"][0]["id"] == two["id"]
        both = a.search(types=["knowledge"], query="answer", all_categories=True, N=100, d_max=2)
        assert {r["workspace"] for r in both["results"]} == {"alpha", "beta"}
        assert both["active_workspace"] == "alpha"
        assert a.read(material_id=one["id"], from_id=two["id"])["workspace"] == "alpha"
        assert a.read(material_id=two["id"])["workspace"] == "beta"
        a.engine.reconcile()
        assert b.search(types=["knowledge"], query="answer", d_max=2)["results"][0]["id"] == two["id"]
        assert a.engine.root.name == "alpha"
        cycle = a.startup("delete")["cycle_id"]
        with pytest.raises(PCWError, match="explicitly update"):
            a.proposal_prepare(cycle, [{"material_id": one["id"], "expected_revision": one["revision"], "delete": True}], expected_task_revision=a.status(cycle)["task_revision"])
        with pytest.raises(PCWError, match="Unknown material"):
            a.proposal_prepare(cycle, [{"material_id": two["id"], "expected_revision": two["revision"], "content": "bad"}], expected_task_revision=a.status(cycle)["task_revision"])
    finally:
        a.close(); b.close()


def test_rename_retains_yaml_id_links_and_embeddings(tmp_path):
    factory = Factory()
    runtime = context(tmp_path, "one", factory)
    item = save(runtime, "# Порядок подключения\nТочные условия.")
    assert item["path"] == "knowledge/Порядок-подключения.md"
    assert _parse((runtime.engine.root / item["path"]).read_text(encoding="utf-8"))[0]["id"] == item["id"]
    calls = len(factory.calls)
    cycle = runtime.startup("rename")["cycle_id"]
    p = runtime.proposal_prepare(cycle, [{"material_id": item["id"], "expected_revision": item["revision"], "path": "guides/Подключение.md"}], expected_task_revision=runtime.status(cycle)["task_revision"])
    runtime.proposal_apply(cycle, p["proposal_id"], p["revision"])
    result = runtime.read(material_id=item["id"])
    assert result["path"] == "knowledge/guides/Подключение.md"
    assert result["revision"] == item["revision"] and len(factory.calls) == calls
    assert runtime.search(types=["knowledge"], query="conditions", d_max=2)["results"][0]["path"] == result["path"]
    runtime.close()


def test_sources_are_generic_returned_on_every_read_not_executed(tmp_path):
    runtime = context(tmp_path, "one")
    body = '# Source\n```yaml\nsources:\n- reference: "pcw:beta/card#section"\n  instruction: "Read the relevant condition only"\n- reference: "saved query 18"\n  instruction: "Run its read-only query and extract the count"\n```\n'
    item = save(runtime, body)
    for _ in range(2):
        assert len(runtime.read(material_id=item["id"])["sources"]) == 2
    runtime.close()
    migrated = migrate_sources('```yaml\nrequired_sources:\n- url: https://example.com\n  extract: Version\n```')
    assert declarations(migrated) == [{"reference": "https://example.com", "instruction": "Version"}]
    assert migrate_sources(migrated) == migrated


def legacy(root):
    old = PCWRuntime(root, "old", provider_factory=Factory())
    item = old.create('# Known fact\n```yaml\nrequired_sources:\n- url: https://example.com\n  extract: Current number\n```\n')
    (old.root / "INSTRUCTIONS.md").write_text("Custom exact user rules\n", encoding="utf-8", newline="")
    (old.root / "CURRENT.md").write_text("Keep ongoing work")
    original = (old.root / item["path"]).read_bytes().decode("utf-8")
    old.close()
    return item, original


@pytest.mark.parametrize("failure", ["file", "index"])
def test_migrate_retry_and_real_index(tmp_path, failure):
    item, original = legacy(tmp_path)
    runtime = CycleRuntime(tmp_path, "old", provider_factory=Factory())
    plan = runtime.upgrade_plan()
    if failure == "file":
        write = runtime.engine._write
        def interrupted(path, text):
            write(path, text)
            if path.name == "Known-fact.md":
                raise OSError("lost acknowledgement")
        with patch.object(runtime.engine, "_write", side_effect=interrupted), pytest.raises(OSError):
            runtime.upgrade_apply(plan["revision"], automatic=True)
    else:
        with patch.object(runtime.engine, "_build_rows", side_effect=ValueError("offline")):
            assert runtime.upgrade_apply(plan["revision"], automatic=True)["status"] == "index_pending"
    retry = runtime.upgrade_plan()
    assert retry["resume_revision"] == plan["revision"] and retry["ready"]
    assert runtime.upgrade_apply(plan["revision"], automatic=True)["status"] == "complete"
    actual = runtime.read(material_id=item["id"])
    assert actual["path"] == "knowledge/Known-fact.md" and len(actual["sources"]) == 1
    assert actual["index_status"] == "indexed"
    assert runtime.search(types=["knowledge"], query="known", d_max=2)["results"][0]["id"] == item["id"]
    receipt = json.loads(runtime._migration_receipt_path().read_text())
    assert (Path(receipt["backup"]) / item["path"]).read_text() == original
    assert runtime.instructions_read()["content"] == "Custom exact user rules\n"
    assert not (runtime.engine.root / "CURRENT.md").exists()
    assert any("Keep ongoing work" in m.content for m in runtime.engine.materials.values() if m.metadata.get("type") == "task")
    assert runtime.upgrade_plan()["compatible"]
    runtime.close()


def test_missing_yaml_or_legacy_sources_detected_with_current_marker(tmp_path):
    runtime = context(tmp_path, "one")
    item = save(runtime, "# Known\nFact")
    path = runtime.engine.root / item["path"]
    path.write_text("# Known\nFact", encoding="utf-8", newline="")
    assert not runtime.workspace_inspect()["compatible"]
    with pytest.raises(PCWError, match="pcw-upgrade"):
        runtime.search(types=["knowledge"], query="fact")
    plan = runtime.upgrade_plan()
    assert runtime.upgrade_apply(plan["revision"], automatic=True)["status"] == "complete"
    assert runtime.read(material_id=item["id"])["content"] == "# Known\nFact"
    runtime.close()


def test_old_source_syntax_is_normalized_before_review(tmp_path):
    runtime = context(tmp_path, "one")
    cycle = runtime.startup("legacy-author")["cycle_id"]
    p = runtime.proposal_prepare(cycle, expected_task_revision=runtime.status(cycle)["task_revision"], changes=[{"key": "a", "content": "# Fact\n```yaml\nrequired_sources:\n- url: file:///tmp/example\n  extract: Specific field\n```\n",
        "description": "Explains the fact's source dependency and the specific field that must be obtained before use."}])
    shown = p["review_changes"][0]["body_changes"][0]["after"]
    assert "sources:" in shown and "reference:" in shown and "instruction:" in shown
    assert "required_sources:" not in shown
    assert runtime.list()["total"] == 1
    runtime.proposal_apply(cycle, p["proposal_id"], p["revision"])
    assert runtime.read(material_id=p["created_ids"]["a"])["content"] == shown
    runtime.close()


def test_source_migration_retains_comments_and_scalar_text():
    old = '# Fact\r\n```yaml\r\nrequired_sources: # only the selected release\r\n  - url: "https://example.com" # canonical reference\r\n    extract: >-\r\n      Keep the supported version\r\n      and its conditions.\r\n```\r\nRest unchanged.\r\n'
    expected = old.replace("required_sources:", "sources:").replace("- url:", "- reference:").replace("    extract:", "    instruction:")
    assert migrate_sources(old) == expected


def test_descriptive_names_without_heading_and_multibyte_limit():
    assert topic_name("Connection conditions\nFull explanation", "abc.md") == "Connection-conditions.md"
    assert len(topic_name("# " + "知" * 110, "abc.md").encode("utf-8")) <= 163


def test_move_category_and_provider_rebuild_preserve_all_contexts(tmp_path):
    factory = Factory()
    a, b = context(tmp_path, "alpha", factory), context(tmp_path, "beta", factory)
    try:
        one = save(a, "# One\nFact")
        two = save(b, "# Two\nFact")
        before = len(factory.calls)
        old = a.engine.root / one["path"]
        old.rename(b.engine.root / one["path"])
        b.engine.reconcile()
        assert len(factory.calls) == before
        assert a.search(types=["knowledge"], query="fact", d_max=2)["results"] == []
        assert {r["id"] for r in b.search(types=["knowledge"], query="fact", d_max=2, N=100)["results"]} == {one["id"], two["id"]}
        save(a, "# Three\nFact")
        config = copy.deepcopy(a.engine.active["config"])
        config["profile"]["revision"] = "a-different-model-revision"
        result = a.engine.switch_embeddings(config, a.engine.active["search"])
        assert result["status"] == "ok" and result["reembedded"]
        assert len(a.search(types=["knowledge"], query="fact", all_categories=True, N=100, d_max=2)["results"]) == 3
        assert b.search(types=["knowledge"], query="fact", d_max=2)["pending_count"] == 0
    finally:
        a.close(); b.close()


def test_concurrent_context_writers_keep_both_categories(tmp_path):
    a, b = context(tmp_path, "alpha"), context(tmp_path, "beta")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save, a, "# One\nFact"), pool.submit(save, b, "# Two\nFact")]
            ids = {f.result(timeout=30)["id"] for f in futures}
        assert {r["id"] for r in a.search(types=["knowledge"], query="fact", all_categories=True, N=100, d_max=2)["results"]} == ids
    finally:
        a.close(); b.close()


def test_duplicate_yaml_ids_and_paths_are_rejected(tmp_path):
    a, b = context(tmp_path, "alpha"), context(tmp_path, "beta")
    try:
        one = save(a, "# One\nFact")
        (b.engine.knowledge / "Different-name.md").write_bytes((a.engine.root / one["path"]).read_bytes())
        with pytest.raises(PCWError, match="Duplicate YAML"):
            a.read(material_id=one["id"])
        (b.engine.knowledge / "Different-name.md").unlink()
        cycle = a.startup("collision")["cycle_id"]
        with pytest.raises(PCWError, match="destination path"):
            a.proposal_prepare(cycle, expected_task_revision=a.status(cycle)["task_revision"], changes=[{"key": "x", "path": "same.md", "content": "X", "description": "Records the first independent fact used to check destination collisions."},
                                      {"key": "y", "path": "same.md", "content": "Y", "description": "Records the second independent fact used to check destination collisions."}])
    finally:
        a.close(); b.close()


@pytest.mark.parametrize("cleanup_required", [False, True])
def test_legacy_partial_proposal_resumes_after_migration(tmp_path, monkeypatch, cleanup_required):
    from workspace.configuration import WORKSPACE_CREATION_DEFAULTS
    from test_workspace_v3 import legacy_with_work

    monkeypatch.setitem(WORKSPACE_CREATION_DEFAULTS, "cleanup_approval_required", cleanup_required)
    runtime, cycle, proposal, _, _ = legacy_with_work(tmp_path)
    entry = runtime._records()["cycles"][cycle]["proposals"][proposal["proposal_id"]]
    raw = runtime._load_plan(cycle, proposal["proposal_id"], entry)
    # Crash after the first durable file, before indexing/acknowledgement.
    ident, change = next(iter(raw["entries"].items()))
    runtime.engine._write(runtime.engine.root / change["after"]["path"], change["after"]["text"])
    records = runtime._records()
    records["cycles"][cycle]["proposals"][proposal["proposal_id"]]["state"] = "partial"
    runtime._persist(records)
    plan = runtime.upgrade_plan()
    assert plan["ready"]
    assert runtime.upgrade_apply(plan["revision"], automatic=True)["status"] == "complete"
    assert runtime.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"], "Retry approved write")["status"] == "applied"
    assert runtime.read(material_id=ident)["path"] == "knowledge/Contract-setup.md"
    assert runtime._policies()["cleanup_approval_required"] is cleanup_required
    cleanup = runtime.finish(cycle)
    if cleanup_required:
        assert cleanup["status"] == "blocked" and cleanup["tmp_preserved"]
        assert runtime.finish(cycle, confirmation="Approve removal of the reviewed temporary files",
                              expected_revision=cleanup["cleanup_revision"])["status"] == "closed"
    else:
        assert cleanup["status"] == "closed" and cleanup["tmp_cleared"]
    runtime.close()
