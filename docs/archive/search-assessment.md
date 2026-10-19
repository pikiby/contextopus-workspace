# PCW search assessment: frozen v2

## Decision and method

Accepted calibrated starting profile **only for this frozen PCW corpus**:

```json
{"N":10,"d_max":0.45,"min_jump":0.01,"multiplier":2.0,"metric":"cosine"}
```

Product defaults remained unchanged. Another model/metric/corpus needs a separately frozen query set and calibration. CPU profile: sentence-transformers, Qwen/Qwen3-Embedding-0.6B revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`,1024 D,float32,L2 normalization,qwen3,local_files_only=true, query instruction `Given a query, retrieve relevant technical documentation.`

Corpus:8 materials,84 literal heading-split parts from guide.md, PCW skill and six references. Literal fidelity checked; real helper semantic splitting is evaluated separately. Frozen labels:24 calibration queries(16 positive/8 negative),12 held-out(8/4). Positive succeeds if at least one complete answer_set appears. Duplicate guide/reference entries count only towards diagnostic exhaustive_relevant. c12/h07 need two materials from separate references.

Precommitted selection:0.5×complete-answer-set rate+0.5×negative rejection rate; ties prefer fewer mean returned materials, lower N, then lexical d_max/min_jump/multiplier. Exhaustive recall, old evaluation and held-out queries do not choose the profile. Runtime and assessment use N→d_max→first median-positive-gap→material dedup.

## Evidence

All paths below are historical under `.pcw/project-update/.pcw-runtime/verification/qwen/`.

| Artifact | Observation | Limit |
| --- | --- | --- |
| rerank-v2-corrected-20260915T032000Z/baseline_verification.json | Fixture SHA and 8 source hashes matched;84 rows, finite1024 D vectors. | Existing table reused; corpus not re-embedded. |
| Same directory calibration_grid/report.json |216 parameter sets; calibration complete/rejection/score0.875/0.625/0.75; held-out0.75/1.0/0.875. Russian positives2/4, English4/4. | Three calibration negatives returned materials; limited corpus. |
| run-20260915T024000Z/report.json |32 available CPUs; process limited4 threads,10 GiB address space; peak RSS4,035,012 KiB, model load4,583 ms,84-part indexing157,134 ms. | One host/process. |
| probe-20260915T025100Z/probe.json | Default 32-longest-part batch allocator failure after 68 s, peak RSS9,182,924 KiB(8.76 GiB). | Reproduced process failure, not reboot cause. |
| probe-batch1-verified-20260915T032100Z/probe.json | Same 32-part batch succeeded in116,441 ms, peak RSS4,006,292 KiB, vectors within tolerance. | One success does not explain reboot. |

The failed default batch used unchanged product settings,4 threads and 10 GiB limit. Public contract returned EmbeddingError; no fallback provider. Intermediate `probe-batch1-20260915T031500Z/` encoded successfully (events.jsonl completed wall_ms114678.056) then assessment instrumentation raised NameError for missing math. It neither replaces nor cancels the original allocator failure. Verified follow-up completed and matched baseline tolerance. None of these artifacts records reboot, kernel/OOM logs or correlated host state, so no reboot cause is established.

## Reproduction

Runner `tests/qwen_search_assessment.py`; historical commands for a future separately authorized run, **not executed while writing this report**. Every output directory must be new. Do not run both modes together; each uses a local pinned snapshot.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/qwen_search_assessment.py \
  --rerank-v2 \
  --baseline .pcw/project-update/.pcw-runtime/verification/qwen/run-20260915T024000Z \
  --v2-fixture .pcw/project-update/.pcw-runtime/verification/qwen/queries-v2-corrected-corpus-hash.json \
  --v2-fixture-sha256 2e217c01353b9e181c2020737ef97a354e03a1517f7ecd84cb448155ce64136c \
  --output /path/to/new-rerank-output

PYTHONDONTWRITEBYTECODE=1 python3 tests/qwen_search_assessment.py \
  --probe-default-batch \
  --baseline .pcw/project-update/.pcw-runtime/verification/qwen/run-20260915T024000Z \
  --output /path/to/new-probe-output
```

rerank-v2 reuses frozen baseline parts/vectors and embeds only v2 queries. probe-default-batch skips retrieval assessment and tests the product32-part batch. This is retrieval/resource evidence, not whole-project acceptance.
