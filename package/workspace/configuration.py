"""Workspace-owned policy defaults and validation over Core configuration layers."""

import hashlib
import json

from contextopus.core.settings import Settings, DEFAULTS as CORE_DEFAULTS

WORKSPACE_POLICY_DEFAULTS = {
    "allow_implicit_updates": False,
    "knowledge_approval_required": True,
    "instructions_approval_required": True,
    "cleanup_approval_required": True,
    "git_history_enabled": False,
    "material_max_tokens": None,
    "create_required_sources": False,
}

WORKSPACE_SETTINGS = {
    **WORKSPACE_POLICY_DEFAULTS,
    "reread_instructions": False,
    "instruction_profiles": [],
}

WORKSPACE_CREATION_DEFAULTS = {
    **WORKSPACE_POLICY_DEFAULTS,
    "cleanup_approval_required": False,
    "knowledge_approval_required": False,
    "git_history_enabled": True,
    "material_max_tokens": 1024,
}


class WorkspaceSettings(Settings):
    defaults = {**CORE_DEFAULTS, **WORKSPACE_SETTINGS}
    protected_fields = frozenset(WORKSPACE_SETTINGS)

    def workspace_snapshot(self, module="workspace", context=None, fields=None,
                           agent=None, agent_model=None):
        """Read policies without opening rules, the vector index or algorithm modules."""
        selected = list(WORKSPACE_SETTINGS) if fields is None else fields
        if (not isinstance(selected, list) or any(key not in WORKSPACE_SETTINGS for key in selected)):
            raise ValueError("Unknown workspace settings fields")
        for value in (agent, agent_model):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Explicit agent/model selectors must be nonempty strings")
        layers = self._layers(module, context)
        effective = json.loads(json.dumps(WORKSPACE_SETTINGS))
        sources = {key: "default" for key in effective}
        for origin, layer in layers:
            values = {key: value for key, value in layer.items() if key in WORKSPACE_SETTINGS}
            self._validate_changes(values)
            effective.update(values)
            sources.update({key: origin + ".configuration" for key in values})
        matches = []
        for profile in effective["instruction_profiles"]:
            if (("agent" not in profile or profile["agent"] == agent)
                    and ("model" not in profile or profile["model"] == agent_model)):
                matches.append((int("agent" in profile) + int("model" in profile), profile))
        if matches:
            specificity = max(score for score, _ in matches)
            winners = [profile for score, profile in matches if score == specificity]
            if len(winners) != 1:
                raise ValueError("Ambiguous instruction profiles; select an explicit agent/model pair")
            effective["reread_instructions"] = winners[0]["reread_instructions"]
            sources["reread_instructions"] = sources["instruction_profiles"] + ".profile"
        policy_layers = [(origin, {key: value for key, value in layer.items() if key in WORKSPACE_SETTINGS})
                         for origin, layer in layers]
        revision = hashlib.sha256(json.dumps([policy_layers, module, context], sort_keys=True).encode()).hexdigest()
        return {"values": {key: effective[key] for key in selected},
                "sources": {key: sources[key] for key in selected}, "revision": revision,
                "selected_scope": {"module": module, "context": context},
                "profile": {"agent": agent, "model": agent_model}}


    policy_snapshot = workspace_snapshot

    def validate_policy(self, changes):
        for key in self.protected_fields - {"material_max_tokens", "instruction_profiles"}:
            if key in changes and type(changes[key]) is not bool:
                raise ValueError(f"{key} must be boolean")
        if "material_max_tokens" in changes:
            value = changes["material_max_tokens"]
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("material_max_tokens must be a positive integer or null")
        if "instruction_profiles" in changes:
            profiles = changes["instruction_profiles"]
            if not isinstance(profiles, list):
                raise ValueError("instruction_profiles must be a list")
            seen = set()
            for profile in profiles:
                if (not isinstance(profile, dict) or set(profile) - {"agent", "model", "reread_instructions"}
                        or not (set(profile) & {"agent", "model"})
                        or type(profile.get("reread_instructions")) is not bool
                        or any(not isinstance(profile[key], str) or not profile[key].strip()
                               for key in ("agent", "model") if key in profile)):
                    raise ValueError("An instruction profile needs explicit agent/model and boolean reread_instructions")
                identity = (profile.get("agent"), profile.get("model"))
                if identity in seen:
                    raise ValueError("Duplicate instruction profile")
                seen.add(identity)
