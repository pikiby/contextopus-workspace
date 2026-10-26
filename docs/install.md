# Install Workspace

Workspace 1.8.0 is a separate optional module for Contextopus Core >=1.8,<2.
Python 3.12+ is required. Start the compatible Core service before module
installation; the selected root must match that running owner. `--root` selects
the data root and does not start a separate server. Core 1.4.0 does not provide the generic source-install
interface used by these installers.

## Install a module release

Download the [latest ZIP](https://github.com/pikiby/contextopus-workspace/releases/latest/download/contextopus-workspace.zip)
or the [versioned 1.8.0 ZIP](https://github.com/pikiby/contextopus-workspace/releases/download/v1.8.0/contextopus-workspace-1.8.0.zip)
from the [release](https://github.com/pikiby/contextopus-workspace/releases).
Extract the ZIP and enter its `workspace` directory.

```console
sh install.sh --root /path/to/contextopus-data --plan
sh install.sh --root /path/to/contextopus-data
```

PowerShell:

```powershell
.\install.ps1 -Root C:\path\to\Contextopus -Plan
.\install.ps1 -Root C:\path\to\Contextopus
```

Without a root option, Core uses its existing bound default. No user-client setup,
private Python environment, OS service or model download is performed by these
wrappers. They delegate to the common Core lifecycle:

```console
contextopus --root /path/to/data modules install workspace --source /path/to/workspace --plan
contextopus --root /path/to/data modules install workspace --source /path/to/workspace
contextopus --root /path/to/data modules
```

`--source` also accepts the unextracted module ZIP. Core validates the package,
shows the installation preview, prepares the native contribution, applies its
explicit module-owned schema migration and enables it after readiness succeeds.
Listing alone does not migrate schema. Workspace executes with Core-process
trust; use a package you trust.

Source installation refuses an existing target or registered installation. If a
first installation failed after copying, preserve those files and retry with
`modules install workspace` without `--source`. Use `modules update workspace
--source /path/to/new-module` for an explicit update of an existing module. Records,
settings, control state and SQL remain separate from replaceable code. A legacy
`.pcw` migration is a distinct operation; do not copy a repository over live data.

## Model and client setup

Installing Workspace does not require or load an embedding model. Configure one
explicitly through Core when semantic retrieval is needed. If the profile already
exists, run `contextopus --root /path/to/data init` after module installation so
Workspace's setup hook can deliver its base documentation and algorithm catalog.
Review unresolved document conflicts or approval requests in the returned result.

Preview and apply Core's native client integration to deliver the available
Workspace skills. Module activation alone does not replace client files. See
[Core's client guide](https://github.com/pikiby/contextopus-core/blob/main/docs/integrations.md).

## Build from source

The builder uses only Python's standard library:

```console
python scripts/build-module.py dist/workspace --archive dist/contextopus-workspace-1.8.0.zip
sh dist/workspace/install.sh --root /path/to/data --plan
```

The source-checkout wrapper deliberately asks for this build step. A built module
contains `module.json`, `package/`, the thin installers, licenses and current
guides. Bytecode, generated packaging metadata, tests, archives, mutable state and
repository metadata are excluded. Existing output paths are refused.

For release assets with checksums, use `python scripts/build-release.py dist` in a
fresh output directory. It produces `contextopus-workspace-1.8.0.zip`, the identical
`contextopus-workspace.zip` download alias and a local SHA256SUMS verification file.
Only the ZIPs are published as release assets.

## Verify without live data

With a compatible Core installed in the selected interpreter:

```console
python scripts/verify_installed.py --workspace-module dist/contextopus-workspace-1.8.0.zip
```

This uses disposable storage to install, disable, restart and re-enable the module,
checking its SQL, operations, procedures and six skills alongside Core's `m` skill.
It makes no model calls or native-client changes. The smoke verifies lifecycle
behavior, not model quality or the user's running service.
