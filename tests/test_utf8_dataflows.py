"""Public UTF-8 files must round-trip independently of the OS text default."""
import hashlib
import json
from pathlib import Path

import pytest

from contextopus.core.filtering import AlgorithmRegistry
from contextopus.core.module_management.manifest import ModuleManifestReader
from contextopus.core.settings import Settings
from contextopus.core.storage import StorageLayout
from workspace.cycles import CycleRuntime
from workspace.runtime import _parse
from workspace.settings import configuration_text, save_configuration
from test_record_links_v13 import context, put
from test_runtime import Factory


@pytest.fixture
def cp1252_file_default(monkeypatch):
    """Model the Windows file default; explicit UTF-8 remains untouched."""
    original = Path.open

    def locale_open(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return original(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", locale_open)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


@pytest.mark.parametrize("first_reader", ["apply", "scan"])
def test_unicode_workspace_settings_receipt_apply_reopen_and_replay(tmp_path, cp1252_file_default, first_reader):
    runtime = context(tmp_path)
    reopened = None
    try:
        knowledge = {p: p.read_bytes() for p in runtime.engine.knowledge.rglob("*.md")}
        profile = {"agent": "Новый агент", "model": "Модель условий", "reread_instructions": True}
        request = "Нужно перечитывать правила для выбранного агента, сохранив остальные настройки."
        preview = runtime.settings_preview({"instruction_profiles": [profile]}, request)
        receipt_path = runtime._settings_receipt_path(preview["preview_revision"])
        raw_preview = receipt_path.read_bytes()
        receipt = json.loads(raw_preview.decode("utf-8"))
        assert receipt["payload"]["preview"]["user_request"] == request
        assert hashlib.sha256(json.dumps(receipt["payload"], sort_keys=True).encode()).hexdigest() == preview["preview_revision"]
        if first_reader == "scan":
            runtime.settings_preview({"reread_instructions": True}, "Новая отдельная проверка настроек")
        result = runtime.settings_apply(preview["preview_revision"], "Подтверждаю показанную ревизию")
        assert result["saved"] is True
        saved_receipt = receipt_path.read_bytes()
        saved_configuration = configuration_text(runtime.control.layout, "alpha")
        assert json.loads(saved_configuration)["instruction_profiles"] == [profile]
        assert request.encode("utf-8") in saved_receipt
        runtime.close()
        reopened = CycleRuntime(tmp_path, "alpha", provider_factory=Factory())
        selected = reopened.settings_read(agent=profile["agent"], agent_model=profile["model"])
        assert selected["values"]["instruction_profiles"] == [profile]
        assert selected["values"]["reread_instructions"] is True
        replay = reopened.settings_apply(preview["preview_revision"])
        assert replay["saved"] and replay["replayed"]
        assert receipt_path.read_bytes() == saved_receipt
        assert configuration_text(reopened.control.layout, "alpha") == saved_configuration
        assert {p: p.read_bytes() for p in reopened.engine.knowledge.rglob("*.md")} == knowledge
    finally:
        runtime.close()
        if reopened is not None:
            reopened.close()


@pytest.mark.parametrize("scope", ["root", "module", "context"])
def test_core_unicode_configuration_preview_apply_and_reopen(tmp_path, cp1252_file_default, scope):
    layout = StorageLayout(tmp_path)
    settings = Settings(layout)
    selection = {} if scope == "root" else {"module": "workspace"}
    if scope == "context":
        selection["context"] = "alpha"
    original = {"include": ["Новые документы/**"], "object_type": "Проверенные условия"}
    path = settings._scope_path(**selection)
    if scope == "context":
        save_configuration(layout, "alpha", json_bytes(original).decode("utf-8"))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes(original))
    before = settings.snapshot(**selection)
    assert before["values"]["include"] == original["include"]
    assert before["values"]["object_type"] == original["object_type"]
    preview = settings.preview({"searchN": 9}, **selection)
    assert settings.apply(preview["preview_revision"])["saved"]
    saved = path.read_bytes()
    again = Settings(layout).snapshot(**selection)
    assert again["values"]["searchN"] == 9
    assert again["values"]["include"] == original["include"]
    assert again["values"]["object_type"] == original["object_type"]
    assert again["revision"] == settings.snapshot(**selection)["revision"]
    assert path.read_bytes() == saved


@pytest.mark.parametrize("action", ["update", "links"])
def test_inherited_adapter_save_preserves_unicode_native_alias(tmp_path, cp1252_file_default, action):
    factory = Factory()
    runtime = context(tmp_path, factory=factory)
    try:
        target = put(runtime, "# Новые условия\nПроверенные границы применимости.")
        extra = put(runtime, "# Дополнительные условия\nПроверяйте при изменении состояния.")
        source = put(runtime, "# Наблюдение\nИспользуйте проверенные условия.", [target["id"]])
        path = runtime.engine.root / source["path"]
        material = runtime.engine.resolve(source["id"])
        alias = "Нужные условия: авторская подпись"
        path.write_bytes(runtime.engine.link_codec().record_text(material.metadata, material.content,
                                                               aliases={target["id"]: alias}).encode("utf-8"))
        original_metadata, body = _parse(path.read_bytes().decode("utf-8"))
        calls = len(factory.calls)
        if action == "update":
            expected_body = body + "\nНовое наблюдение уточняет те же условия."
            runtime.engine.update(source["id"], expected_body, expected_revision=material.revision)
            expected_links = [target["id"]]
        else:
            runtime.engine.links(source["id"], add=[extra["id"]])
            expected_body, expected_links = body, [target["id"], extra["id"]]
            assert len(factory.calls) == calls
        metadata, actual_body = _parse(path.read_bytes().decode("utf-8"))
        assert actual_body == expected_body
        assert metadata["id"] == source["id"]
        assert metadata["description"] == original_metadata["description"]
        assert runtime.engine.link_codec().decode(metadata["links"], source["id"]) == expected_links
        assert runtime.engine.link_codec().display_aliases(metadata["links"], source["id"])[target["id"]] == alias
        runtime.engine.reconcile()
        assert runtime.engine.resolve(source["id"]).content == expected_body
        assert runtime.engine.resolve(source["id"]).links == expected_links
    finally:
        runtime.close()


@pytest.mark.parametrize("catalog", ["manifest", "algorithm"])
def test_public_module_catalogs_load_unicode_utf8(tmp_path, cp1252_file_default, catalog):
    description = "Новые условия отбора: проверенная применимость"
    if catalog == "manifest":
        package = tmp_path / "modules/unicode-demo/package"
        package.mkdir(parents=True)
        (package / "reader.py").write_bytes("# Новое чтение\ndef read(args, core):\n    return {}\n".encode("utf-8"))
        manifest = {"id": "unicode-demo", "version": "1.0.0", "operations": [
            {"name": "unicode-demo.read", "kind": "query", "handler": "reader.py:read", "description": description}]}
        (package.parent / "module.json").write_bytes(json_bytes(manifest))
        observed = ModuleManifestReader(StorageLayout(tmp_path))._manifest("unicode-demo")
        assert observed == manifest
        assert not (tmp_path / "core/storage/modules").exists()
    else:
        package = tmp_path / "author-algorithm"
        package.mkdir()
        manifest = {"id": "unicode-demo", "version": "1.0.0", "purpose": description,
                    "when_to_use": "Нужно сохранить все подходящие записи", "how_to_adjust": "Настроек нет",
                    "parameters_schema": {"type": "object", "additionalProperties": False}, "defaults": {}}
        (package / "description.json").write_bytes(json_bytes(manifest))
        (package / "algorithm.py").write_bytes("# Новые правила автора\ndef select(rows, parameters):\n    return rows, {}\n".encode("utf-8"))
        registry = AlgorithmRegistry([package])
        assert not registry.errors
        assert registry.catalog("unicode-demo") == [manifest]
        _, _, select = registry.resolve({"algorithm": "unicode-demo"})
        rows = [{"id": "one", "distance": .2}]
        assert select(rows, {}) == (rows, {})


def test_uncheckpointed_compares_literal_crlf_with_git_blob(tmp_path):
    runtime = context(tmp_path)
    try:
        item = put(runtime, "# Наблюдение\nПроверенные условия.")
        path = runtime.engine.root / item["path"]
        raw = path.read_bytes().replace(b"\n", b"\r\n")
        path.write_bytes(raw)
        receipt = runtime._history_record("literal-crlf-check", force=True)
        assert receipt["status"] == "committed"
        assert runtime.engine._git("show", "HEAD:" + item["path"]).stdout.encode("utf-8") == raw
        assert runtime.engine.uncheckpointed([item["path"]]) == []
        path.write_bytes(raw.replace(b"\r\n", b"\n"))
        assert runtime.engine.uncheckpointed([item["path"]]) == [item["path"]]
        path.write_bytes(raw)
        assert runtime.engine.uncheckpointed([item["path"]]) == []
    finally:
        runtime.close()
