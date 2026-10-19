# English repository and Unicode test fixtures

Product documentation, shipped procedures/skills, source comments and user-facing
messages use English. Historical archive documents are English renditions of the
original evidence; dates, measurements, identifiers and applicability limits are
preserved rather than presented as current release acceptance.

Non-English strings below are intentional **test inputs and expected outputs**.
They exercise UTF-8/CRLF preservation, Unicode context and filename identity,
native-link aliases, multilingual query corpora or actual host-protocol text
roundtrips. Translating those strings would remove the property being tested.
They are not production messages or instructions distributed to users.

| File | Lines containing Cyrillic fixture data |
| --- | ---: |
| `tests/qwen_search_assessment.py` | 7 |
| `tests/test_base_context.py` | 13 |
| `tests/test_codex_host.py` | 25 |
| `tests/test_context_memory.py` | 1 |
| `tests/test_cycles.py` | 3 |
| `tests/test_embeddings.py` | 5 |
| `tests/test_hermes_verifier.py` | 2 |
| `tests/test_instructions.py` | 1 |
| `tests/test_legacy_identity_migration.py` | 2 |
| `tests/test_record_links_v13.py` | 23 |
| `tests/test_shared_v4.py` | 4 |
| `tests/test_upgrade_11_to_13.py` | 6 |
| `tests/test_utf8_dataflows.py` | 15 |
| `tests/test_workspace_settings_v4.py` | 3 |
| `tests/test_workspace_text_io.py` | 7 |

`qwen_search_assessment.py` deliberately labels its Russian/English query corpus
with language codes; original wording is part of the measured historical input.
The Codex host fixtures similarly keep multilingual user requests and literal
assertions. The other entries check exact byte/content preservation and native
Unicode paths, names, links or settings. Test comments and test descriptions are
English. These fixtures are excluded from the installable module ZIP.
