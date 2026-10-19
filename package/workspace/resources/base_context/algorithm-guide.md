# Creating a retrieval selection algorithm

A selection algorithm receives distance-ordered candidates after structural filters. In material mode, the shared selection contract 2 groups fragments by document ID before relevance selection, result limits and pagination. The nearest fragment represents each document. Explicit fragment mode retains individual parts and fragment units. All built-in algorithms, including Archivist, follow these units; their 2.0.0 versions preserve their configured defaults. Selection does not change embeddings, source data or active instructions. Configure it through the shared Core settings and registry.

For an ordinary question, inherit the selected context's algorithm and limits first. If the observed result is unsuitable, inspect `core.algorithms` and search diagnostics before changing parameters. Installed choices include `distance_jump`, `distance_threshold`, `nearest_n`, `none` and `archivist`. `nearest_n` is a count window, not a relevance guarantee; `none` provides distance-ordered pagination rather than disabling retrieval.

Example: if a result cap omitted otherwise useful materials, adjust that limit for a later bounded search. If the retrieval candidate pool was capped, changing selection alone cannot recover missing records. Preview persistent selection through `core.settings_preview` and apply its exact `preview_revision` with `core.settings_apply`; a search's `selection` override affects only that request. `core.search_event/search_replay` support a separate historical comparison against retained candidates. Semantic algorithm lookup requires a prepared catalog; read-only lookup does not install embeddings or build it.

Inspect the installed algorithm manifest and interface before implementing. Declare a stable identifier, version, purpose, parameter schema, defaults and usage guidance. Keep selection deterministic for identical candidates and parameters. Empty input, equal distances, missing distances and short candidate lists need defined behavior.

Example: distance-jump evaluates the full pool within `d_max` for a meaningful jump before applying `N`. Its diagnostics distinguish the relevance boundary from the result cap, missing jumps and insufficient data. A technical candidate cap is a separate completeness limit; no selector can recover candidates that retrieval omitted. Preserve ranked order and distinguish distance from confidence. Compare selection with fixed query fixtures and source relevance evidence.

A configuration preview should display the algorithm and effective parameters. Applying it requires the exact preview revision. Request-level overrides remain scoped to that request.

An enabled module's `filter_algorithms` directory supplies algorithm subfolders, each with `description.json` and `algorithm.py`. A minimal descriptor is:

```json
{"id":"sample_first","version":"1.0.0","purpose":"Keep the first ranked candidates","parameters_schema":{"type":"object","properties":{"N":{"type":"integer","minimum":1,"maximum":1000}},"additionalProperties":false},"defaults":{"N":20},"when_to_use":"Fixed nearest window","how_to_adjust":"Change N"}
```

```python
# algorithm.py

def select(rows, parameters):
    kept = rows[:parameters["N"]]
    return kept, {"reason": "nearest_window"}
```

The shared registry validates `parameters_schema` and `defaults`, loads `select(rows, parameters)`, and supplies candidates in the requested units. The selector returns `(kept_rows, boundary_metadata)`. Report relevance and result-cap decisions distinctly; the shared layer reports retrieval completeness. Choose the algorithm through `selection: {"algorithm":"sample_first","parameters":{"N":20}}` in shared settings or a scoped search request. This example describes a custom algorithm; changed built-in behavior uses its own version so historical replay cannot silently reinterpret an older event. An enabled module contributes the provider through its manifest's `filter_algorithms` path.

Implementation references: `src/contextopus/core/filtering/`, `src/contextopus/core/settings.py`, `src/contextopus/core/search_history.py`. Detailed procedures: `search`, `reference/prepare-search`.
