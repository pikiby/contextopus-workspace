# PCW 0.7.1: topic materials and compact operations

> Privacy: personal paths and trace/material identifiers are anonymized; reported measurements are unchanged.

Verified on 2026-09-16 against the 0.7.1 source candidate. This report supersedes the
0.7.0 workflow assessment for the changed APIs; historical reports remain evidence
of their own versions.

## Adopted behavior

The primary divides substantive returns into coherent topic materials before saving.
MCP generates their filename-IDs and accounts for every block. There is no required
duplicate full-return knowledge file. The primary reads and assesses the saved
blocks, connects related materials, and corrects or consolidates them itself.

Only afterwards does a semantic helper choose embedding boundaries within each
saved topic. A batch sends numbered texts, without their memory coordinates. Its
boundary arrays are control records, not knowledge. Search continues to select whole
topic materials; this release does not add snippet retrieval or a fixed file-size cut.
Choosing useful topic scope remains the primary's semantic responsibility.

Rules retrieval is read-only and separate from assignment preparation, assessment,
transition and completion. Startup combines state reads. Operational replies avoid
repeating rules or the entire ledger. Batch operations and `check_id` binding reduce
repeated requests; a still-valid tools map can be reused.

## Regression and packaging checks

The complete suite passed **171 tests and 73 subtests**, with **6 opt-in skips** and
7 dependency deprecation warnings. The run took 29.27 seconds in this environment.
The skipped tests require explicit host/model integrations; they are not passing
evidence. Plugin and skill validators and `git diff --check` also passed.

New checks cover generated block IDs, exact retries, explicit revision-checked
corrections, partial writes and restart recovery, atomic read accounting, batch
boundary coverage and revision validation, and removal of technical knowledge
receipts. Legacy search returns readable filename IDs without migrating files or
re-embedding. Known invalidated revisions warn on direct read and are excluded from
ordinary search. A linked clarification alone cannot resolve an unchanged bad body.

The correction guard checks that affected bodies changed or were removed; the
primary must still verify the correctness of the new content. This is not automatic
semantic fact checking.

## Actual native-agent and MCP cycle

The manual driver is `tests/native_primary_memory_probe.py`. It uses a real stdio
MCP server and deterministic test embeddings in `/tmp/pcw-071-native`; it does not
touch project knowledge or download/load embedding model weights.

Two native `gpt-5.6-luna` agents with low reasoning received standalone tasks:

- `/root/topic_author_probe_071` returned a description from supplied synthetic
  facts, then a requested wording correction. The initial phrase incorrectly joined
  camera pan/zoom to the content panel; the primary identified the discrepancy.
- `/root/boundary_probe_071` received only the two corrected numbered texts and
  returned `[{"text_number":1,"boundaries":[4]},{"text_number":2,"boundaries":[3]}]`.

These are the executor addresses returned by the native collaboration tool, not
invented UUIDs. The primary performed all MCP writes. It saved the original return
as two topics, read them, marked the affected topic, obtained the correction,
saved/read the corrected topics, replaced the old materials, and accepted the
clarification. An initial driver merge call omitted required target bodies and was
rejected; supplying those bodies allowed the idempotent retry to complete.

Final observed state:

- exactly two knowledge files: `00000000000000000000000000000002.md` (layout storage)
  and `00000000000000000000000000000004.md` (HTML export);
- the first links to the second; both exact corrected bodies were read back;
- both indexed, one literal embedding part per topic, `pending_count=0`;
- old incorrect material removed, discrepancy resolved, task completed;
- no extra monolithic return or technical-report knowledge files.

The native returns establish behavior on this small supplied-facts fixture, not
general source-investigation quality or technical worker isolation. Access remains
prohibited at the assignment level, as requested. The driver exercises stdio MCP;
installed-plugin verification through a fresh Codex host is a separate opt-in check
in `tests/test_codex_host.py`.

## Measured response overhead

Reproduce with `python3 tests/measure_protocol_responses.py --baseline v0.7.0`.
The script executes historical and current workflow/ledger code for an equivalent
successful four-operation sequence. It fixes IDs and clocks, uses in-memory state,
and equalizes rules paths. The task completes in both cases with the same worker
launch message.

| JSON response body | 0.7.0 bytes | 0.7.1 bytes |
| --- | ---: | ---: |
| Prepare assignment | 5,704 | 1,495 |
| Assess result | 1,972 | 143 |
| Transition | 1,702 | 68 |
| Finish | 2,694 | 64 |
| **Total** | **12,072** | **1,770** |

The response bodies are **85.34% smaller** in this fixture. Both cases execute four
operations. This measures canonical UTF-8 JSON body bytes, not model tokens, elapsed
task time or a reduction in all tool calls. Requests, MCP envelopes, setup, binding,
save/read bodies and separately requested rules are excluded. No latency, embedding
quality, peak-memory or end-to-end performance claim follows from this measurement.

## Upgrade scope

The MCP surface replaces `pcw_action_requirements` with `pcw_rules_get`,
`pcw_assignment_prepare`, `pcw_result_assess`, `pcw_transition` and `pcw_finish`.
`pcw_save_result_blocks` replaces `pcw_save_subagent_result`. Existing Python
compatibility methods are retained for callers and historical tests, not advertised
as the current MCP interface. Restart the Codex thread after plugin installation.

This release does not automatically split, delete, migrate or repair existing user
knowledge. It also does not establish why any previously observed workspace was
empty. No model downloads or embedding-provider changes were needed for these checks.
