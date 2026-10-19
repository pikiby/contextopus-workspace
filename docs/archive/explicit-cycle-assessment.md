# PCW 0.9.0 verification

Date: 2026-09-18. Scope: the local source candidate implementing `context-workspace-design-v2.md`. No Git publication or installed-host upgrade is established by this assessment.

## Completed checks

- 139 tests passed, plus 64 subtests; four opt-in Codex-host tests were skipped.
- The 20 new cycle tests use actual filesystem storage and LanceDB with injected deterministic embeddings. They cover fresh Current, temporary/permanent separation, proposal revision changes, human-confirmation argument boundaries, full cleanup, independent cycles, explicit restart, rejection/no-change closure, cross-links, merges/splits, conflicting bodies and incoming links, unchanged-text indexing, partial writes, restart after pending indexing, cleanup failures, tampered plans/reviews, literal CRLF/Unicode, missing-storage recovery and symlink rejection.
- The launcher test runs the packaged MCP server over real stdio in a temporary directory, using both interpreter-selection modes. It checks the exposed API, cycle routing, proposal preparation/application, read-back, retained tmp, derived-storage recovery and cancellation cleanup.
- Existing storage, token-window, provider, startup, package and navigation regressions remain in the passing suite.
- The skill validator passed. Package versions agree at 0.9.0. `git diff --check` reported no whitespace errors.
- The distribution ZIP was reopened and every packaged file compared byte-for-byte with the source candidate; its SHA-256 is stored alongside it in dist.

The deterministic suite used the existing private Python dependency directory, offline model settings and temporary workspaces. LanceDB stalled during connection inside the filesystem sandbox; the tests passed outside that sandbox. Seven warnings concern existing deprecated LanceDB APIs.

## Limits

The revised opt-in model test now uses two turns: an ordinary question must produce temporary findings and a proposal without permanent writes, then a separate user approval must lead to application and closure. This real-model scenario has been updated but not run for this candidate. No claim of universally reliable model compliance follows from the deterministic tests.

Human invocation and the origin of approval remain procedural: a standard MCP server cannot authenticate whether an agent-supplied string was actually written by the user. The runtime enforces an active cycle, the exact prepared revision, affected-file conflict checks and operation state. It does not provide filesystem isolation from an agent using other tools.

Multi-file application is resumable, not an all-or-nothing filesystem transaction. A partial write reports its state and retains tmp; retry uses the same generated IDs. Explicit user cancellation can discard tmp after partial application and reports incomplete operation IDs without pretending to roll back knowledge.
