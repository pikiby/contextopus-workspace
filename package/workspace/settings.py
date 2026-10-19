"""Workspace policy edits in the existing Core configuration, under prior policy."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import re
import uuid

from .configuration import WorkspaceSettings as Settings, WORKSPACE_SETTINGS, WORKSPACE_POLICY_DEFAULTS
from contextopus.core.storage import sync_directory
from .runtime import PCWError
from .text_io import read_utf8


def configuration_path(layout, context):
    path = Settings(layout)._scope_path("workspace", context)
    for candidate in (path, *path.parents):
        if candidate == layout.root:
            break
        if candidate.is_symlink():
            raise PCWError("Workspace configuration cannot use symlinks")
    return path


def configuration_text(layout, context):
    path = configuration_path(layout, context)
    return path.read_bytes().decode("utf-8") if path.is_file() else None


def save_configuration(layout, context, text):
    path = configuration_path(layout, context)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=".workspace-settings-", delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class WorkspaceSettingsMixin:
    def _settings_receipt_path(self, revision):
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise PCWError("Supply the exact settings preview revision")
        path = self.control.root / "settings" / (revision + ".json")
        self.control.safe(path)
        return path

    def _settings_snapshot(self, fields=None, agent=None, agent_model=None):
        text = configuration_text(self.control.layout, self.engine.workspace)
        if text is None or not set(WORKSPACE_POLICY_DEFAULTS) <= json.loads(text).keys():
            raise PCWError("Workspace settings are missing; restore the configuration or use upgrade_plan")
        try:
            return Settings(self.control.layout).workspace_snapshot(
                context=self.engine.workspace, fields=fields, agent=agent, agent_model=agent_model)
        except ValueError as exc:
            raise PCWError(str(exc)) from exc

    def _policies(self):
        return self._settings_snapshot(list(WORKSPACE_POLICY_DEFAULTS))["values"]

    def settings_read(self, fields: list[str] | None = None,
                      agent: str | None = None, agent_model: str | None = None):
        self._require_format(storage=False)
        return self._settings_snapshot(fields, agent, agent_model)

    def settings_preview(self, changes: dict, user_request: str):
        self._require_format(storage=False)
        if not isinstance(user_request, str) or not user_request.strip():
            raise PCWError("An explicit user request to change Workspace settings is required")
        if not isinstance(changes, dict) or not changes or set(changes) - set(WORKSPACE_SETTINGS):
            raise PCWError("Supply only known Workspace settings")
        try:
            Settings(self.control.layout)._validate_changes(changes)
        except ValueError as exc:
            raise PCWError(str(exc)) from exc
        with self.control.guard():
            for path in (self.control.root / "settings").glob("*.json"):
                self.control.safe(path)
                if json.loads(read_utf8(path))["state"] == "applying":
                    raise PCWError("Resume pending settings_apply with preview revision " + path.stem)
            before = self._settings_snapshot()
            before_configuration = configuration_text(self.control.layout, self.engine.workspace)
            result = {"base_revision": before["revision"], "changes": changes,
                  "user_request": user_request,
                  "approval_required": before["values"]["instructions_approval_required"],
                  "before": before["values"], "after": {**before["values"], **changes}}
            payload = {"preview_id": uuid.uuid4().hex, "preview": result,
                       "before_configuration": before_configuration,
                       "after_configuration": json.dumps({**json.loads(before_configuration), **changes},
                           ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                       "history_before": self._settings_history_text()}
            revision = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            path = self._settings_receipt_path(revision)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                self.control.write(path, json.dumps({"payload": payload, "state": "prepared"},
                                                    ensure_ascii=False, indent=2) + "\n")
            return {**result, "preview_revision": revision}

    def settings_apply(self, preview_revision: str, confirmation: str | None = None):
        with self.control.guard():
            path = self._settings_receipt_path(preview_revision)
            if not path.is_file():
                raise PCWError("Settings preview is absent; use settings_preview before applying")
            receipt = json.loads(read_utf8(path))
            if receipt.get("state") not in {"prepared", "applying", "applied"}:
                raise PCWError("Unsupported settings operation state; inspect the retained receipt")
            payload = receipt["payload"]
            if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != preview_revision:
                raise PCWError("Stored settings preview changed; inspect the retained receipt")
            preview = payload["preview"]
            if receipt["state"] == "applied":
                history = receipt.get("history", {"status": "disabled"})
                if history.get("status") == "pending":
                    history = self._history_flush("settings-" + preview_revision)
                    receipt["history"] = history
                    self.control.write(path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
                return {"saved": True, "replayed": True, "preview_revision": preview_revision,
                        "history": history, "settings": self._settings_snapshot()}
            if receipt["state"] == "prepared":
                if self._settings_snapshot()["revision"] != preview["base_revision"]:
                    raise PCWError("Settings changed; review settings_preview again")
                if configuration_text(self.control.layout, self.engine.workspace) != payload["before_configuration"]:
                    raise PCWError("Configuration changed; review settings_preview again")
                if preview["approval_required"] and (not isinstance(confirmation, str) or not confirmation.strip()):
                    raise PCWError("Current user's explicit confirmation of the settings revision is required")
                receipt.update(state="applying", confirmation=confirmation)
                self.control.write(path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            if (preview["approval_required"] and
                    (not isinstance(receipt.get("confirmation"), str) or not receipt["confirmation"].strip())):
                raise PCWError("The pending settings operation has no recorded user confirmation")
            current = configuration_text(self.control.layout, self.engine.workspace)
            if current not in (payload["before_configuration"], payload["after_configuration"]):
                raise PCWError("Settings conflict during recovery; inspect the retained settings receipt")
            if not receipt.get("history_baseline_saved"):
                if preview["before"]["git_history_enabled"] or preview["after"]["git_history_enabled"]:
                    try:
                        receipt["history_baseline"] = self._history_record("pre-settings-" + preview_revision, force=True)
                    except Exception as exc:
                        return {"saved": False, "status": "history_pending", "preview_revision": preview_revision,
                                "error": str(exc), "next_action": "settings_apply"}
                receipt["history_baseline_saved"] = True
                self.control.write(path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            if not receipt.get("write_verified"):
                if current == payload["before_configuration"] and self._settings_snapshot()["revision"] != preview["base_revision"]:
                    raise PCWError("Settings changed before saving; review settings_preview again")
                try:
                    save_configuration(self.control.layout, self.engine.workspace, payload["after_configuration"])
                except Exception as exc:
                    saved = configuration_text(self.control.layout, self.engine.workspace) == payload["after_configuration"]
                    return {"saved": saved, "status": "write_pending", "preview_revision": preview_revision,
                            "error": str(exc), "next_action": "settings_apply"}
                if configuration_text(self.control.layout, self.engine.workspace) != payload["after_configuration"]:
                    raise PCWError("Settings write verification failed; inspect the retained settings receipt")
                receipt["write_verified"] = True
                self.control.write(path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            from .history_queries import SETTINGS_HISTORY_PATH
            before_text, after_text = payload["history_before"], self._settings_history_text()
            plan = {"kind": "settings", "summary": "Update Workspace settings", "entries": {
                "workspace-settings": {"before": {"path": SETTINGS_HISTORY_PATH, "text": before_text,
                                                    "fingerprint": hashlib.sha256(before_text.encode()).hexdigest()},
                                       "after": {"path": SETTINGS_HISTORY_PATH, "text": after_text,
                                                   "fingerprint": hashlib.sha256(after_text.encode()).hexdigest()}}}}
            try:
                history = self._history_record("settings-" + preview_revision,
                    force=preview["before"]["git_history_enabled"], plan=plan)
            except Exception as exc:
                return {"saved": True, "status": "history_pending", "preview_revision": preview_revision,
                        "error": str(exc), "next_action": "settings_apply"}
            receipt.update(state="applied", history=history)
            self.control.write(path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            return {"saved": True, "preview_revision": preview_revision,
                    "history": history, "settings": self._settings_snapshot()}
