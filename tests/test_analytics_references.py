"""Exact replay, unknown historical evidence and body-free new journal records."""
from unittest.mock import patch

import pytest

from test_runtime import Factory
from test_workspace_v3 import create
from pcw_runtime.cycles import CycleRuntime
from pcw_runtime.server import dispatch
from pcw_runtime.analytics import EventStore


@pytest.fixture
def workspace(tmp_path):
    runtime = CycleRuntime(tmp_path, "project", provider_factory=Factory())
    create(runtime)
    with patch("pcw_runtime.server.runtime", return_value=runtime):
        yield runtime, lambda op, **args: dispatch(str(tmp_path), "project", op, args)
    runtime.close()


def save(call, cycle, text, **extra):
    proposal = call("proposal_prepare", cycle_id=cycle,
                    expected_task_revision=call("status", cycle_id=cycle)["task_revision"],
                    changes=[{"description": "Verified behavior used to test historical replay and policy evidence", "key": "one", "content": text, **extra}])
    call("proposal_apply", cycle_id=cycle, proposal_id=proposal["proposal_id"], expected_revision=proposal["revision"])
    return proposal["created_ids"]["one"]


def test_new_journal_references_replay_without_duplicate_markdown(workspace):
    runtime, call = workspace
    cycle = call("startup", request_id="references")["cycle_id"]
    text = "# Selected evidence\nUnique-content-marker-857201; precise conditions."
    ident = save(call, cycle, text)
    returned = call("read", material_id=ident)
    event_id = returned["analytics"]["event_id"]
    event = runtime.analytics_read(event_id)
    assert event["journal_format"] == 2 and event["versions"] == []
    assert event["references"][0]["material_id"] == ident
    assert event["references"][0]["status"] == "versioned"
    assert event["result"]["value"]["content"]["retained"] is False
    assert runtime.analytics_reference_read(event_id)["text"] == text
    page = runtime.analytics_reference_read(event_id, offset=2, max_chars=5)
    assert page["text"] == text[2:7] and page["next_offset"] == 7
    with EventStore(runtime.engine.work_root).connect() as db:
        assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM observed").fetchone()[0] == 0
        assert not any("Unique-content-marker-857201" in row[0] for row in db.execute("SELECT text FROM blobs"))


def test_read_replays_old_rules_and_content_after_later_change(workspace):
    runtime, call = workspace
    cycle = call("startup", request_id="eras")["cycle_id"]
    ident = save(call, cycle, "# Before\nOriginal recorded fact.")
    read = call("read", material_id=ident)
    first = runtime.analytics_read(read["analytics"]["event_id"])
    instructions = runtime.instructions_read()
    amended = instructions["content"] + "\nNew rule applies from now.\n"
    # Edit through the authorized proposal path; the event should retain prior basis.
    rules_cycle = runtime.startup("new-rules", purpose="instructions")["cycle_id"]
    prepared = runtime.instructions_prepare(rules_cycle, amended, instructions["revision"], "Add the new rule")
    call("instructions_apply", cycle_id=rules_cycle, proposal_id=prepared["proposal_id"],
         expected_revision=prepared["revision"], confirmation="Apply")
    assert runtime.analytics_reference_read(read["analytics"]["event_id"], reference_index=-1)["text"] == instructions["content"]
    second = call("read", material_id=ident)
    latest = runtime.analytics_read(second["analytics"]["event_id"])
    assert latest["instructions_revision"] != first["instructions_revision"]
    assert first["procedures_revision"] == latest["procedures_revision"]
    selected = runtime.analytics_report(instruction_revision=first["instructions_revision"])
    assert set(selected["groups"]["instruction_revision"]) == {first["instructions_revision"]}


def test_cross_context_read_keeps_owner_and_active_rules(workspace):
    runtime, call = workspace
    other = CycleRuntime(runtime.engine.work_root, "other", provider_factory=Factory())
    create(other)
    cycle = other.startup("other-cycle")["cycle_id"]
    proposal = other.proposal_prepare(cycle, [{"description": "Verified behavior used to test historical replay and policy evidence", "key": "other", "content": "# Other context\nForeign fact."}], expected_task_revision=other.status(cycle)["task_revision"])
    other.proposal_apply(cycle, proposal["proposal_id"], proposal["revision"])
    read = call("read", material_id=proposal["created_ids"]["other"])
    event = runtime.analytics_read(read["analytics"]["event_id"])
    assert event["workspace"] == "project" and event["references"][0]["workspace"] == "other"
    assert event["instructions_ref"]["workspace"] == "project"
    assert runtime.analytics_reference_read(read["analytics"]["event_id"])["text"].endswith("Foreign fact.")
    other.close()


def test_historical_read_page_replays_its_original_range(workspace):
    runtime, call = workspace
    cycle = call("startup", request_id="history-page")["cycle_id"]
    ident = save(call, cycle, "# History page\nExact source for partial history read.")
    returned = call("history_read", material_id=ident, offset=8, max_chars=17)
    source = returned["materials"][0]["text"]
    replay = runtime.analytics_reference_read(returned["analytics"]["event_id"])
    assert replay["text"] == source


def test_dirty_git_read_records_unversioned_reference_without_markdown_copy(workspace):
    runtime, call = workspace
    # New exact read bytes differ from Git. The journal records uncertainty, not HEAD.
    cycle = call("startup", request_id="dirty")["cycle_id"]
    ident = save(call, cycle, "# Kept\nPreviously versioned text.")
    item = runtime.read(material_id=ident)
    path = runtime.engine.root / item["path"]
    path.write_text(path.read_text().replace("Previously versioned text", "Uncommitted-new-evidence-54983"))
    read = call("read", material_id=ident)
    event_id = read["analytics"]["event_id"]
    event = runtime.analytics_read(event_id)
    assert event["references"][0]["status"] == "unversioned"
    assert runtime.analytics_reference_read(event_id)["status"] == "unavailable"
    with EventStore(runtime.engine.work_root).connect() as db:
        assert not any("Uncommitted-new-evidence-54983" in row[0] for row in db.execute("SELECT text FROM blobs"))


def test_git_history_disabled_read_has_no_replay_or_markdown_copy(tmp_path):
    runtime = CycleRuntime(tmp_path, "no-history", provider_factory=Factory())
    create(runtime, git_history_enabled=False)
    try:
        with patch("pcw_runtime.server.runtime", return_value=runtime):
            def call(op, **args):
                return dispatch(str(tmp_path), "no-history", op, args)
            cycle = call("startup", request_id="no-git-history")["cycle_id"]
            marker = "Disabled-history-evidence-927416"
            ident = save(call, cycle, "# No Git history\n" + marker)
            read = call("read", material_id=ident)
        event_id = read["analytics"]["event_id"]
        event = runtime.analytics_read(event_id)
        assert event["references"][0]["status"] == "no_history"
        assert runtime.analytics_reference_read(event_id)["status"] == "unavailable"
        assert event["result"]["value"]["content"]["retained"] is False
        with EventStore(runtime.engine.work_root).connect() as db:
            assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM observed").fetchone()[0] == 0
            assert not any(marker in row[0] for row in db.execute("SELECT text FROM blobs"))
    finally:
        runtime.close()


def test_reference_pagination_and_replay_beyond_default_page(workspace):
    runtime, call = workspace
    cycle = call("startup", request_id="reference-pages")["cycle_id"]
    text = "# Paged references\nThe same verified source at the final ordinal."
    ident = save(call, cycle, text)
    returned = call("read", material_id=ident)
    event_id = returned["analytics"]["event_id"]
    # Reuse one verified Git ref to exercise persisted reference paging cheaply.
    # The final ordinal must be outside the default fifty-reference page.
    with EventStore(runtime.engine.work_root).connect() as db:
        reference = db.execute("SELECT reference FROM event_references WHERE event_id=? AND ordinal=0", (event_id,)).fetchone()[0]
        db.executemany("INSERT INTO event_references VALUES (?,?,?)",
                       [(event_id, ordinal, reference) for ordinal in range(2, 53)])
    first = runtime.analytics_read(event_id)
    assert len(first["references"]) == 50
    assert first["references_count"] == 53 and first["next_offset"] == 50
    penultimate = runtime.analytics_read(event_id, offset=first["next_offset"], limit=2)
    assert [ref["reference_index"] for ref in penultimate["references"]] == [50, 51]
    assert penultimate["next_offset"] == 52
    final = runtime.analytics_read(event_id, offset=penultimate["next_offset"], limit=1)
    assert [ref["reference_index"] for ref in final["references"]] == [52]
    assert final["next_offset"] is None
    assert runtime.analytics_reference_read(event_id, reference_index=52)["text"] == text
