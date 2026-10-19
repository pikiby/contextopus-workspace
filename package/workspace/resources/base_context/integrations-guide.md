# Connect agent clients to one Contextopus service

One local MCP owner serves the persistent root. Native skills route requests; the service supplies current procedures. A configured URL or healthy service alone does not prove the agent can call its tools.

After explicitly choosing storage and setting up embeddings, `contextopus service install` installs the native user service. `contextopus service status`, `start`, `stop` and `restart` control it. Reading reference cards performs no setup or service installation.

Preview `contextopus integrate CLIENT --project PROJECT`, then add `--apply` for the reviewed changes:

| Client | Project configuration | Skills |
| --- | --- | --- |
| Codex | `.codex/config.toml` | `.agents/skills` |
| Claude Code | `.mcp.json` | `.claude/skills` |
| OpenCode | `opencode.json` | `.opencode/skills` |
| Crush | `crush.json` | `.agents/skills` |

Use `--user` for native user scope. Hermes supports user/profile scope only: `contextopus integrate hermes --user --hermes-home HERMES_HOME` previews `config.yaml` and `skills/` in that native home. Without the override it follows `HERMES_HOME` or the platform default. Its entrypoints use `/w` and related slash commands; invocation syntax differs between clients.

The seven canonical skills are `w`, `w-search`, `w-create`, `w-settings`, `w-context`, `w-analyze` and `m`. Integration preserves unrelated configuration and locally edited skills; conflicting Contextopus definitions require resolution. Use one delivery method per client to avoid duplicates.

In the actual client, discover `guide`, `read`, `write` and `finish`, then call `guide()` and a bounded authorized read in the intended root. Model-driven scenarios establish observed procedure use separately. Do not mutate unrelated work to test transport.

For existing sessions: `contextopus reconnect codex` reloads all loaded threads/servers; `--thread` enables a read check in that thread. OpenCode uses the existing `--client-url` and `--directory`. Claude Code uses its native `/mcp` reconnect action. Crush can renew an established connection on use; no external retry adapter for its initial failure is verified here. Hermes offers `/reload-mcp`; this release does not remotely reload an existing Hermes conversation.

Report queued reload, rediscovered tools and a successful call separately. A fresh process does not prove an old session recovered; native APIs depend on client versions.

Implementation references: `src/contextopus/integrations/native.py`, `src/contextopus/integrations/hermes.py`, `src/contextopus/integrations/reconnect.py`, `src/contextopus/interfaces/cli/main.py`. Detailed procedure: `upgrade`. Repository guides: `docs/integrations.md`, `docs/integrations/hermes.md`.
