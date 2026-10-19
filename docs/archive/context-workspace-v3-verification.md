# PCW historical version verification

Personal paths and trace/material identifiers are anonymized; measured results are preserved. These are historical release checks, not fresh acceptance of the current module.

## Candidate 0.13.0 — 2026-10-07

Explicit create_required_sources creation settings were tested for both values, absent choices/invalid types, preserved existing rules and retries of previously accepted creation requests, including unfinished ones. Body required_sources blocks survived repeated read/read_many exactly. This verifies storage, not an agent's actual external-source requests; procedures/MCP descriptions require fresh source checks for every receipt.

Added pcw-analyze and renamed pcw-instructions to pcw-edit-instructions, aligning display/invocation names. All eight skills passed quick_validate.py and link/package checks. Journal checks exposed read results, declared link traversal and errors without claiming knowledge use. Focused: **62 passed**. Full: **251 passed, 5 skipped, 64 subtests**, seven LanceDB deprecation warnings. Skips were opt-in real-agent checks. Contracts tested real stdio and two-tool registration. Sandbox LanceDB hangs were stopped; successful checks used temporary storage and deterministic embeddings outside that sandbox. No publication, installation, user-context changes, universal model-compliance or token-savings claim.

## Default material limit 0.12.1 — 2026-10-05

New templates set1024 tokens. Tests covered exact 1024/1025 boundary, another explicit value, null and old missing-setting fallback; creation took the number from the template without a question. Full run:239 passed,5 skipped,64 subtests plus one deliberately large-body test exceeding the new default. That review test explicitly disabled the limit through instruction editing; focused cycle/workspace rerun: **62 passed**. No other full-run errors. Skill validation passed. Locally installed 0.12.1 through Codex manager; all **43 files** matched sources; installed template and creation procedure were checked. Existing contexts were not rewritten.

## Two MCP tools 0.12.0 — 2026-10-05

Final full run: **237 passed,5 skipped,64 subtests**, seven pre-existing LanceDB warnings. Tests covered operation allowlist, read/write division, types/extras, no storage opening on bad requests, approvals/revision conflicts and exact SQLite operation/error names. Real stdio exposed only pcw_query/pcw_execute; creation, search/read, temporary results, proposals/apply, instructions, derived-store recovery and finish passed. All 42 schemas were available through describe without creating files. An early isolated stdio timeout passed on retry; stderr was moved from an unread pipe to a file, then the complete suite passed.

Separate Codex detected the two tools/seven skills and used host API. A real model created a context in **106.67 s**, calling describe then workspace_create itself; coordinates, instructions, PROJECT, empty knowledge and absence of startup were verified. This is one scenario, not universal behavior; two-turn approval and Qwen were not rerun. The first model run timed out. Another exposed a test-copy defect: skills were copied without relative default_instructions resource; the model found the source template and created successfully. Harness now copies package/resources. Public trace diagnostics: `/tmp/pcw-0120-host-traces/`, not knowledge. Seven skills validated; local installed 0.12.0 had43 exact files and dependency readiness. The current session called the new describe successfully. Format stayed1; no user contexts were created/updated.

## Fix0.11.2 — 2026-10-02

Full **218 passed,4 skipped,64 subtests**; focused workspace **29 passed**. Skips were optional host checks; no new model run. Fresh-process reads retained both migrated tasks and proposal states. Tests checked current-format no-op, corrupt proposal, and missing/empty/placeholder/present PROJECT, including repeat-apply diagnostics. Source stdio upgrade_plan on actual data-platform reported format 1, two unfinished works (prepared runbook merge and applied retention-rule edit), no blockers and populated PROJECT. All 17 selected source/control hashes matched before/after; index, Git and old backups were excluded. PROJECT had been filled by another session and was only read. Skill validated. This source-only prerelease check did not replace installed 0.11.1; publication/install verification were separate.

## Migration0.11.1 — 2026-10-02

Full **213 passed,4 skipped,64 subtests**, with a separate final workspace test for interrupted-transfer diagnostics. No new model run. Covered prepared/applied state preservation, fresh-process continuation, approval preservation, tmp/control conflicts, unsupported states/damaged proposals, interrupted snapshot, same-copy resume and corrupt-backup rejection. Transition0→1 on a temporary copy of data-platform preserved both unfinished works and all 9 source hashes; prepared remained prepared, applied remained applied, no pending index; workspace/control snapshots checked. Original user context was unchanged. Skill validated; existing dependencies, no model downloads; LanceDB required execution outside the restricted sandbox.

## Original0.11.0 report — 2026-09-30

Timezone America/Argentina/Cordoba. Local candidate sources, disposable workspaces and separate Codex environment were checked; live MCP stayed0.10.0, with no candidate publication/install in this run.

| Plan stage | Implementation and evidence |
| --- | --- |
| Contracts | Format 1, explicit creation, independent reads, four policies; contracts/workspace tests. |
| Compatibility | workspaces.py inspect/plan/apply0→1, backup/resume; old temporary data, exact preservation, lost receipt retry. |
| Reads | read_guard, optional cycle_id, pcw-search; no creation or implicit stale-index repair. |
| Project/tasks | documents.py prepare/apply and per-task revisions; parallel tasks, conflicts, continuation, finish preservation. |
| Policies | policies.py and CycleRuntime modes; four combinations plus existing instruction/finish checks. |
| Topics/links | Optional limit, descriptions/targets, ordinary split; needs_split before writes, preserved conditions/redirected links, metadata-only no embedding. |
| Git | Exact snapshots/queue/local commits/list/diff/read/retry; no-op tree, failure and retry after later edits. |
| Analytics | SQLite dispatcher/file snapshots/two import adapters; errors, large responses, unknown outcome, redaction, threads/processes and other sessions. |
| Procedures | Seven skills, distinct audit/rework, structural audit without repair, split/link handling, actual Codex discovery. |
| End-to-end | MCP contracts/stdio, real agent cycle and byte-verified package. |

Full pytest: **203 passed,4 skipped,64 subtests**, seven existing LanceDB warnings. Added three-process check: **1 passed**,15 completed SQLite operations, no loss/unknown outcomes. Real stdio covered contracts and two Python-selection modes. Seven skills validated. Separate Codex skill discovery: **1 passed**. Real agent `test_agent_retains_tmp_and_waits_for_human_approval`: **1 passed,258.21 s**; found/read the fact, saved tmp, searched results and prepared changes; before the separate approval message it neither applied nor finished, afterwards it saved and cleaned tmp.

Existing dependencies were used from an anonymized `/home/example/.local/share/persistent-context-workspace/packages/cpython-312-linux-x86_64`; file/index tests used deterministic vectors, with no new Qwen quality/speed measurement. LanceDB connect hung inside the normal sandbox and completed with temporary data outside. Host run used `PCW_TEST_CODEX=1 PCW_TEST_SOURCE=1`, separate app-server MCP/skills, no global Codex install/configuration change. One successful Codex scenario does not prove all-model compliance.

Candidate `dist/persistent-context-workspace-0.11.0-candidate.zip`:43 installed files, no build/egg-info/bytecode; testzip and every source-byte comparison passed. SHA256 `69bea8ff28a1df93a80419ed7bb47eb20aa1db8be4a255fca2e4fa4e2bc362ee`.

No real user-context format migration or project-update instruction-policy change occurred. After explicit approval, proposal `d8b57ed6db9e4acc8ee7474927530589` / revision `27494285aea368327fd86c939a15b74ff9c78002ff8ced400e0be5e392e893d7` updated/indexed24 materials, preserving IDs/links. Workspace total 28; pending/needs_correction/unregistered0; search ranked the SQLite topic first. Cycle `890ac7f5f9644da39f563756589b0a79` finished and cleaned its tmp.

SQLite sees dispatcher calls and available revisions, not missed calls, pre-dispatch schema errors, unobserved external actions or proof of human approval origin. Arbitrary unlabelled secrets may escape filters. Git, file writes, indexing and journal outcomes are separate, without one atomic transaction.
