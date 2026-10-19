# Develop and test Workspace

Use Python 3.12+ and an explicitly selected compatible development Core. Workspace 1.6.0 uses the generic module source installer, native contribution and
module SQL APIs in Core >=1.5,<2. Older Core 1.4.0 packages are not compatible.

## Environment

Create a development environment and install the chosen Core with its test extras:

```console
python -m venv .venv
.venv/bin/python -m pip install -e '/path/to/core[test]'
.venv/bin/python -m pip install -e '.[test]' --no-deps
.venv/bin/python scripts/test.py --core /path/to/core
```

The runner requires `--core` to name a checkout containing `src/contextopus`, or
uses Core already importable in that interpreter. It never probes sibling paths.
Additional pytest options can follow `--`, for example `-- -q tests/test_workspace_module.py`.

Module sources are under `package/`; `pcw_runtime` aliases under the retained
legacy plugin exist for older tests and migrations. They are excluded from the
module ZIP and must not be treated as a separate supported service deployment.
Core-only build, marketplace and installed-wheel checks belong to Core; Workspace
retains its own integration, old-format migration and native-skill transition tests.

## Validation

```console
.venv/bin/python scripts/test.py --core /path/to/core
python scripts/build-module.py dist/workspace --archive dist/contextopus-workspace-1.6.0.zip
.venv/bin/python scripts/verify_installed.py --workspace-module dist/contextopus-workspace-1.6.0.zip
```

Use a fresh build destination. An explicit Core source path can be added to
`PYTHONPATH` for the smoke command if the chosen Core is not installed editable.
All standard test roots are temporary. Offline fixtures avoid model downloads.
Host-client checks and an actual 1.1 writer remain optional and explicitly enabled.

`CONTEXTOPUS_V11_PYTHON=/path/to/isolated/old-writer/python` enables the real 1.1
writer migration test. Its source is pinned to commit
`ff711966921c562f22aa7bcdb9358a871c8d4282` of Core. A manual CI workflow keeps this
separate interpreter and migration check alongside Linux/macOS/Windows validation.
CI uses the explicit compatible Core ref `v1.5.0` by default; a manual input can
select another compatible candidate. Remote CI and native operating-system
acceptance must be reported separately. The release workflow builds ZIP/checksum
assets and uploads them for review; publication is a separate action.

The independent Python package can be built with `python -m build`; installation
as a Core module uses the folder/ZIP builder and the common lifecycle instead.

## Release preparation

Run `python scripts/build-release.py /path/to/fresh-output`. The module, Python
package and managed documentation versions must agree. The two ZIP names have
identical contents and both appear in SHA256SUMS. No build command installs a
module, changes a live data root, creates a Git commit or publishes a release.

`install.sh` and `install.ps1` only forward the selected root and preview flag to
the installed Core lifecycle. Linux/macOS wrapper tests use a recording Core
command; PowerShell checks run where `pwsh` is available. Native Windows/macOS
results are not inferred from Linux simulation.

The English-content exception is intentional Unicode test data. UTF-8/CRLF
preservation, Unicode identities/filenames/native links, multilingual retrieval
fixtures and host-protocol roundtrips retain non-English inputs and matching
assertions. Repository prose, comments, user-facing messages and shipped guides
are English. See the exact fixture inventory in [the language audit](language-audit.md).
