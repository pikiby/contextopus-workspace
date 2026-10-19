# PCW 0.14.0 verification

Verified on 2026-10-09 against the local source candidate. This report does not describe an installed or published release.

## Results

| Check | Result |
| --- | --- |
| Complete Python suite | 264 passed, 5 skipped, 64 subtests passed |
| Native Codex MCP and candidate skill discovery | 2 passed |
| Packaged skill validation | All eight skills passed |
| `git diff --check` | Passed |

The complete suite took 76.03 seconds. Its seven warnings concern existing LanceDB API deprecations. Five opt-in tests were skipped; the two host integration checks were then run explicitly. No paid model-driven behavioral run was performed.

## Reproduction

With the plugin's Python dependencies available:

```sh
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONPATH=plugins/persistent-context-workspace \
python3 -m pytest -q tests

PCW_TEST_CODEX=1 PCW_TEST_SOURCE=1 \
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONPATH=plugins/persistent-context-workspace \
python3 -m pytest -q tests/test_codex_host.py \
  -k 'tools_are_loaded or native_pcw_skill'
```

This workstation supplied dependencies through its private PCW package directory in `PYTHONPATH`. LanceDB tests required execution outside the filesystem sandbox. Native host checks used a temporary source-candidate installation and temporary contexts; they did not update the user's installed plugin.

## Covered behavior

- Category filtering before top-N; default, multiple and all-category search; cross-context reads and links without changing active instructions or write scope.
- Shared embedding profile rebuild, concurrent contexts and reconciliation that preserves other categories.
- Stable YAML identity, descriptive Unicode filenames, renames and category moves that reuse unchanged vectors; duplicate identities and destination collisions are rejected.
- General `sources` references, repeated-read declarations, legacy syntax normalization before review and migration that preserves scalar text, comments and CRLF.
- Migration from real legacy fixtures, including original filename IDs, local indices and immutable prepared proposals. Existing work and partially applied proposals resume after migration.
- Interrupted file transformations, failed indexing, snapshot validation, retry and missing-ID recovery where the indexed path identifies the original card unambiguously.
- Two published MCP tools, native stdio initialization and discovery of all eight source-candidate skills.

The runtime validates storage, revisions and declarations. Fresh source retrieval and immediate routing to the upgrade procedure are agent instructions; these tests do not establish that every model always follows them. The suite does not test automatic consolidation of separate memory roots, which is outside this implementation.

See the [design](context-workspace-design-v4.md) and [storage/API contracts](context-workspace-v4-contracts.md).
