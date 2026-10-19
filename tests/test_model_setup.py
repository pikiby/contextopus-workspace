from workspace_support import install_workspace, workspace_module
from types import SimpleNamespace
import copy
import pytest
from contextopus.core import model_setup
from contextopus.core.embeddings import default_config, LocalProvider
from contextopus.core.chunking import default_chunking


def torch_probe(cuda=False, count=0, mps=False):
    return lambda: SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: cuda,
            device_count=lambda: count,
            get_device_name=lambda index: f"Fake GPU {index}",
        ),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )














def test_application_setup_uses_real_lance_and_actual_profile_rebuild(
    tmp_path, monkeypatch
):
    import json
    from contextopus.startup import Application
    from contextopus.core.resources import ProcessResources
    from test_runtime import Factory
    from test_shared_v4 import context, save

    factory = Factory()
    (tmp_path / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    workspace = context(tmp_path, "alpha", factory)
    material = save(workspace, "# Model setup\nunchanged source text")
    prior = copy.deepcopy(workspace.engine.active)
    workspace.close()
    hardware = model_setup.detect_hardware(torch_probe(True, 1))
    monkeypatch.setattr(model_setup, "detect_hardware", lambda probe=None: hardware)
    with ProcessResources(factory) as resources:
        with Application(
            tmp_path, context="alpha", resources=resources, discover=False
        ) as app:
            install_workspace(tmp_path, app=app)
            plan = app.query("core.model_plan", {"device": "cuda:0"})
            assert plan["config"]["profile"] == prior["config"]["profile"]
            assert app.active_profile() == prior
            result = app.execute("core.model_apply", {"plan": plan})
            assert result["status"] == "ok"
            active = app.active_profile()
            assert active["config"]["provider"]["device"] == "cuda:0"
            assert active["table"] != prior["table"]
            import lancedb

            database = lancedb.connect(str(app.layout.rag / "lancedb"))
            rows = database.open_table(active["table"]).search().limit(None).to_list()
            assert any(
                row["material_id"] == material["id"]
                and "unchanged source text" in row["text"]
                for row in rows
            )
            assert any(
                instance.config["provider"]["device"] == "cuda:0"
                for instance in factory.instances
            )
            # Failed replacement leaves the durable profile and rows usable.
            bad = copy.deepcopy(active["config"])
            bad["profile"]["revision"] = "fail"
            failed_plan = app.query("core.model_plan", {"config": bad, "device": "cpu"})
            failed = app.execute("core.model_apply", {"plan": failed_plan})
            assert failed["status"] == "failed"
            assert app.active_profile() == active


def test_http_model_setup_rebuilds_shared_real_lance_profile(tmp_path):
    import json
    import os
    from pathlib import Path
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    from test_runtime import Factory
    from test_shared_v4 import context, save
    from contextopus.integrations.remote import RemoteApplication

    root = tmp_path / "data"
    root.mkdir()
    (root / "modules/workspace/workspace_contexts").mkdir(parents=True, exist_ok=True)
    workspace = context(root, "alpha", Factory())
    material = save(workspace, "# HTTP model setup\nsource survives device switch")
    original = copy.deepcopy(workspace.engine.active)
    workspace.close()
    install_workspace(root)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    code = """
import sys
from types import SimpleNamespace
from contextopus.core import model_setup
from contextopus.core.resources import ProcessResources
from contextopus.interfaces.mcp import server
from test_runtime import Factory
factory=Factory()
server.ProcessResources=lambda:ProcessResources(factory)
probe=lambda:SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,device_count=lambda:1,get_device_name=lambda index:'Fake CUDA'),backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda:False)))
hardware=model_setup.detect_hardware(probe)
model_setup.detect_hardware=lambda probe=None:hardware
server.run(sys.argv[1],port=int(sys.argv[2]))
"""
    repository = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            map(
                str,
                [
                    Path(__import__("contextopus.startup", fromlist=["Application"]).__file__).resolve().parents[1],
                    repository / "tests",
                    repository / "plugins/persistent-context-workspace",
                ],
            )
        ),
    }
    remote = None
    with (tmp_path / "http.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(root), str(port)],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path / "http.log").read_text())
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=0.2
                    ):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("isolated model MCP server did not start")
            remote = RemoteApplication(
                root, context="alpha", endpoint=f"http://127.0.0.1:{port}/mcp"
            )
            assert remote.query("core.model_hardware")["cuda"]["available"] is True
            plan = remote.query("core.model_plan", {"device": "cuda:0"})
            assert plan["config"]["profile"] == original["config"]["profile"]
            assert remote.execute("core.model_apply", {"plan": plan})["status"] == "ok"
            active = json.loads((root / "core/rag/active.json").read_text())
            assert active["config"]["provider"]["device"] == "cuda:0"
            import lancedb

            table = lancedb.connect(str(root / "core/rag/lancedb")).open_table(
                active["table"]
            )
            assert any(
                row["material_id"] == material["id"]
                for row in table.search().limit(None).to_list()
            )
        finally:
            if remote:
                remote.close()
            process.terminate()
            process.wait(timeout=10)
