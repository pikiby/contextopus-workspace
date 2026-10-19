from workspace_support import install_workspace, workspace_module
import pytest
import copy
from contextopus.core.rag import RagIndex
from contextopus.core.storage import StorageLayout
from contextopus.core.resources import ProcessResources
from contextopus.core.embeddings import default_config
from contextopus.core.chunking import default_chunking
from test_shared_v4 import context, save
from test_runtime import Factory


def test_profile_switch_stale_generic_reader(tmp_path):
    f = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    w = context(tmp_path, "alpha", f)
    save(w, "# answer\ntext")
    with ProcessResources(f) as res:
        idx = RagIndex(StorageLayout(tmp_path), res)
        idx.search("answer")
        cfg = copy.deepcopy(default_config())
        cfg["profile"]["model"] = "changed-model"
        result = w.engine.switch_embeddings(
            cfg,
            {
                "N": 20,
                "d_max": 1,
                "min_jump": 0.02,
                "multiplier": 3,
                "metric": "cosine",
            },
        )
        assert result["status"] == "ok", result
        idx.search("answer")
    w.close()


def test_collection_move_generic_search(tmp_path):
    f = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    a = context(tmp_path, "alpha", f)
    b = context(tmp_path, "beta", f)
    one = save(a, "# answer\ntext")
    with ProcessResources(f) as res:
        idx = RagIndex(StorageLayout(tmp_path), res)
        idx.search("answer")
        old = a.engine.root / one["path"]
        new = b.engine.knowledge / old.name
        old.rename(new)
        b.engine.reconcile()
        rows = idx.search("answer", module="workspace", collection="beta", distance=2)
        assert rows, idx.read_rows(
            b.engine.table, ["workspace", "collection", "object_id"]
        )
    a.close()
    b.close()


def test_contextless_app_close_leaves_cached_runtime_closed_lease(tmp_path):
    from contextopus.startup import Application

    f = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    w = context(tmp_path, "alpha", f)
    save(w, "# answer\ntext")
    w.close()
    res = ProcessResources(f)
    app = Application(tmp_path, resources=res, discover=False)
    install_workspace(tmp_path, app=app)
    app._owns_resources = True
    result = app.apply_profile(default_config(), default_chunking())
    assert result["status"] == "ok", result
    app.close()
    with Application(
        tmp_path, "alpha", resources=ProcessResources(f), discover=False
    ) as nextapp:
        nextapp.query("workspace.search", {"query": "answer", "d_max": 2.0})


def test_public_search_applies_context_settings_and_request_override(tmp_path):
    from contextopus.startup import Application
    from contextopus.core.rag import DocumentKey, IndexDocument

    factory = Factory()
    with ProcessResources(factory) as resources:
        app = Application(tmp_path, resources=resources, discover=False)
        for n in range(10):
            app.index.index(
                IndexDocument(
                    DocumentKey("second", "alpha", str(n)),
                    "r1",
                    "same searchable answer",
                    f"second://{n}",
                )
            )

        def setting(changes, **scope):
            preview = app.query("core.settings_preview", {"changes": changes, **scope})
            return app.execute(
                "core.settings_apply", {"preview_revision": preview["preview_revision"]}
            )

        setting({"searchN": 2, "module": "second", "context": "alpha", "distance": 2.0})
        assert len(app.query("core.search", {"query": "same"})["results"]) == 2
        setting({"searchN": 4}, module="second")
        assert len(app.query("core.search", {"query": "same"})["results"]) == 4
        setting({"searchN": 6}, module="second", context="alpha")
        selected = app.query("core.search", {"query": "same"})
        assert selected["effective"]["limit"] == 6
        assert len(selected["results"]) == 6
        explicit = app.query(
            "core.search",
            {"query": "same", "module": "second", "collection": "alpha", "limit": 1},
        )
        assert len(explicit["results"]) == 1
        app.close()


@pytest.mark.parametrize("change", ["edit", "delete", "invalidate"])
def test_core_search_workspace_source_freshness_without_cycle_or_repair(
    tmp_path, change
):
    import json
    from contextopus.startup import Application
    from contextopus.core.rag import DocumentKey, IndexDocument

    factory = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    workspace = context(tmp_path, "alpha", factory)
    material = save(workspace, "# Original\nshared searchable answer")
    with ProcessResources(factory) as resources:
        app = Application(tmp_path, resources=resources, discover=False)
        install_workspace(tmp_path, app=app)
        app.index.index(
            IndexDocument(
                DocumentKey("second", "notes", "independent"),
                "r1",
                "shared searchable answer",
                "second://1",
            )
        )
        first = app.query("core.search", {"query": "answer", "distance": 2.0})[
            "results"
        ]
        assert {row["key"][0] for row in first} == {"workspace", "second"}
        version = workspace.engine.table.version
        if change == "edit":
            path = workspace.engine.root / material["path"]
            path.write_text(path.read_text() + "\nExternally changed body.\n")
        elif change == "delete":
            (workspace.engine.root / material["path"]).unlink()
        else:
            (workspace.control.root / "task-ledger.json").write_text(
                json.dumps(
                    {
                        "tasks": {
                            "t": {
                                "assignments": {
                                    "a": {
                                        "affected_materials": {
                                            material["id"]: material["revision"]
                                        },
                                        "discrepancy_reason": "Known incorrect",
                                    }
                                }
                            }
                        }
                    }
                )
            )
        found = app.query("core.search", {"query": "answer", "distance": 2.0})[
            "results"
        ]
        assert "second" in {row["key"][0] for row in found}
        assert material["id"] not in {row["key"][2] for row in found}
        assert workspace.engine.table.version == version
        app.close()
    workspace.close()
