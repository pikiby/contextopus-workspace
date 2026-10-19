# Automatic indexing

Applying a proposal under the workspace's approval policy automatically tokenizes new or changed Markdown topics, embeds overlapping windows and updates LanceDB. Tmp returns and prepared proposals are not indexed. The agent supplies each proposed body during preparation; application uses that exact stored body without retransmission. No semantic helper, boundary arrays or splitting assignment exists.

Defaults are 512 tokens per window with 64-token overlap, using the selected model's tokenizer. These are engineering defaults, not a calibrated optimum. Windows advance by size minus overlap and preserve literal Unicode text; they may cross Markdown paragraphs, code or tables. Formatting and special tokens must fit the model's input limit.

Saved knowledge remains authoritative after an indexing failure. The apply returns `index_pending` or a partial result and keeps tmp. Fix the reported cause and retry the same proposal. Alternatively run `reconcile`, then retry the same apply to record its completion. Old vectors of changed bodies do not appear as current facts. Normal closure waits for applications/indexing to complete; explicit user cancellation reports their incomplete state.

Search covers permanent knowledge only and returns bounded unique IDs, paths and distances. Read suitable full topics and useful exact links. Links-only edits and moves retain embeddings. Changing tokenizer, window size or embedding space rebuilds an inactive table through `switch_embeddings`, then activates it after revision checks; failure preserves the previous active source/index. Startup and status never load model weights.
