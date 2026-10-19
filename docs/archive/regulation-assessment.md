# Historical PCW procedure-text audit

Dated 2026-09-15. Scope: README, PLAN, pcw skill/references versus plugin-update-design. Historical roles/traces do not validate today's primary-only memory procedure; see [primary-memory assessment](primary-memory-assessment.md). This document assesses texts and a narrow guarded-save runtime/MCP contract, not native workflow acceptance.

## Conclusion and seven corrected discrepancies

Seven previously identified issues were corrected and reread; other reviewed governing requirements had no substantial textual contradiction.

| Area | Previous defect | Verified correction |
| --- | --- | --- |
| prepare-search:5 | Unconditional Codex-only fork_turns:none. | Require clean context where supported; Codex parameter is a host example. |
| README:38,PLAN:23,memory-operations:15 | Every rename claimed to change ID/path. | Same-basename a.md→nested/a.md changes path/search rows but preserves ID/links; basename change updates exact incoming links. |
| SKILL:37 | Every rename claimed to rewrite incoming links. | Same distinction, without re-embedding unchanged bodies. |
| server.py:53/rename MCP description | Same unconditional ID/link promise. | Match ID/path rules and same-basename move distinction. |
| action-requirements:9 | Confirming Current allowed first five-field write to slip until later work. | Primary selects all five from task, includes save via pcw_current_set in the first native assignment (bounded research included), then rereads. |
| action-requirements:67,memory-operations:45 | Source-commit prohibition was treated as local memory-history/consolidation prohibition. | Separate exact Current/knowledge consolidation/checkpoint from source Git; explicit PCW-specific restrictions remain binding. |
| SKILL:21,action-requirements:11 | Generic search/read omitted confirmed-empty-memory branch. | After status establishes empty knowledge, skip its search and assign only needed bounded source research; resume memory search when knowledge exists. |

## Guarded raw-save review

Runtime.py historical lines411/438/445 preserve original Markdown, require the same actual executor for replacement of the same filename-ID and matching expected_previous_revision. Replacement preserves links, renews UTC saved_at, sets pending_preparation and deletes old search rows. Exact same-executor/body repeat returns already_saved with original time. Dynamic server.py77–95 exports that exact signature. Skill/native-assignments/memory-operations/action-requirements/README require primary raw save→pcw_read before semantic decision/acceptance; memory/Git returns name actual affected filename IDs, changed Current path and status, rather than generic completion or stale lists.

Tests runtime 454–532 cover exact body/executor/CAS/links/new time/pending/stale/foreign executor/ordinary material/exact retry and authoritative _save failure. A controlled temporary reproduction failed _delete_rows after _save: call errored with new authoritative body/revision and old derived row between calls. Public read/search/exact retry enters _guard→_reconcile: read returns new pending body, search omits stale row and reports pending, retry returns already_saved. This is recoverable partial write, not full file+LanceDB rollback; that failure point was not in the original unit test and no full transaction is claimed.

Supplying expected_previous_revision when material_name is absent does not alter first-save behavior (runtime 423–459); no existing record means no revision comparison. Guarded replacement applies only to an existing raw material of the same executor. That parameter does not prove a deleted prior material was not recreated. Detecting deletion would require a distinct contract not established by guide/procedures.

## Verified textual correspondences

Primary builds current model map, assigns substantive native work directly and saves/reads raw results with actual executor ID. MCP is no controller/dispatcher/hook; no fixed bootstrap researcher/roles. Bounded research is needed only when concrete interfaces/interactions/change conditions/checks are missing; source paths/general task wording alone do not establish them. Primary selects/checks object, timing and observed signs from complexity; examples cover simple final check, interacting-component intermediate/final checks and validating research before dependent implementation. No checker is mandatory solely on return.

Current has five readable compact sections; HTML length markers supplement rather than replace Markdown. First native assignment saves primary-decided fields even for bounded research; later updates follow changed decisions/blockers, not every return. Confirmed empty knowledge skips nonexistent-memory search, without requiring research when facts already suffice. Candidate 060111 installed this wording;060139 reportedly exercised it, but final native acceptance was not yet evidence at this text review. Earlier installed traces remain historical.

Small helper literally splits only the supplied material; no source research, user-task execution, consolidation decision or navigation. Consolidation preserves meaning/conditions/uncertainty/table/code; no mandatory attribution/provenance preservation was added. Setup first uses available MCP, then bounded diagnostics, requiring specific permission for install/download; absence of another host is no defect. Codex mentions are verified examples. Capture existing artifact state before authorized regeneration/replacement, preserving discrepancy evidence without an automatic repair hook.

Finish order: primary decides content/links, worker applies content/Current, separate small helper prepares changed/new bodies, worker checkpoints exact changed memory paths when permitted, then actual status checked. Includes new checker/helper/checkpoint raw returns, each saved and read before decisions. Exact already-current same-executor delivery may reuse idempotence without a new pending copy; do not conceal new facts/errors/discrepancies. Pending preparation/checkpoint remains authorized unfinished work or a concrete authority/blocker; ready alone is not completion. Temporary memory location does not cancel required memory work and does not grant source commit/push authority.

Actual individual executor metadata is obtained before reading/interpreting substantive returns; missing/conflicting identity permits only targeted clarification before save. It is not a factual result check. Recheck native metadata before final return and resolve foreign-ID collision; CODEX_THREAD_ID is only the verified Codex-shell example, with other-host mechanisms allowed. Clean context/fork_turns remain host-specific, not a universal parameter.

Short cycle asks pcw_action_requirements immediately before each action and again before the next. Rules are distributed across assignment preparation, raw-result processing, transition and finish; MCP provides applicable instructions without dispatching lifecycle. A completed structural response/raw/status does not substitute for actual content/Current/preparation/checkpoint verification. Continue until completion or a concrete user-dependent blocker.

## Historical source fingerprints

The first 13 rows below identify the initial text/guarded-save review; the following9 identify the later readiness review. These SHA256 values identify historical inspected revisions, not current translated files.

| File | SHA256 |
| --- | --- |
| `.pcw/project-update/knowledge/plugin-update-design.md` | `f672ced41aa6fea7a2643f16794418a6cddb70bbf260c8acef03fc177fa635e1` |
| `PLAN.md` | `30c24dc9caed575ff5cb343ca4546a2ca35a10be06b0c8d05cb86c6453ad168a` |
| `README.md` | `e81987bbeb07bda8301b48e21630a129ef0d7a43c8d6863939c54e538dedb79e` |
| `skills/pcw/SKILL.md` | `3066986b129c2795ac1b51ff9d2183cf23d5850e9019a3e0f235f7f55a4b0c52` |
| `skills/pcw/references/action-requirements.md` | `59aca8eacecd8bd61eccc2df09fa6328711e0a667be03c71847fb1fcb49f6f96` |
| `skills/pcw/references/memory-operations.md` | `ece608bcb5c6c029cc7926172d872b91a0c736b477ea8458a1c7a67bd59d2bd2` |
| `skills/pcw/references/native-assignments.md` | `984938e03ded5a94a221991151f7009cbd44bc1c449b920597d7fde775f1f355` |
| `pcw_runtime/runtime.py` | `4798eb1289f37857c6bb177683de5a923e4df723e8fa5f6b67eccad152335942` |
| `pcw_runtime/server.py` | `f1051bf0bd8a06ab005b6e5dc44244bfdb6ca8ad9275ea66c7838c0810d2c2ab` |
| `tests/test_runtime.py` | `8445d1ca2b0997968c6dc42ba0aca06001f58f7f0eb15ea99c903b31c426c412` |
| `tests/test_mcp_workflow.py` | `8e142399ed355fdb164d12ac6ce733416ae8281e9dc4fcbf513e62413bfbf0bd` |
| `tests/test_contracts.py` | `427149b5fe4115e4b16fa84a87eebf2a9fb554173b67913f3b7e24687fb18158` |
| `tests/test_agent_workflow.py` | `ce59ba74443a9b16019624e0e616cd114c1dbf50624717f5d5844d5650b88ef5` |
| `.pcw/project-update/knowledge/plugin-update-design.md` | `f672ced41aa6fea7a2643f16794418a6cddb70bbf260c8acef03fc177fa635e1` |
| `plugins/persistent-context-workspace/pcw_runtime/agent_workflow.py` | `451ffe3895ff809927ce5acc59c70793e0124224c3aa3340dddad74504b032ad` |
| `plugins/persistent-context-workspace/pcw_runtime/server.py` | `36c42de20a88fd9df421c476c0ab1264ec3bf374f69c9691ed6d99d9aad2bd43` |
| `tests/test_agent_workflow.py` | `1c984ca788ad39611c9ab804e2fc9140c52167d7eada128bc180eb457d0a8301` |
| `plugins/persistent-context-workspace/skills/pcw/SKILL.md` | `2c4bead7a269d6d5ec178949e44559af4138e63eb70996f16094cdbd0b2c53d4` |
| `plugins/persistent-context-workspace/skills/pcw/references/native-assignments.md` | `6bd58f2fbcb8de69c6839db998fc8ec01a8469f2d83634730a8274bf08978cc2` |
| `plugins/persistent-context-workspace/skills/pcw/references/action-requirements.md` | `8926f9a67f8e00aaa04150587c07fe486369c733025b009619ed76c5bea753e5` |
| `README.md` | `96113f28caf08354c9a11a1050d9dd5a8c43f084414f8bf3ab0aa74db886eff3` |
| `PLAN.md` | `8a192e2c5107219651175db943e7da500e7cd3a7d1169cdf3d7b313cabed382c` |

## Limits of this review

Embedding/search algorithms beyond guarded-save recovery, model-map/action mechanics, Qwen outcomes and native runs were not assessed. Runtime/server were read only for pcw_save_subagent_result, without claiming general file/LanceDB atomicity. A simple native audit still left new raw bodies pending and omitted Current/checkpoint; text sufficiency did not prove execution. Earlier045633 used former installed instructions, had no first-assignment Current evidence under new wording and finished without checkpoint. A subsequent real cycle must demonstrate these operations, not merely new wording.

## Separate readiness clarification —2026-09-15

Static final review found no blocking issue. prepare_assignment now explicitly repairs missing prerequisites and rechecks the whole assignment before launch; ready:true still validates observable data only. agent_workflow.py565 fixed missing map guidance: saved environment→publish_tools_map; absent environment→environment_check. Tests202–265 cover corrected profile/recheck, instructions without an assignment and both missing-map branches. No circular ban on creating prerequisites: only prelaunch prepare_assignment requires ready, while creation/persistence remain available. process_result retains save→read; transition permits assigned checks; finish requires applicable checks/memory work. New assignments receive their own readiness validation.

Corrected interruption-assessment.json for 105645 records primary ready239→launch245 and ready900→launch906. Failures333/357 belonged to an already-running worker with no subsequent launch, so the earlier allegation of primary launching after refusal was unsupported. Reviewer read retained prelaunch_readiness rather than reexamining raw trace. Coordinates/SHA/actor audit are in historical acceptance/readiness-response-review.json. Reviewer ran no tests/model/MCP/native host; this static result is not evidence of a fresh native run.
