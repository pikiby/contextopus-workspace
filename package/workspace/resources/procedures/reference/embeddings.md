# Embedding configuration

Read for explicit connection or profile changes, search settings, or embedding failures. Use the active configuration returned by `status` and the `switch_embeddings` schema. A user request for ordinary memory work does not select a paid service.

The profile belongs to the common memory root, not one category. A profile change rebuilds all compatible categories in its shared RAG. Establish that scope before switching. The first migrated context supplies its existing profile when no shared profile exists; subsequent contexts use the established shared profile and rebuild incompatible vectors.

## Local default

The default provider is `sentence-transformers` on CPU, with `local_files_only: false`. The profile uses `Qwen/Qwen3-Embedding-0.6B`, revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1024 dimensions, `float32` precision, `l2` normalization, `qwen3` input format, and a 32768-token configured input ceiling. The model is loaded lazily on the first embedding request and reused by the running MCP process. Before first use, follow [Runtime setup](procedure:reference/setup) to distinguish local dependency readiness from cached model availability and obtain any still-missing download permission. The initial model load may download weights; later calls can use its cache. For an offline installation, explicitly configure `local_files_only: true` after provisioning the cache. Missing dependencies or weights return an error; no remote fallback is selected.

Qwen queries receive `Instruct: Given a query, retrieve relevant technical documentation.` followed by `Query: ` and the user's search text. Document parts receive no retrieval instruction. Other embedding profiles may use `raw` or explicit `prefix` formatting; Qwen's instruction is not imposed on every model.

## Configuration shape

An embedding configuration has two objects:

- `provider`: connection `type`, local `device`, `cache_folder`, and `local_files_only`; or remote `base_url`, `api_key_env`, `timeout_seconds`, and optional `send_dimensions`.
- `profile`: `model`, `revision`, `dimensions`, `normalization`, `input_format`, `max_input_tokens`, and `precision`; `query_instruction` for Qwen formatting, or `query_prefix` and `document_prefix` for prefix formatting.

Supply the complete intended configuration, search settings and optional `chunking` to `switch_embeddings`. Search settings contain `N`, `d_max`, `min_jump`, `multiplier`, and `metric`. Numerical defaults are provisional. Choose thresholds for the selected profile and metric; do not describe them as calibrated without relevant retrieval evidence.

Supported remote provider types:

| Type | Endpoint | Input and response |
| --- | --- | --- |
| `ollama` | `/api/embed` | `model`, `input`, `truncate: false`; ordered `embeddings` |
| `openai-compatible` | `/v1/embeddings` | `model`, `input`; `data[].embedding` reordered by `index` |

Use explicit server coordinates. Store an environment variable name in `api_key_env`, never an API key in memory or configuration. A chat subscription or conversational model is not an embedding endpoint. A provider with a different protocol requires its own adapter.

The local adapter rejects oversized inputs before encoding; Ollama is told not to truncate. Compatible HTTP APIs have no universal disable-truncation field: the configured provider must reject overflow. A returned vector alone cannot establish that the server preserved the complete input. Invalid counts, dimensions, indexes, zero vectors, and non-finite values fail explicitly.

## Switching and errors

The runtime binds the active LanceDB table to the full vector-space profile. A different model, revision, precision, input format, dimension, normalization, or unverified computation source requires new vectors even when dimensions or model names match.

Switching rebuilds token windows and vectors into an inactive table whenever the embedding space or chunk settings change. It does not change knowledge, IDs or links. New provider, chunk settings, thresholds and table become active together after all source revisions are checked; failure retains the previous active source and table. Pending saved materials are included in a successful rebuild.

## Tokenization

Default `chunking`:

```json
{"chunk_size_tokens": 512, "overlap_tokens": 64, "tokenizer": {"type": "provider"}}
```

`provider` reuses a local sentence-transformers model's tokenizer. For a remote source, explicitly configure its matching tokenizer; do not use Qwen's tokenizer for an unrelated model. Supported specifications:

```json
{"type": "huggingface", "model": "Qwen/Qwen3-Embedding-0.6B", "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3", "local_files_only": true}
```

```json
{"type": "tiktoken", "encoding": "cl100k_base"}
```

The latter is appropriate only for a model documented to use that encoding. Hugging Face tokenization needs transformers and offset support; tiktoken needs the optional tiktoken dependency and its encoding cache. Missing tokenizer components produce pending indexing, not a silent substitute. Model calls do not choose boundaries. Window and overlap must be integers with `0 < overlap < size`; actual formatted inputs including special tokens must fit the model limit.

Changing only token settings uses the same `switch_embeddings` operation with unchanged embedding configuration and explicit new `chunking`. Source text is re-read by the plugin, not sent back by the primary.

Credential rotation does not change vector identity. Cache location, offline-loading preference, and transport timeout do not change the computed vectors. Different endpoints or execution settings are treated conservatively unless compatibility is established; matching a quantized model's name is insufficient.

Report the actual provider or dependency failure. Do not fabricate vectors, quietly shorten parts, or enable another provider to make a failed operation appear successful. The model cache, runtime configuration, and LanceDB data remain outside Git. Rebuilding vectors is separate from LanceDB index maintenance and disk cleanup.
