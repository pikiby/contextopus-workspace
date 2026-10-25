<p align="center"><img src="docs/media/contextopus-wide-1200.svg" alt="Contextopus" width="720"></p>

# Contextopus Workspace

Give coding agents an evolving project memory. Keep verified facts, decisions,
tasks and complete project instructions together; retrieve relevant knowledge by
meaning and update it through a reviewed procedure as the project changes.
Several sessions can work on the same project context at once, sharing current
knowledge and rules while card reservations coordinate their edits.

Workspace is an optional [Contextopus Core](https://github.com/pikiby/contextopus-core)
module. Markdown remains readable and editable. Core supplies the shared model,
vector index and service; Workspace supplies linked records, source checks and the
procedure for finding evidence, reviewing changes and continuing unfinished work.

[Usage](docs/usage.md) · [Installation](docs/install.md) ·
[Architecture](docs/architecture.md) · [Development](docs/development.md)

## Install

**Workspace 1.7.0 requires Core >=1.7,<2 and Python 3.12+.** Install Core first
and start its service. The selected root must match that running Core owner.
Download and extract the [latest module ZIP](https://github.com/pikiby/contextopus-workspace/releases/latest/download/contextopus-workspace.zip)
or the [exact 1.7.0 ZIP](https://github.com/pikiby/contextopus-workspace/releases/download/v1.7.0/contextopus-workspace-1.7.0.zip).
Release availability is listed on the [releases page](https://github.com/pikiby/contextopus-workspace/releases).
If the asset is not available yet, [build it from this checkout](docs/install.md#build-from-source).

From the extracted `workspace` folder:

```console
sh install.sh --root /path/to/contextopus-data
```

On Windows, use PowerShell:

```powershell
.\install.ps1 -Root C:\path\to\Contextopus
```

Omit the root to use Core's bound default. Add `--plan` (`-Plan` in PowerShell) to
inspect the validated installation without applying it. The wrappers use Core's
common module lifecycle and do not create another runtime. Equivalent direct CLI:

```console
contextopus --root /path/to/contextopus-data modules install workspace --source /path/to/workspace
```

A module ZIP is also accepted as `--source`. An existing module target is refused;
use the normal retry/update lifecycle for an existing installation. Module
installation creates its SQL schema and enables its interfaces without loading a
model. Configure Core's embedding profile explicitly to use semantic retrieval.
If Core already has a model, run `contextopus --root /path/to/contextopus-data init`
after installing Workspace to deliver its managed documentation context.
[Installation, updates and client integration](docs/install.md).

## Use project knowledge

> Use workspace-context to find the project context for release verification.
>
> Use workspace-search: find the release decision, read the supporting records and check its sources.
>
> Use workspace: verify the changed retry interval, update the existing decision and show the changes.

The `workspace` procedure connects instructions, evidence, reviewed knowledge and task
state. `workspace-search` answers questions without opening a work cycle. `workspace-create`
creates an explicitly requested context, `workspace-settings` handles requested settings
changes and `workspace-analyze` investigates a bounded episode.

- Keep project rules and write destinations separate while sharing one vector index.
- Link facts, tasks and decisions with stable IDs and descriptive Markdown filenames.
- Check declared sources afresh and retain the conditions and limits of a conclusion.
- Continue unfinished work with optional temporary handoffs and exact proposal revisions.
- Inspect Git versions and operation evidence without treating a read event as proof of model use.

After enabling Workspace, preview and apply Core's native client integration to
install its six skills. Enabling the module alone does not overwrite client files.
[Session Search](https://github.com/pikiby/contextopus-session-search) is a separate
module for searchable session descriptions.

## Work across sessions

**Work in parallel without splitting your project memory.** Give one session an
installation task and another a documentation task: both use the same knowledge
and rules, and their accepted updates become available to the other. Each session
keeps its own work cycle.

Workspace 1.6+ temporarily reserves the cards an agent is editing, with a short
reason visible to other agents. Other sessions can still read, search and update
different cards. Related edits reserve their full set; reservations are released
after saving or expire, and revision checks reject outdated changes. Direct
Markdown editors do not honor reservations; revision checks detect observed
external changes.
[Concurrent editing and recovery](docs/concurrent-editing.md).

## Develop

```console
python scripts/build-release.py dist
/path/to/core-environment/bin/python scripts/test.py --core /path/to/contextopus-core
```

The release builder produces a versioned module ZIP, a stable download alias and
SHA256SUMS. Supply a compatible Core checkout explicitly or use Core already
installed in the selected interpreter. Tests use disposable roots and offline
fixtures. [Development, CI and release assets](docs/development.md).

Code and procedures: `package/workspace/`. Tests: `tests/`. Historical designs:
`docs/archive/`. Legacy import compatibility: `plugins/persistent-context-workspace/`.
Only module code, installers, licenses and current guides enter the module ZIP;
user state, repository metadata, tests and caches are excluded.

Licensed under [Apache 2.0](LICENSE).

## Client delivery

Core 1.7 client packages share one installer and one persistent data root.
Skills use full canonical names under the `contextopus` plugin namespace, or a
`contextopus-` prefix for native file delivery. Short aliases are user-selected.
See the [client package guide](https://github.com/pikiby/contextopus-core/blob/main/docs/client-packages.md)
for package formats, setup and verification limits.

## Current release

[1.7.0: Shared instructions and project documents](docs/releases/1.7.0.md) · [Downloads](https://github.com/pikiby/contextopus-workspace/releases/tag/v1.7.0)
