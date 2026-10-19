# PCW 0.8.0: memory-only implementation assessment

> Privacy: personal paths and trace/material identifiers are anonymized; reported measurements are unchanged.

Date: 2026-09-17. Governing specification: [context-workspace-design.md](context-workspace-design.md).

## Implemented behavior

- The primary uses saved context first and saves useful findings during work. External work may be direct or optionally delegated; PCW does not register or launch workers.
- The MCP surface contains 23 memory operations. Environment discovery, model maps, task and assignment ledgers, executor binding, result acceptance gates and manual embedding-boundary submission are removed.
- Create/update accept ordinary Markdown topic bodies. The runtime checks the saved body and metadata, tokenizes automatically, embeds overlapping literal windows and publishes current-revision LanceDB rows. No second body transfer or splitting agent is required.
- The default is local Qwen3-Embedding-0.6B with its own tokenizer and 512-token windows / 64-token overlap. Local alternative models, Ollama and compatible embedding APIs remain configurable. Remote sources require an explicit compatible tokenizer; credentials stay in environment variables.
- The primary retains topic organization, exact links, factual corrections, compaction and Current. The plugin does not split a multi-topic report into knowledge files.
- Saved knowledge survives indexing failure and is retryable through reconcile using the same ID. Profile or chunking changes rebuild an inactive table before switching. Source revision, changed settings and incomplete rows are checked.
- Existing knowledge, Current, exact IDs and links are preserved. Legacy derived indexes are replaced on opening and reindexed by reconcile. Old remote configurations require a tokenizer. Known incorrect legacy revisions remain excluded from search until corrected. Old task/controller records are not resumed or edited.

## Regression and real model checks

The complete local suite passed **119 tests and 64 subtests**, with **3 opt-in Codex host tests skipped**, in 24.52 seconds. Coverage includes CRUD, links, compaction, path safety, Current, memory Git operations, provider adapters, MCP/stdio contracts, automatic token windows, pending retry after restart, source revision conflicts, interrupted indexing, index-format upgrade and atomic profile switching. Tests requiring the retired controller were removed; applicable memory cases were retained.

A separate temporary-workspace cycle used the real cached `Qwen/Qwen3-Embedding-0.6B`, CPU and production LanceDB/tokenization, with Hugging Face networking disabled. A 2,401-character Russian Markdown material containing a table, code and Unicode produced two windows with token positions **[0, 512)** and **[448, 843)**. Exact saved content was verified. Creation took **5.349 s**, search **0.141 s**; creation, search, a second material, links, update and Current completed in **6.311 s**. Final status: two materials, zero pending. No assignment/task file was created.

These timings describe one local runtime/model cycle. They are not whole-agent latency measurements or a calibrated retrieval benchmark. The 512/64 defaults and search thresholds are engineering settings.

The suite was run outside the restricted Linux sandbox because LanceDB's connection stalled inside that sandbox; the same tests completed outside it. No dependency or model download was performed.

## Installed-host verification

Installed through the existing Git marketplace using `codex plugin marketplace upgrade persistent-context-workspace` and `codex plugin add persistent-context-workspace@persistent-context-workspace`. The installer reported version **0.8.0** at `/home/example/.codex/plugins/cache/persistent-context-workspace/persistent-context-workspace/0.8.0`.

All **3 opt-in Codex host checks passed in 17.47 seconds** with `PCW_TEST_CODEX=1 PCW_TEST_QWEN=1`. A fresh real Codex app-server loaded the exact 23-tool inventory and enabled PCW skill. Tests exercised create/read/update, Current, automatic real-Qwen indexing, search and links in temporary workspaces. The installed manifests, launcher, runtime and skill files were byte-compared with the source before the checks. No plugin-root environment variable was injected into the host.

The old long-lived conversation is not automatically reconnected by this installation; a new Codex thread loads the revised tool surface and skill. Existing user workspaces were not opened for this test.

## Limits

- Optional worker restrictions are instructions, not a demonstrated cross-platform filesystem/tool isolation mechanism.
- Token windows may cross paragraphs, tables or code; literal source is preserved and overlapping text supplies nearby context. Unicode boundaries can slightly increase overlap.
- Alternative HTTP adapters are covered by controlled protocol tests, including automatic tokenization/indexing. No external paid embedding API or live Ollama server was called in this release check.
- No end-to-end language-model agent turn, mobile/Windows runtime or retrieval-quality benchmark was performed. Codex host integration is a real tool/skill transport check, not agent-behavior evidence.
- User knowledge workspaces are not bulk rewritten or reindexed merely by installing the plugin. Large existing reports are not automatically reorganized into topic files.

Historical assessments and the old Qwen corpus script describe their original versions. They do not establish the quality of the new token-window retrieval scheme.
