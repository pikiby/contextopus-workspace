"""Opt-in integration through Codex itself, including a two-turn human-approval scenario.

PCW_TEST_CODEX=1 requires a locally installed matching PCW candidate, unless
PCW_TEST_SOURCE=1 selects an isolated source-backed MCP and temporary local skills.
It also runs a normal question and a separate approval through the configured model (inference costs apply).
PCW_TEST_QWEN=1 additionally exercises the already provisioned local model.
Only temporary knowledge is changed. No plugin/config installation is done here.

Before the host starts, the installed candidate is compared with the source
package, including the skill and references. A working old cache is not
release evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest
import uuid


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "persistent-context-workspace"
PLUGIN_ID = "persistent-context-workspace@persistent-context-workspace"


class CodexHost:
    def __init__(self, environment, work, notification_sink=None, server_request_sink=None,
                 server_decision_sink=None, server_request_handler=None, notification_observer=None,
                 source_root=None):
        settings_path = Path.home() / ".codex" / "config.toml"
        settings = tomllib.loads(settings_path.read_text()) if settings_path.exists() else {}
        names = set(settings.get("plugins", {})) | {PLUGIN_ID}
        plugin_policy = "{" + ",".join(
            json.dumps(name) + "={enabled=" + ("true" if name == PLUGIN_ID and source_root is None else "false") + "}"
            for name in sorted(names)
        ) + "}"
        self.messages = queue.Queue()
        self.notifications = []
        self.notification_sink = Path(notification_sink) if notification_sink else None
        self.server_request_sink = Path(server_request_sink) if server_request_sink else None
        self.server_decision_sink = Path(server_decision_sink) if server_decision_sink else None
        self.server_request_handler = server_request_handler
        self.notification_observer = notification_observer
        self.notification_observer_errors = []
        self._notifications_changed = threading.Condition()
        self._write_lock = threading.Lock()
        self.request_id = 0
        self.errors = tempfile.TemporaryFile(mode="w+")
        mcp_policy = "{}" if source_root is None else '{pcw={command="sh",args=[' + json.dumps(str(source_root / "scripts/launch.sh")) + ']}}'
        self.process = subprocess.Popen(
            ["codex", "-c", "mcp_servers=" + mcp_policy, "-c", "plugins=" + plugin_policy,
             "-c", "features.apps=false", "app-server", "--stdio"],
            env=environment, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.errors, text=True, bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()

    @staticmethod
    def _append_jsonl(path, value):
        """Append one complete, synced trace record before handling the next event."""
        if not path:
            return
        with path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            sink.flush()
            os.fsync(sink.fileno())

    def _server_request_response(self, request):
        """Return a narrow response; callers must opt in to any approval."""
        if self.server_request_handler is None:
            return {"error": {"code": -32601, "message": "Unexpected server request in PCW host test"}}
        try:
            response = self.server_request_handler(request)
        except Exception as error:  # The app-server must not be left waiting on test code.
            return {"error": {"code": -32603, "message": "PCW request policy failed: " + str(error)}}
        if not isinstance(response, dict) or not ("result" in response or "error" in response):
            return {"error": {"code": -32603, "message": "PCW request policy returned no JSON-RPC response"}}
        return response

    def _handle_server_request(self, request):
        self._append_jsonl(self.server_request_sink, request)
        response = self._server_request_response(request)
        message = {"id": request["id"], **response}
        self._append_jsonl(self.server_decision_sink, message)
        self.send(message)

    def _read(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                if "method" in message and "id" not in message:
                    with self._notifications_changed:
                        self.notifications.append(message)
                        self._append_jsonl(self.notification_sink, message)
                        if self.notification_observer:
                            try:
                                # The notification is already durable in the
                                # raw trace before an observer can act on it.
                                self.notification_observer(message, len(self.notifications) - 1)
                            except Exception as error:
                                self.notification_observer_errors.append(str(error))
                        self._notifications_changed.notify_all()
                elif "method" in message and "id" in message:
                    self._handle_server_request(message)
                else:
                    self.messages.put(message)
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(EOFError("Codex app-server closed stdout"))

    def send(self, message):
        with self._write_lock:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def call(self, method, params, timeout=60):
        self.request_id += 1
        request_id = self.request_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise TimeoutError("Codex did not answer " + method) from error
            if isinstance(message, Exception):
                self.errors.seek(0)
                raise RuntimeError(str(message) + "\n" + self.errors.read()[-4000:]) from message
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise AssertionError({"method": method, "error": message["error"]})
                return message["result"]

    def notification_snapshot(self):
        """Return observed server notifications without consuming trace evidence."""
        with self._notifications_changed:
            return list(self.notifications)

    def stderr_tail(self):
        """Read a diagnostic copy before close without exposing configuration."""
        self.errors.seek(0)
        return self.errors.read()[-8000:]

    def wait_notification(self, predicate, timeout=60):
        """Wait for a server notification while retaining it in the trace."""
        deadline = time.monotonic() + timeout
        with self._notifications_changed:
            while True:
                for notification in self.notifications:
                    if predicate(notification):
                        return notification
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Codex did not emit the expected notification")
                self._notifications_changed.wait(remaining)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.errors.close()


@unittest.skipUnless(os.environ.get("PCW_TEST_CODEX") == "1", "Set PCW_TEST_CODEX=1 for real Codex host integration")
class CodexHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("codex"):
            raise AssertionError("PCW_TEST_CODEX requires the actual Codex CLI")
        environment = dict(os.environ)
        for name in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PCW_PYTHON", "PCW_DEPENDENCIES"):
            environment.pop(name, None)
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
        cls.source_mode = os.environ.get("PCW_TEST_SOURCE") == "1"
        if cls.source_mode:
            cls.launcher = PLUGIN / "scripts/launch.sh"
        else:
            result = subprocess.run(["codex", "mcp", "get", "pcw", "--json"], env=environment,
                                    text=True, capture_output=True, timeout=30, check=True)
            cls.configuration = json.loads(result.stdout)
            cls.transport = cls.configuration["transport"]
            assert cls.transport["command"] == "sh", cls.transport
            assert len(cls.transport["args"]) == 1, cls.transport
            cls.launcher = Path(cls.transport["args"][0])
        assert cls.launcher.is_absolute() and cls.launcher.is_file()
        assert "${" not in str(cls.launcher)
        installed = json.loads((cls.launcher.parents[1] / "plugin.json").read_text())
        source = json.loads((PLUGIN / "plugin.json").read_text())
        assert installed["name"] == source["name"]
        assert installed["version"].split("+")[0] == source["version"].split("+")[0]
        cls.installed_root = cls.launcher.parents[1]
        cls._assert_candidate_parity()
        cls.temporary = tempfile.TemporaryDirectory(prefix="pcw actual codex ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = cls.temporary.name
        cls.skill_root = cls.installed_root / "skills"
        cls.skill_prefix = "persistent-context-workspace:"
        if cls.source_mode:
            cls.skill_root = Path(cls.work) / ".agents/skills"
            # Keep packaged relative references (notably the creation template)
            # valid. Copying only skills made the model search for missing files.
            shutil.copytree(PLUGIN, cls.skill_root.parent,
                            ignore=shutil.ignore_patterns("__pycache__", ".venv", "*.egg-info", "build"))
            cls.skill_prefix = ""
        trace = {}
        if os.environ.get("PCW_TEST_TRACE_DIR"):
            trace_root = Path(os.environ["PCW_TEST_TRACE_DIR"])
            trace_root.mkdir(parents=True, exist_ok=True)
            prefix = uuid.uuid4().hex
            trace = {name: trace_root / (prefix + "-" + name + ".jsonl")
                     for name in ("notification_sink", "server_request_sink", "server_decision_sink")}
        cls.host = CodexHost(environment, cls.work, source_root=PLUGIN if cls.source_mode else None, **trace)
        cls.addClassCleanup(cls.host.close)
        cls.host.call("initialize", {
            "clientInfo": {"name": "pcw_host_regression", "version": "1"},
            "capabilities": {"experimentalApi": True},
        })
        cls.host.send({"method": "initialized", "params": {}})
        thread = cls.host.call("thread/start", {
            "cwd": cls.work, "ephemeral": True, "approvalPolicy": "never", "sandbox": "workspace-write",
        })
        cls.thread_id = thread["thread"]["id"]
        if cls.source_mode:
            inventory = cls.host.call("skills/list", {"cwds": [cls.work], "forceReload": True})
            expected_path = cls.skill_root / "pcw/SKILL.md"
            selected = [skill for entry in inventory["data"] for skill in entry["skills"]
                        if Path(skill["path"]) == expected_path]
            assert len(selected) == 1, {"expected_path": str(expected_path), "matches": selected}
            # Hosts that recognize the copied plugin manifest namespace even
            # repo-scoped skills. Bind to the actual candidate path, not an
            # installed skill that happens to have the same short name.
            assert selected[0]["name"] in {"pcw", "persistent-context-workspace:pcw"}
            cls.skill_prefix = selected[0]["name"][:-len("pcw")]
        described = cls.host.call("mcpServer/tool/call", {
            "threadId": cls.thread_id, "server": "pcw", "tool": "pcw_query",
            "arguments": {"operation": "describe"},
        })
        assert not described.get("isError"), described
        catalog = described.get("structuredContent") or json.loads(described["content"][0]["text"])
        cls.operation_tools = {name: tool for tool, names in catalog["operations"].items() for name in names}

    def setUp(self):
        if self._testMethodName == "test_agent_creates_context_through_compact_interface":
            return  # The agent, not the harness, must create this workspace.
        self.tool("workspace_create", request_id="create-" + self._testMethodName,
                  instructions_content=(Path(__file__).resolve().parents[1] / "package/workspace/default_instructions.md").read_text(),
                  project_content="# Host integration fixture\nTemporary PCW test workspace.\n",
                  user_request="Create this temporary test context with the supplied settings")
        opened = self.tool("startup", request_id="fixture-" + uuid.uuid4().hex)
        self.cycle_id = opened["cycle_id"]

    def tool(self, name, **arguments):
        if name not in {"startup", "workspace_create"} and hasattr(self, "cycle_id"):
            arguments.setdefault("cycle_id", self.cycle_id)
        result = self.host.call("mcpServer/tool/call", {
            "threadId": self.thread_id, "server": "pcw", "tool": self.operation_tools[name],
            "arguments": {"operation": name, "work_root": self.work, "workspace": self._testMethodName, "arguments": arguments},
        })
        self.assertFalse(result.get("isError"), result)
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        return json.loads(next(part["text"] for part in result["content"] if part["type"] == "text"))

    def save_fixture(self, content, material_id=None, expected_revision=None, links=None):
        change = {"material_id": material_id, "expected_revision": expected_revision} if material_id else {"key": "fixture"}
        change["content"] = content
        if links is not None:
            change["links"] = links
        proposal = self.tool("proposal_prepare", changes=[change])
        result = self.tool("proposal_apply", proposal_id=proposal["proposal_id"],
                           expected_revision=proposal["revision"], confirmation="Apply this test fixture")
        return result["materials"][0]

    @classmethod
    def _candidate_files(cls):
        """All source files that determine PCW's host behavior or instructions."""
        fixed = [
            PLUGIN / "plugin.json",
            PLUGIN / ".codex-plugin" / "plugin.json",
            PLUGIN / "mcp.json",
            PLUGIN / "pyproject.toml",
        ]
        trees = (PLUGIN / "pcw_runtime", PLUGIN / "scripts", PLUGIN / "skills")
        return sorted(
            [path for path in fixed if path.is_file()] +
            [path for tree in trees if tree.is_dir() for path in tree.rglob("*")
             if path.is_file() and "__pycache__" not in path.parts],
            key=lambda path: path.relative_to(PLUGIN).as_posix(),
        )

    @classmethod
    def _assert_candidate_parity(cls):
        missing, different = [], []
        for path in cls._candidate_files():
            relative = path.relative_to(PLUGIN)
            candidate = cls.installed_root / relative
            if not candidate.is_file():
                missing.append(relative.as_posix())
            elif path.read_bytes() != candidate.read_bytes():
                different.append(relative.as_posix())
        assert not missing and not different, {
            "message": "Installed PCW candidate differs from source",
            "missing": missing,
            "different": different,
        }

    @staticmethod
    def _declared_tool_names():
        return {"pcw_query", "pcw_execute"}

    def test_tools_are_loaded_and_memory_works_through_codex(self):
        inventory = self.host.call("mcpServerStatus/list", {
            "threadId": self.thread_id, "detail": "toolsAndAuthOnly", "limit": 100,
        })
        server = next(item for item in inventory["data"] if item["name"] == "pcw")
        # Codex 0.154 returns a compact list of names while earlier hosts
        # returned descriptors. Both are host inventory, not a static proxy.
        actual_tools = {tool if isinstance(tool, str) else tool["name"] for tool in server["tools"]}
        self.assertEqual(self._declared_tool_names(), actual_tools, server)
        item = self.save_fixture(content="Запись через настоящий Codex.")
        self.assertEqual("Запись через настоящий Codex.", self.tool("read", material_id=item["id"])["content"])
        changed = self.save_fixture(material_id=item["id"], content="Обновлено через Codex.", expected_revision=item["revision"])
        self.assertEqual("Обновлено через Codex.", self.tool("read", material_id=changed["id"])["content"])
        instructions = self.tool("instructions_read")
        self.assertEqual("INSTRUCTIONS.md", instructions["path"])
        self.assertTrue(instructions["content"].strip())

    def test_native_pcw_skill_is_discoverable_for_explicit_turn_input(self):
        skills = self.host.call("skills/list", {"cwds": [self.work], "forceReload": True})
        for short_name in ("pcw", "pcw-edit-instructions", "pcw-search", "pcw-create", "pcw-audit", "pcw-rework", "pcw-upgrade", "pcw-analyze"):
            matching = [skill for entry in skills["data"] for skill in entry["skills"]
                        if skill["name"] == self.skill_prefix + short_name
                        and Path(skill["path"]) == self.skill_root / short_name / "SKILL.md"
                        and (self.source_mode or skill.get("pluginId") == PLUGIN_ID)]
            self.assertEqual(1, len(matching), {"skill": short_name, "matches": matching, "errors": [e.get("errors", []) for e in skills["data"]]})
            self.assertTrue(matching[0]["enabled"])
            self.assertTrue(matching[0]["path"].endswith("/skills/" + short_name + "/SKILL.md"))

    def test_agent_creates_context_through_compact_interface(self):
        target = Path(self.work) / ".pcw" / self._testMethodName
        self.assertFalse(target.exists())
        thread = self.host.call("thread/start", {
            "cwd": self.work, "ephemeral": True, "approvalPolicy": "on-request",
            "approvalsReviewer": "auto_review", "sandbox": "workspace-write",
        })
        thread_id = thread["thread"]["id"]
        purpose = "Контекст для правил проверки учебных документов."
        turn = self.host.call("turn/start", {
            "threadId": thread_id,
            "input": [
                {"type": "text", "text": (
                    f"${self.skill_prefix}pcw-create Создай новый контекст по точному пути {target}. "
                    f"Назначение: {purpose} "
                    "Настройки выбраны: обновления без отдельного вызова PCW запрещены; "
                    "показанные изменения знаний требуют моего подтверждения; "
                    "Git-историю не включать; лимит размера материалов отключён. "
                    "Создание обязательных ссылок для повторной проверки источников отключено. "
                    "Используй стандартные инструкции с этими настройками и указанное назначение. "
                    "Подтверждаю создание с этими текстами и настройками. "
                    "Другие контексты не трогай; рабочую задачу после создания не запускай."
                )},
                {"type": "skill", "name": self.skill_prefix + "pcw-create",
                 "path": str(self.skill_root / "pcw-create" / "SKILL.md")},
            ],
        })
        turn_id = turn["turn"]["id"]
        try:
            completed = self.host.wait_notification(
                lambda event: event.get("method") == "turn/completed"
                and event.get("params", {}).get("threadId") == thread_id
                and event["params"].get("turn", {}).get("id") == turn_id, timeout=300)
        except TimeoutError:
            self.host.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            raise
        self.assertEqual("completed", completed["params"]["turn"]["status"], completed)
        items = [event["params"]["item"] for event in self.host.notification_snapshot()
                 if event.get("method") == "item/completed"
                 and event.get("params", {}).get("threadId") == thread_id
                 and event["params"].get("turnId") == turn_id]
        calls = [item for item in items if item.get("type") == "mcpToolCall" and item.get("server") == "pcw"]
        self.assertTrue(calls, items)
        self.assertTrue({item["tool"] for item in calls} <= {"pcw_query", "pcw_execute"})
        successful = [item for item in calls if item.get("status") == "completed"
                      and not item.get("error") and not (item.get("result") or {}).get("isError")]
        self.assertTrue(any(item["tool"] == "pcw_query" and item["arguments"].get("operation") == "describe"
                            for item in successful), "The model could not discover the operation schema")
        created = [item for item in successful if item["tool"] == "pcw_execute"
                   and item["arguments"].get("operation") == "workspace_create"]
        self.assertEqual(1, len(created), items)
        self.assertEqual(str(target.parent.parent), created[0]["arguments"]["work_root"])
        self.assertEqual(target.name, created[0]["arguments"]["workspace"])
        self.assertTrue((target / "INSTRUCTIONS.md").is_file())
        self.assertIn(purpose, (target / "PROJECT.md").read_text())
        self.assertFalse(list((target / "knowledge").glob("*.md")))
        self.assertFalse(any(item["arguments"].get("operation") == "startup" for item in calls))


    def test_agent_retains_tmp_and_waits_for_human_approval(self):
        # This scenario explicitly selects manual approval instead of the default.
        instructions = self.tool("instructions_read")
        manual = instructions["content"].replace("knowledge_approval_required: false", "knowledge_approval_required: true")
        rules = self.tool("instructions_prepare", content=manual,
                          expected_revision=instructions["revision"], user_request="Require knowledge approval in this test")
        self.tool("instructions_apply", proposal_id=rules["proposal_id"],
                  expected_revision=rules["revision"], confirmation="Apply the manual-approval test instructions")
        # Seed only a source pointer. Neither the prompt nor initial knowledge
        # contains the answer, and the harness never saves the answer for the model.
        fact = "PCW-" + uuid.uuid4().hex
        source = Path(self.work) / "project-info.md"
        source.write_text("# Проект\n\nКод поставки: " + fact + "\n", encoding="utf-8")
        seed = self.save_fixture(content=(
            "# Код поставки проекта\n\nКод поставки указан в project-info.md. "
            "Значение ещё не проверено.\n"
        ))
        self.assertEqual("indexed", seed["index_status"], seed)
        knowledge = Path(self.work) / ".pcw" / self._testMethodName / "knowledge"
        initial = list(knowledge.glob("*.md"))
        self.assertTrue(initial, "The fixture must contain searchable initial knowledge")
        self.assertTrue(all(fact not in path.read_text(encoding="utf-8") for path in initial))

        thread = self.host.call("thread/start", {
            "cwd": self.work, "ephemeral": True, "approvalPolicy": "on-request",
            "approvalsReviewer": "auto_review", "sandbox": "workspace-write",
        })
        thread_id = thread["thread"]["id"]
        turn = self.host.call("turn/start", {
            "threadId": thread_id,
            "input": [
                {"type": "text", "text": (
                    f"${self.skill_prefix}pcw Какой код поставки у проекта? "
                    f"Рабочая папка: {self.work}. Workspace: {self._testMethodName}. "
                    "Исходные файлы проекта не изменяй."
                )},
                {"type": "skill", "name": self.skill_prefix + "pcw",
                 "path": str(self.skill_root / "pcw" / "SKILL.md")},
            ],
        })
        turn_id = turn["turn"]["id"]
        try:
            completed = self.host.wait_notification(
                lambda event: event.get("method") == "turn/completed"
                and event.get("params", {}).get("threadId") == thread_id
                and event["params"].get("turn", {}).get("id") == turn_id,
                timeout=300,
            )
        except TimeoutError:
            self.host.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            raise
        self.assertEqual("completed", completed["params"]["turn"]["status"], completed)
        items = [
            event["params"]["item"] for event in self.host.notification_snapshot()
            if event.get("method") == "item/completed"
            and event.get("params", {}).get("threadId") == thread_id
            and event["params"].get("turnId") == turn_id
        ]
        answers = [(index, item) for index, item in enumerate(items) if item.get("type") == "agentMessage"]
        self.assertTrue(answers, "The model did not return an answer")
        answer_index, answer = answers[-1]
        self.assertIn(fact, answer["text"], "The answer must contain the fact read from the source")
        calls = [
            (index, item) for index, item in enumerate(items)
            if item.get("type") == "mcpToolCall" and item.get("server") == "pcw"
            and item.get("arguments", {}).get("workspace") == self._testMethodName
            and item["arguments"].get("work_root") == self.work
        ]
        successful = [(index, item) for index, item in calls
                      if item.get("status") == "completed" and not item.get("error")
                      and not (item.get("result") or {}).get("isError")]
        names = [item.get("arguments", {}).get("operation") for _, item in successful]
        self.assertIn("startup", names)
        self.assertIn("search", names)
        self.assertTrue({"read", "read_many"} & set(names))
        self.assertLess(names.index("search"), min(names.index(name) for name in
                        ("read", "read_many") if name in names))
        writes = [(index, item) for index, item in successful if item.get("arguments", {}).get("operation") == "tmp_write"
                  and fact in item["arguments"]["arguments"].get("content", "")]
        self.assertTrue(writes, "The agent did not retain the discovered result in temporary context")
        self.assertTrue(any(index < answer_index for index, _ in writes))
        self.assertIn("proposal_prepare", names)
        prepared_at = next(index for index, item in successful if item.get("arguments", {}).get("operation") == "proposal_prepare")
        self.assertTrue(any(writes[0][0] < index < prepared_at and item.get("arguments", {}).get("operation") == "search"
                            for index, item in successful),
                        "The agent must compare the new result with memory before preparing changes")
        self.assertNotIn("proposal_apply", names, "Knowledge changed before human approval")
        self.assertNotIn("finish", names, "Cycle closed while waiting for the user")
        self.assertTrue(all(fact not in path.read_text() for path in knowledge.glob("*.md")))
        active_cycle = writes[-1][1]["arguments"]["arguments"]["cycle_id"]
        temporary = knowledge.parent / "tmp" / active_cycle
        self.assertTrue(any(fact in path.read_text() for path in (temporary / "materials").glob("*.md")))
        before_approval = len(self.host.notification_snapshot())
        approval = self.host.call("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": "Подтверждаю показанные изменения постоянной памяти. Примени их."}],
        })
        approval_id = approval["turn"]["id"]
        try:
            completed = self.host.wait_notification(
                lambda event: event.get("method") == "turn/completed"
                and event.get("params", {}).get("threadId") == thread_id
                and event["params"].get("turn", {}).get("id") == approval_id,
                timeout=300)
        except TimeoutError:
            self.host.call("turn/interrupt", {"threadId": thread_id, "turnId": approval_id})
            raise
        self.assertEqual("completed", completed["params"]["turn"]["status"])
        later_calls = [event["params"]["item"] for event in self.host.notification_snapshot()[before_approval:]
                       if event.get("method") == "item/completed"
                       and event.get("params", {}).get("threadId") == thread_id
                       and event["params"].get("turnId") == approval_id
                       and event["params"].get("item", {}).get("type") == "mcpToolCall"]
        applied = [item for item in later_calls if item.get("arguments", {}).get("operation") == "proposal_apply"
                   and item.get("status") == "completed" and not item.get("error")
                   and not (item.get("result") or {}).get("isError")]
        self.assertTrue(applied, "The agent failed to apply after explicit approval")
        self.assertTrue(any(fact in path.read_text() for path in knowledge.glob("*.md")))
        self.assertFalse(temporary.exists(), "The completed cycle left tmp behind or requested separate closure approval")



    @unittest.skipUnless(os.environ.get("PCW_TEST_QWEN") == "1", "Set PCW_TEST_QWEN=1 after provisioning local Qwen")
    def test_local_qwen_index_search_and_links_through_codex(self):
        status = self.tool("status")
        self.assertEqual("Qwen/Qwen3-Embedding-0.6B", status["active"]["config"]["profile"]["model"])
        text = "Резервные копии PostgreSQL сохраняются ежедневно в три часа ночи."
        item = self.save_fixture(content=text)
        self.assertEqual("indexed", item["index_status"], item)
        found = self.tool("search", query="Когда делают резервные копии PostgreSQL?")
        self.assertEqual(item["id"], found["results"][0]["id"], found)
        other = self.save_fixture(content="Порядок восстановления данных.")
        self.save_fixture(content="Порядок восстановления данных.", material_id=other["id"],
                          expected_revision=other["revision"], links=[item["id"]])
        self.assertIn(item["id"], self.tool("read", material_id=other["id"])["links"])


if __name__ == "__main__":
    unittest.main()
