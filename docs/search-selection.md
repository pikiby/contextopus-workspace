# Search scope and result selection

Core and Workspace use one selection registry. Structural eligibility filters run before vector nearest-neighbor retrieval. The selected algorithm then cuts or pages that eligible candidate pool. `output: "materials"` groups document keys before selection and returns material metadata and descriptions in ordinary responses. The nearest fragment represents each document for ranking; its text is not part of the ordinary material response. `output: "fragments"` explicitly selects individual fragments, including their text. A key is `[module, collection, object_id]`; Workspace collections are context names.

Selecting another context as evidence does not activate its instructions. Workspace cross-context search uses the active context's search settings; `diagnostics.scope.settings_context` identifies that settings scope. `scope.contexts` identifies the contexts actually searched. Core can restrict `module` and `collection` separately. A null scope field means no restriction for that field.

## Installed algorithms

These are engineering defaults, not calibrated relevance guarantees. Distances are ranking measurements, not confidence scores. Inspect `core.algorithms` for the installed descriptions and parameter schemas.

| Algorithm | Parameters and defaults | Selection order |
| --- | --- | --- |
| `distance_jump` | `N: 20`, `d_max: 0.8`, `min_jump: 0.02`, `multiplier: 3.0` | Requested units → distance cap → first large gap over all remaining units → N result cap. |
| `distance_threshold` | `N: 20`, `d_max: 0.8` | Requested units → distance cap → N result cap. |
| `nearest_n` | `N: 20` | Nearest N requested units, without a relevance estimate. |
| `none` | `offset: 0`, `page_size: 20` | Distance-ordered page of requested units, without a relevance estimate. |
| `archivist` | `d_max: null`, `top_k: 20` | Requested units → optional distance cap → upper-median first jump → weak plateau cut → top_k result cap. |

All builtins use algorithm version `2.0.0` and selection contract `2`. Their default parameters and the configured algorithm are preserved. For materials, **every algorithm** receives one nearest representative per document key before any boundary, limit or page is evaluated. For explicit fragments, every part is a unit; Archivist also preserves fragment units. A technical candidate limit can still retrieve only parts of one document. Grouping cannot recover documents that retrieval omitted.

For `distance_jump`, at least three units must remain after the distance cap. The first gap at least `max(min_jump, median(positive gaps) * multiplier)` cuts the tail. This uses Python's arithmetic median: an even-sized set averages its middle pair. Equal distances do not contribute positive gaps. The result cap N is applied **after** gap evaluation. Diagnostics distinguish `insufficient_candidates`, `no_positive_gaps`, `no_jump`, and an observed `distance_jump`; a result cap is separately reported. Legacy parameter names use the current implementation rather than restoring the old fragment-first order.

`archivist` preserves the Archivist relevance procedure over the requested units. Repository/location families and default-branch preference are not inferred by Core; a module owns such domain rules. Its first jump uses the **upper median** positive gap times 3, with a minimum of 0.02. If more than three units remain, the weak-plateau pass uses `head = median(first three distances)` and `delta = max(0.02, median(positive gaps) * 2)`; this second median is the arithmetic median. Starting at index 2, it cuts before the first candidate whose distance exceeds `head + delta` and whose preceding local gap is below 0.01. `top_k` applies after both passes. With `d_max: null`, no absolute distance cap applies. For example, distances `[0.10, 0.11, 0.12, 0.60, 0.61, 0.62]` retain the first three; `[0.10, 0.11, 0.13, 0.15, 0.151, 0.152]` retain the first four through the weak-plateau rule.

For `none`, offset and page_size count distinct documents in material mode and individual parts in fragment mode. Material pages no longer repeat a document merely because it has several chunks. Stable pagination still requires the same indexed corpus and ranking; index changes or tied distances can change later pages.

## Configuration and origins

The common configuration object is:

```json
{
  "selection": {
    "algorithm": "distance_jump",
    "parameters": {
      "N": 20,
      "d_max": 0.8,
      "min_jump": 0.02,
      "multiplier": 3.0
    }
  }
}
```

Settings merge in this order:

1. Built-in defaults, with explicit migration of the active index's legacy search settings.
2. `<root>/core/configuration/settings.json`.
3. `<root>/core/configuration/modules/<module>.json`.
4. `<root>/core/configuration/contexts/<module>/<context>.json`.
5. Request overrides.

Partial parameters inherit only within the same algorithm. Switching `algorithm` clears parameters inherited for the previous algorithm and uses the new algorithm's defaults. Unknown algorithms or parameters, invalid types, nonfinite numbers and out-of-range values fail validation. A module's custom algorithm uses its declared JSON schema.

`core.settings` returns effective `values`, individual `sources`, and a revision. Selection origins use keys such as `selection.algorithm` and `selection.parameters.N`; examples include `algorithm.default`, `root.active_index`, `root.configuration`, `module.configuration`, `context.configuration`, and `request`. Legacy aliases include `searchN → N` and `distance → d_max` where the selected algorithm supports them. A modern parameter explicitly supplied in the same configuration layer wins over its legacy alias. Search-time legacy arguments appear as `request.legacy`.

Save changes through `core.settings_preview` followed by `core.settings_apply` with the exact `preview_revision`. The revision binds the configuration scope and `request_only` mode. A stale preview must be regenerated. `request_only: true` returns effective request settings without saving them. Supplying `selection` directly to a search also affects only that request.

Prefer algorithm parameters in new clients. Legacy Workspace `N`, `d_max`, `min_jump`, and `multiplier` are accepted only when supported by the selected algorithm. Core's legacy `distance` requires a `d_max` parameter; Core's legacy `limit` maps to `N`, or `page_size` for `none`. For `archivist`, use `selection.parameters.top_k` explicitly.

## Diagnostics and limits

Search responses expose the algorithm identifier/version, effective settings, eligibility scope, count units and the reasons that limited selection. Public compact diagnostics omit candidate snapshots, nearby rejected rows and the verbose settings-origin map; full evidence remains available through the search event.

- `eligible_documents` and `eligible_fragments`: indexed data allowed by structural filters, before nearest retrieval.
- `considered_fragments` and `considered_documents`: finite-distance retrieved rows and their distinct keys.
- `selection_units`: document representatives in material mode, individual parts in fragment mode.
- `selected_fragments`: returned fragment representatives (one per returned material in material mode).
- `selected_materials`: distinct returned document keys.
- `invalid_distance_fragments`: retrieved rows excluded because their distance is missing or nonfinite.

The older `eligible`, `considered` and `selected` fields remain compatibility aliases. Use explicit units for comparisons: compatibility `eligible` counts fragments in Core and documents in Workspace. Old evidence keeps the meaning its recorded version used; do not interpret an old `selected_fragments` count using contract 2.

Three independent limits must be distinguished:

1. **Candidate retrieval.** By default nearest retrieval considers the full structurally eligible indexed pool. Explicit `candidate_limit` (1–100000) caps fragments before grouping. `limits.candidate_limit` (also `limits.nearest` for compatibility) reports it. `candidate_pool_truncated` and `completeness.reasons: ["candidate_limit"]` signal omitted eligible fragments. Raising N alone cannot retrieve missing documents.
2. **Relevance and response.** `boundary.relevance` reports the threshold/jump/plateau assessment before the response limit. `boundary.result_cap` and `limits.result` report the result limit, omitted units and pagination offset where applicable. If N caps a no-jump pool, `boundary.reason` is `result_cap`, while the relevance assessment remains available. `nearest_n` and `none` explicitly report that relevance was not evaluated.
3. **Evidence snapshot.** At most 1000 metadata candidate rows are retained. `limits.candidate_snapshot` and `truncated` describe that bound, independently of retrieval or the number returned. `completeness.candidate_snapshot_complete` exposes it in compact diagnostics.

`completeness.result_window_complete` is false when the response cap or pagination omits units from the relevance-selected pool (null when a custom provider does not describe its cap). `completeness.reasons` also names result caps, pagination offsets and truncated evidence. `completeness.retrieval_complete` and `selection_input_complete` refer to the structurally eligible **indexed pool**, not global knowledge coverage or answer quality. Invalid distances make selection input incomplete independently of retrieval. Workspace pending/unindexed materials and source errors must also be considered where reported by that module. A small result alone does not justify broadening a query: first assess scope, technical limits, repeated fragments, relevance cuts and task coverage. Choose what to read using the task, description, type/status and applicability conditions; an old task can be useful historical evidence. Distance does not establish freshness or confidence.

Full candidate snapshots contain keys, revisions, fragment indices, distances, input positions and selected flags, without source bodies or vectors. `nearby_rejected` contains at most five rejected metadata rows. Read a source material before using it as verified evidence. Search history and replay have separate evidence limits described in [Search analysis](search-analysis.md).

## Catalog and module providers

`core.algorithms` lists descriptions or finds an exact identifier; non-exact text queries filter the description text. `semantic: true` searches derivative algorithm cards in the common RAG index under module `core`, collection `search_algorithm`. That search fixes `nearest_n` internally and restricts eligibility to the catalog, avoiding recursive algorithm selection. Document include/exclude patterns do not hide catalog cards.

Run `core.algorithms_refresh` explicitly to index changed descriptions and remove cards for algorithms no longer registered. A semantic query with missing or stale representations returns `catalog_state: "missing"` or `"stale"` and `next_action: "core.algorithms_refresh"`; the query does not refresh them. Exact catalog inspection needs no embeddings.

A provider directory contains `algorithm.py` exporting `select(rows, parameters)` and `description.json` declaring `id`, `version`, `purpose`, `parameters_schema`, `defaults`, `when_to_use`, and `how_to_adjust`. The function receives distance-ordered dictionaries in the requested units (document representatives for materials, individual parts for fragments) and returns `(kept_rows, boundary_metadata)`. The shared selection contract is versioned separately from a provider algorithm version. Providers should report `boundary.relevance` and `boundary.result_cap` when those stages apply; older provider boundary metadata is preserved without inventing those assessments. Keep selection deterministic, retain candidate identity, and handle empty input. Use the shipped [algorithm guide](../package/workspace/resources/base_context/algorithm-guide.md) and template for the complete example.

A module declares its relative `filter_algorithms` path inside its package. Discovery reads contracts; provider code is loaded only for an enabled module whose current package fingerprint matches its prepared fingerprint. A disabled or changed unprepared package contributes no algorithm. Loaded providers are cached for the current fingerprint; malformed providers are isolated from the remaining registry. Explicit module preparation/enablement and catalog refresh are separate operations.

## Repair an unavailable configured algorithm

Disabling a module, changing its package without preparing/enabling it again, or removing its provider can make a saved selection unavailable. `core.settings` preserves that selection and reports `selection_status.valid: false`, `code: "algorithm_unavailable"`, and `next_action: "core.settings_preview"`. Search with the unchanged saved selection fails; no builtin is substituted silently.

Inspect settings at the same module/context scope where the selection was configured. An explicit builtin `selection` in a search request works without changing the saved configuration. To repair it persistently, preview a replacement such as `{"selection":{"algorithm":"nearest_n","parameters":{"N":20}}}` at that configuration scope, then apply the returned revision. Enabling the original prepared module is another explicit repair path. Disabled provider code is not loaded to inspect or replace its settings.

Preview revisions include the available algorithm descriptions. If a provider is enabled, disabled, or changed between preview and apply, regenerate the preview rather than applying the old revision.

## MCP examples

Use `read` for queries and `write` for changes; `guide(operation=...)` gives an unknown signature. Select a context by name; the MCP server owns its storage location.

Inspect an exact algorithm without loading embeddings:

```json
{"operation":"core.algorithms","arguments":{"query":"archivist"}}
```

Search one Workspace context with a request-only selection override:

```json
{"operation":"workspace.search","arguments":{"query":"release conditions","selection":{"algorithm":"nearest_n","parameters":{"N":5}}},"context":"project-a"}
```

Inspect a page of fragments across explicitly chosen contexts:

```json
{"operation":"workspace.search","arguments":{"query":"release conditions","categories":["project-a","project-b"],"output":"fragments","selection":{"algorithm":"none","parameters":{"offset":20,"page_size":20}}},"context":"project-a"}
```

Search another module's collection through Core:

```json
{"operation":"core.search","arguments":{"query":"release conditions","module":"example","collection":"reference","selection":{"algorithm":"archivist","parameters":{"d_max":null,"top_k":5}}}}
```

Preview a persistent context setting, then pass the returned revision to the execute tool:

```json
{"operation":"core.settings_preview","arguments":{"module":"workspace","context":"project-a","changes":{"selection":{"algorithm":"distance_threshold","parameters":{"N":40,"d_max":0.6}}}}}
```

```json
{"operation":"core.settings_apply","arguments":{"preview_revision":"<returned-preview-revision>"}}
```

Refresh the semantic catalog with the execute tool, then search it with the query tool:

```json
{"operation":"core.algorithms_refresh","arguments":{}}
```

```json
{"operation":"core.algorithms","arguments":{"query":"retain a close cluster and cut a weak tail","semantic":true,"limit":3}}
```
