<p align="center"><img src="docs/media/contextopus-wide-1200.svg" alt="Contextopus" width="720"></p>

# Contextopus Workspace

Give coding agents an evolving project memory. Keep verified facts, decisions,
tasks and complete project instructions together; retrieve relevant knowledge by
meaning and update it through a reviewed procedure as the project changes.

Workspace is an optional [Contextopus Core](https://github.com/pikiby/contextopus-core)
module. Markdown remains readable and editable. Core supplies the shared model,
vector index and service; Workspace supplies linked records, source checks and the
procedure for finding evidence, reviewing changes and continuing unfinished work.

[Usage](docs/usage.md) · [Installation](docs/install.md) ·
[Architecture](docs/architecture.md) · [Development](docs/development.md)

## Install

**Workspace 1.6.0 requires Core >=1.5,<2 and Python 3.12+.** Install Core first
and start its service. The selected root must match that running Core owner.
Download and extract the [latest module ZIP](https://github.com/pikiby/contextopus-workspace/releases/latest/download/contextopus-workspace.zip)
or the [exact 1.6.0 ZIP](https://github.com/pikiby/contextopus-workspace/releases/download/v1.6.0/contextopus-workspace-1.6.0.zip).
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

> Use w-context to find the project context for release verification.
>
> Use w-search: find the release decision, read the supporting records and check its sources.
>
> Use w: verify the changed retry interval, update the existing decision and show the changes.

The `w` procedure connects instructions, evidence, reviewed knowledge and task
state. `w-search` answers questions without opening a work cycle. `w-create`
creates an explicitly requested context, `w-settings` handles requested settings
changes and `w-analyze` investigates a bounded episode.

- Keep project rules and write destinations separate while sharing one vector index.
- Link facts, tasks and decisions with stable IDs and descriptive Markdown filenames.
- Check declared sources afresh and retain the conditions and limits of a conclusion.
- Continue unfinished work with optional temporary handoffs and exact proposal revisions.
- Inspect Git versions and operation evidence without treating a read event as proof of model use.

After enabling Workspace, preview and apply Core's native client integration to
install its six skills. Enabling the module alone does not overwrite client files.
[Session Search](https://github.com/pikiby/contextopus-session-search) is a separate
module for coding-session conversations.

## Work across sessions

Several sessions can read the same context and update different records. Before
editing a shared card, Workspace reserves it for one work cycle with a short
explanation visible to competing agents. Related changes reserve their whole set;
expired sessions do not keep cards occupied forever. Revision checks still protect
against stale proposals and external file edits.
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
