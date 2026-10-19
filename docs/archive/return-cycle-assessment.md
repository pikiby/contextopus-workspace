# PCW 0.7.2: ordinary return cycle

> Privacy: personal paths and trace/material identifiers are anonymized; reported measurements are unchanged.

## Observed problem

The 0.7.1 note-creation task supplied for review ran from 2026-09-16
23:45:18.912Z to 23:58:14.416Z: **775.504 seconds**. It produced a useful
external note, but also redundant operational knowledge, a follow-up for omitted
content, repeated reads and a wrong executor binding. The four-response size
comparison in the previous release was not a measurement of this normal task.

Reproduction requires the original measurement logs, which are not distributed.
Replace the example paths below with your own logs; the command does not scan
session directories or export message bodies:

```sh
python3 tests/measure_native_cycle.py \
  --start 2026-09-16T23:45:18.912Z --end 2026-09-16T23:58:14.416Z \
  /path/to/primary.jsonl \
  /path/to/author.jsonl \
  /path/to/splitter.jsonl
```

| Observation | Original task |
|---|---:|
| Model responses, primary / author / splitter | 37 / 12 / 1 |
| MCP calls | 36 |
| MCP-reported runtime, summed | 14.233 s |
| Host MCP intervals, summed | 230.493 s |
| Primary input tokens, summed across requests | 2,475,869 |
| Primary cached input tokens | 2,322,688 |
| Primary uncached input tokens | 153,181 |
| Primary output tokens | 15,102 |
| Primary per-request input range | 44,234–98,971 |

Token sums contain repeated request input, not one context of that size. Host
intervals include queues, review and transport. The explicit first approval
timeout accounted for 90.005 seconds; other unexplained host delay must not be
labelled approval time without evidence. Separate trace inspection found 153.159
seconds between preparing the splitter handoff and its native launch; the
splitter itself ran about 8.135 seconds. The launcher accepts message text, not a
prepared-message reference.

## Implemented changes

| Failure in that task | Current behavior |
|---|---|
| Missing full note required a knowledge receipt before follow-up | `return_record` retains incomplete/error state in control and permits continuation; acceptance remains blocked |
| Operational checks duplicated a complete artifact in knowledge | Control-only complete returns can be assessed; instructions distinguish verification evidence from reusable topic facts |
| Save, read, link and prepare repeated the same bodies | `result_store` saves chosen topic bodies and links, verifies disk read-back and returns bodies/revisions once; explicit assessment stays separate |
| Wrong inherited parent ID could only be worked around | MCP binding requires native evidence; conflicts persist and `assignment_rebind` repairs exact assignments and metadata with history |
| Completed assignments were restored at startup | Startup returns outstanding work; full history remains explicit |
| Current writes required an immediate separate read | The write verifies actual disk content before returning |
| Map publication repeated the submitted profiles | MCP publication returns a compact receipt; startup already supplies reusable profiles |
| Completion and transfer overhead was obscured by a tiny benchmark | Whole-cycle measurement separates elapsed time, per-thread model usage and host/runtime tool timing |

Topic division and factual acceptance remain primary judgments. The implementation
does not claim to infer semantic topic boundaries automatically. A short coherent
topic is valid; an entire multi-topic artifact wrapped in a code fence is not a
substitute for topic division. Existing user knowledge is not migrated or cleaned.

## Verification

Regression tests exercise a complete temporary task through substantive two-topic
storage, bidirectional links, disk read-back, explicit assessment, within-topic
boundary submission, indexing and finish. Another cycle records an incomplete
return, obtains its continuation, accepts control-only evidence and finishes with
zero knowledge files. No model is needed for these protocol fixtures.

Failure tests cover link validation before writes, corrupted disk read-back,
partial link writes with stable-ID retry, wrong-ID reuse after a conflict,
provenance correction including interrupted multi-file repair, and rejection of
shell-environment identity evidence. Real stdio tests exercise the exposed MCP
schema, required evidence, composite save/read/link and recovery. Plugin and skill
validators are run separately. The complete suite passed 181 tests and 73
subtests with six opt-in skips; seven existing dependency deprecation warnings
remain. Final coverage includes unchanged-result retries after completion and
continuations bound to a fresh native executor.

**Limits:** these are implementation and protocol checks. They do not establish a
new autonomous task duration or guarantee that a model follows every instruction.
Prepared-message forwarding without regeneration still requires native host
support. PCW records the primary's identity evidence; it does not authenticate a
host session or impose OS/tool access isolation. A before/after latency claim needs
another comparable whole native task with factual outcome review.
