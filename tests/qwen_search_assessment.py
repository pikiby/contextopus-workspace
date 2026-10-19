"""Historical Qwen/LanceDB assessment for the pre-0.8 documentation corpus.

Reproduce this fixture from tag v0.7.2, where its corpus and workflow still
exist. It is not a benchmark or calibration of 0.8 token-window retrieval.
Current runtime/index tests are in test_context_memory.py and test_codex_host.py.

This is an assessment fixture, never a PCW runtime migration: it does not
change product defaults or a workspace's active search table.  All artifacts
are durable below ``.pcw/project-update/.pcw-runtime/verification/qwen`` so a
reboot still leaves the last completed stage and its measurements for review.
"""
from __future__ import annotations

import argparse
from array import array
import dataclasses
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import sys
import time
from typing import Any


# These settings must be in force before sentence-transformers/transformers is
# imported.  LocalProvider additionally receives local_files_only=True.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
# Keep CPU-side BLAS, OpenMP and Torch from multiplying the memory footprint.
# They must be set before Torch/NumPy are imported through sentence-transformers.
for _thread_variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                         "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_thread_variable] = "4"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "persistent-context-workspace"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

CORPUS_SOURCES = (
    ("guide.md", ".pcw/project-update/knowledge/plugin-update-design.md"),
    ("pcw-skill.md", "plugins/persistent-context-workspace/skills/pcw/SKILL.md"),
    ("memory-operations.md", "plugins/persistent-context-workspace/skills/pcw/references/memory-operations.md"),
    ("prepare-search.md", "plugins/persistent-context-workspace/skills/pcw/references/prepare-search.md"),
    ("embeddings.md", "plugins/persistent-context-workspace/skills/pcw/references/embeddings.md"),
    ("setup.md", "plugins/persistent-context-workspace/skills/pcw/references/setup.md"),
    ("native-assignments.md", "plugins/persistent-context-workspace/skills/pcw/references/native-assignments.md"),
    ("action-requirements.md", "plugins/persistent-context-workspace/skills/pcw/references/action-requirements.md"),
)

# Calibration is intentionally separate from evaluation.  The selected settings
# are chosen only from this list; evaluation labels are never used for tuning.
CALIBRATION_QUERIES = (
    # The label is independent of the guide: the skill and memory-operations
    # both specify the same raw-result save protocol.
    {"id": "cal-1", "language": "ru", "text": "Как первично сохранить исходный возврат субагента с saved_at и executor_session_id?", "useful": ["guide.md", "pcw-skill.md", "memory-operations.md"]},
    {"id": "cal-2", "language": "en", "text": "How are literal semantic parts validated before indexing?", "useful": ["prepare-search.md"]},
    {"id": "cal-3", "language": "ru", "text": "Как настроить Qwen офлайн из локального кеша без загрузки весов?", "useful": ["embeddings.md", "setup.md"]},
    {"id": "cal-4", "language": "en", "text": "What does rename do to incoming filename links and embeddings?", "useful": ["guide.md", "memory-operations.md", "pcw-skill.md"]},
    {"id": "cal-5", "language": "ru", "text": "Когда нужно обновить карту моделей и reasoning перед поручением?", "useful": ["guide.md", "pcw-skill.md"]},
    {"id": "cal-6", "language": "en", "text": "How does LanceDB include fresh rows outside an ANN index?", "useful": ["guide.md"]},
)

EVALUATION_QUERIES = (
    {"id": "eval-1", "language": "ru", "text": "Имя файла является ID материала: как мигрировать старые UUID и ссылки?", "useful": ["guide.md", "pcw-skill.md", "memory-operations.md"]},
    {"id": "eval-2", "language": "en", "text": "What exact instruction is added to a Qwen query while document parts stay literal?", "useful": ["embeddings.md"]},
    {"id": "eval-3", "language": "ru", "text": "Какие поля должны быть в поручении нативному исполнителю и как он возвращает знания?", "useful": ["guide.md", "native-assignments.md"]},
    {"id": "eval-4", "language": "en", "text": "Why must a failed local embedding load not silently fall back to a remote provider?", "useful": ["embeddings.md", "setup.md"]},
    {"id": "eval-5", "language": "ru", "text": "Почему fixed token windows и пересказ не подходят для подготовки поиска?", "useful": ["guide.md", "prepare-search.md"]},
    {"id": "eval-6", "language": "en", "text": "Which MCP action requirements are checked before a transition or finish?", "useful": ["action-requirements.md"]},
    {"id": "eval-7", "language": "ru", "text": "Как развернуть Kubernetes production cluster в трёх регионах?", "useful": []},
    {"id": "eval-8", "language": "en", "text": "What are the favourite colors of the project maintainers?", "useful": []},
)


MEMORY_LIMIT_BYTES = 10 * 1024**3
THREAD_LIMIT = 4
VERIFY_ROOT = REPO / ".pcw" / "project-update" / ".pcw-runtime" / "verification" / "qwen"
BASELINE_RUN = VERIFY_ROOT / "run-20260915T024000Z"
V2_QUERIES = VERIFY_ROOT / "queries-v2-corrected-corpus-hash.json"
V2_QUERIES_SHA256 = "2e217c01353b9e181c2020737ef97a354e03a1517f7ecd84cb448155ce64136c"


def _rss_kib() -> int:
    # ru_maxrss is the process peak on Linux, measured in KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _memory_snapshot() -> dict[str, int]:
    """Linux resident/virtual memory now, plus this process's peak RSS."""
    result = {"peak_rss_kib": _rss_kib()}
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition(":")
            if name in {"VmRSS", "VmHWM", "VmSize"}:
                result[name.lower() + "_kib"] = int(value.strip().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return result


class DurableRun:
    """Append-only, fsync'd progress so a killed benchmark is inspectable."""
    def __init__(self, output: Path):
        self.output = output
        self.events = output / "events.jsonl"

    def event(self, stage: str, **values: Any) -> None:
        item = {"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stage": stage, "memory": _memory_snapshot(), **values}
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def measure(label: str, function, run: DurableRun):
    run.event("started", label=label)
    before = _rss_kib()
    start_wall, start_cpu = time.perf_counter_ns(), time.process_time_ns()
    try:
        value = function()
    except BaseException as exc:
        run.event("failed", label=label, exception_type=type(exc).__name__, message=str(exc))
        raise
    result = {
        "label": label,
        "wall_ms": round((time.perf_counter_ns() - start_wall) / 1_000_000, 3),
        "cpu_ms": round((time.process_time_ns() - start_cpu) / 1_000_000, 3),
        **_memory_snapshot(),
        "peak_rss_delta_kib": max(0, _rss_kib() - before),
    }
    run.event("completed", label=label, measurement=result)
    return value, result


def split_literal_markdown(text: str) -> list[tuple[str, str]]:
    """Fixture splitter at Markdown headings, never at token counts or fences.

    It proves only byte-for-byte coverage for this assessment corpus.  It is
    deliberately not presented as a substitute for the separate model-led
    semantic splitting required by the PCW design.
    """
    starts = [0]
    fence = False
    heading = re.compile(r"^#{1,3}\s+.+")
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            fence = not fence
        if offset and not fence and heading.match(line):
            starts.append(offset)
        offset += len(line)
    starts.append(len(text))
    chunks = []
    for start, end in zip(starts, starts[1:]):
        chunk = text[start:end]
        if not chunk:
            continue
        first = next((line.strip() for line in chunk.splitlines() if line.strip()), "preamble")
        chunks.append((first[:160], chunk))
    if "".join(chunk for _, chunk in chunks) != text:
        raise AssertionError("literal semantic splitter lost source text")
    return chunks


def build_corpus() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parts, coverage = [], []
    for material_id, relative in CORPUS_SOURCES:
        source = REPO / relative
        text = source.read_text(encoding="utf-8")
        sections = split_literal_markdown(text)
        fenced_blocks = len(re.findall(r"(?m)^\s*(?:```|~~~)", text))
        coverage.append({
            "material_id": material_id, "source": relative, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "characters": len(text), "parts": len(sections), "literal_coverage": "complete",
            "markdown_fence_markers": fenced_blocks,
            "fence_markers_even": fenced_blocks % 2 == 0,
        })
        for index, (heading, content) in enumerate(sections):
            parts.append({
                "material_id": material_id, "part_index": index, "source": relative,
                "heading": heading, "text": content,
            })
    return parts, coverage


def score(queries: tuple[dict[str, Any], ...], returned: dict[str, list[str]]) -> dict[str, Any]:
    query_scores = []
    for query in queries:
        expected, actual = set(query["useful"]), set(returned[query["id"]])
        if expected:
            relevant = expected & actual
            precision = len(relevant) / len(actual) if actual else 0.0
            recall = len(relevant) / len(expected)
        else:
            relevant = set()
            precision = 1.0 if not actual else 0.0
            recall = 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        query_scores.append({
            "id": query["id"], "expected": sorted(expected), "returned": sorted(actual),
            "precision": precision, "recall": recall, "f1": f1, "false_positive_count": len(actual - expected),
        })
    return {
        "macro_precision": sum(item["precision"] for item in query_scores) / len(query_scores),
        "macro_recall": sum(item["recall"] for item in query_scores) / len(query_scores),
        "macro_f1": sum(item["f1"] for item in query_scores) / len(query_scores),
        "false_positive_count": sum(item["false_positive_count"] for item in query_scores),
        "mean_results": sum(len(item["returned"]) for item in query_scores) / len(query_scores),
        "queries": query_scores,
    }


def selected_ids(rows: list[dict[str, Any]], config) -> list[str]:
    from pcw_runtime.runtime import select_candidates
    return [item["id"] for item in select_candidates(rows, config)]


def candidate_grid(raw: dict[str, list[dict[str, Any]]], queries: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    from pcw_runtime.runtime import SearchConfig
    results = []
    for N, d_max, min_jump, multiplier in itertools.product(
            (10, 20, 30), (0.45, 0.60, 0.75, 0.90, 1.10), (0.01, 0.02, 0.04), (2.0, 3.0, 4.0)):
        config = SearchConfig(N=N, d_max=d_max, min_jump=min_jump, multiplier=multiplier, metric="cosine")
        returned = {query["id"]: selected_ids(raw[query["id"]], config) for query in queries}
        quality = score(queries, returned)
        results.append({"settings": dataclasses.asdict(config), "quality": quality})
    return sorted(results, key=lambda item: (
        -item["quality"]["macro_f1"], -item["quality"]["macro_recall"],
        item["quality"]["false_positive_count"], item["quality"]["mean_results"],
    ))


def _load_and_verify_v2_fixture(baseline: Path, fixture_path: Path,
                                expected_digest: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate frozen labels, literal corpus and stored baseline vectors.

    This performs only local file/LanceDB reads.  It intentionally neither
    reconstructs corpus embeddings nor changes the baseline table.
    """
    raw_fixture = fixture_path.read_bytes()
    digest = hashlib.sha256(raw_fixture).hexdigest()
    if digest != expected_digest:
        raise AssertionError(f"Frozen v2 query SHA256 differs: {digest}")
    fixture = json.loads(raw_fixture)
    if fixture.get("schema_version") != "qwen-retrieval-labels-v2":
        raise AssertionError("Unexpected frozen v2 query schema")
    calibration, holdout = fixture.get("calibration"), fixture.get("holdout")
    if not isinstance(calibration, list) or not isinstance(holdout, list) or len(calibration) != 24 or len(holdout) != 12:
        raise AssertionError("Frozen v2 fixture must contain 24 calibration and 12 holdout queries")
    identifiers = [item.get("id") for item in calibration + holdout]
    if len(identifiers) != len(set(identifiers)) or any(not isinstance(value, str) for value in identifiers):
        raise AssertionError("Frozen v2 query IDs must be unique strings")

    corpus = json.loads((baseline / "corpus.json").read_text(encoding="utf-8"))
    expected_materials = fixture["frozen_corpus"]["material_sha256"]
    actual_materials = {item["material_id"]: item["sha256"] for item in corpus["coverage"]}
    if actual_materials != expected_materials:
        raise AssertionError("Frozen material SHA256 values do not match the baseline corpus")
    expected_parts = corpus["parts"]
    if len(expected_parts) != 84:
        raise AssertionError("Baseline corpus must contain the frozen 84 parts")

    import lancedb
    table = lancedb.connect(str(baseline / "lancedb")).open_table("parts")
    table_rows = table.to_arrow().to_pylist()
    expected_by_key = {
        (part["material_id"], part["part_index"], part["text"]): part for part in expected_parts
    }
    table_by_key = {
        (row["material_id"], row["part_index"], row["text"]): row for row in table_rows
    }
    if set(table_by_key) != set(expected_by_key):
        raise AssertionError("Baseline LanceDB rows do not exactly match the frozen literal corpus")
    for key, row in table_by_key.items():
        vector = row.get("vector")
        if not isinstance(vector, list) or len(vector) != 1024 or not all(math.isfinite(value) for value in vector):
            raise AssertionError(f"Baseline LanceDB vector is invalid for {key[0]} part {key[1]}")
    vector_payload = [
        {"material_id": row["material_id"], "part_index": row["part_index"], "text": row["text"], "vector": row["vector"]}
        for _, row in sorted(table_by_key.items())
    ]
    verification = {
        "queries_path": str(fixture_path), "queries_sha256": digest,
        "baseline_run": str(baseline), "frozen_material_sha256": actual_materials,
        "parts": len(expected_parts), "table_rows": len(table_rows), "dimensions": 1024,
        "vectors_all_finite": True,
        "table_values_sha256": hashlib.sha256(json.dumps(
            vector_payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
    }
    return fixture, verification


def _v2_query_metrics(query: dict[str, Any], returned: list[str]) -> dict[str, Any]:
    returned_set = set(returned)
    base = {"id": query["id"], "language": query["language"], "kind": query["kind"],
            "scope": "multi" if query.get("coverage") == "multi_fact" else "single",
            "returned": returned, "unique_returned_materials": len(returned)}
    if query["kind"] == "negative":
        base.update({"negative_rejected": not returned, "false_positive_material_count": len(returned)})
        return base
    answer_sets = [set(answer) for answer in query["answer_sets"]]
    exhaustive = set(query["exhaustive_relevant"])
    base.update({
        "complete_answer_set_hit": any(answer <= returned_set for answer in answer_sets),
        "hit_at_1": any(answer <= set(returned[:1]) for answer in answer_sets),
        "hit_at_2": any(answer <= set(returned[:2]) for answer in answer_sets),
        "full_material_recall": len(returned_set & exhaustive) / len(exhaustive),
        "exhaustive_relevant": sorted(exhaustive),
    })
    return base


def _v2_metric_summary(queries: list[dict[str, Any]], returned: dict[str, list[str]]) -> dict[str, Any]:
    per_query = [_v2_query_metrics(query, returned[query["id"]]) for query in queries]

    def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        positive = [item for item in items if item["kind"] == "positive"]
        negative = [item for item in items if item["kind"] == "negative"]
        mean = lambda values: sum(values) / len(values) if values else None
        positive_hit = mean([float(item["complete_answer_set_hit"]) for item in positive])
        negative_rejection = mean([float(item["negative_rejected"]) for item in negative])
        return {
            "queries": len(items), "positive_queries": len(positive), "negative_queries": len(negative),
            "positive_complete_answer_set_hit_rate": positive_hit,
            "negative_rejection_rate": negative_rejection,
            "balanced_score": (0.5 * positive_hit + 0.5 * negative_rejection
                               if positive_hit is not None and negative_rejection is not None else None),
            "hit_at_1": mean([float(item["hit_at_1"]) for item in positive]),
            "hit_at_2": mean([float(item["hit_at_2"]) for item in positive]),
            "full_material_recall": mean([item["full_material_recall"] for item in positive]),
            "negative_false_positive_query_count": sum(not item["negative_rejected"] for item in negative),
            "negative_false_positive_material_count": sum(item["false_positive_material_count"] for item in negative),
            "mean_unique_returned_materials": mean([item["unique_returned_materials"] for item in items]),
        }

    return {
        **aggregate(per_query), "per_query": per_query,
        "by_language": {language: aggregate([item for item in per_query if item["language"] == language])
                        for language in ("ru", "en")},
        "by_scope": {scope: aggregate([item for item in per_query if item["scope"] == scope])
                     for scope in ("single", "multi")},
    }


def _candidate_grid_v2(raw: dict[str, list[dict[str, Any]],], calibration: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from pcw_runtime.runtime import SearchConfig
    results = []
    for N, d_max, min_jump, multiplier in itertools.product(
            (10, 20, 30), (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80),
            (0.01, 0.02, 0.04), (2.0, 3.0, 4.0)):
        config = SearchConfig(N=N, d_max=d_max, min_jump=min_jump, multiplier=multiplier, metric="cosine")
        returned = {query["id"]: selected_ids(raw[query["id"]], config) for query in calibration}
        metrics = _v2_metric_summary(calibration, returned)
        results.append({"settings": dataclasses.asdict(config), "metrics": metrics})
    return sorted(results, key=lambda item: (
        -item["metrics"]["balanced_score"], item["metrics"]["mean_unique_returned_materials"],
        item["settings"]["N"], f"{item['settings']['d_max']:.2f}",
        f"{item['settings']['min_jump']:.2f}", f"{item['settings']['multiplier']:.1f}"))


def _snapshot_exists() -> Path:
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = Path(os.environ.get("HF_HUB_CACHE", hf_home / "hub"))
    snapshot = hub / "models--Qwen--Qwen3-Embedding-0.6B" / "snapshots" / "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    if not snapshot.is_dir():
        raise RuntimeError(f"Required offline Qwen snapshot is absent: {snapshot}")
    return snapshot


def _write(path: Path, value: Any) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _set_memory_limit() -> dict[str, Any]:
    """Apply an inherited hard address-space ceiling before ML imports."""
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    requested = MEMORY_LIMIT_BYTES
    effective_hard = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
    effective_soft = requested if soft == resource.RLIM_INFINITY else min(requested, soft, effective_hard)
    resource.setrlimit(resource.RLIMIT_AS, (effective_soft, effective_hard))
    now_soft, now_hard = resource.getrlimit(resource.RLIMIT_AS)
    return {"kind": "RLIMIT_AS", "requested_bytes": requested,
            "soft_bytes": now_soft, "hard_bytes": now_hard,
            "threads": THREAD_LIMIT}


def embed_individually(provider, texts: list[str], purpose: str, run: DurableRun,
                       label: str) -> list[list[float]]:
    """Do not allow SentenceTransformer's default batch of long documents."""
    vectors = []
    for index, value in enumerate(texts, start=1):
        vector, _ = measure(f"{label}[{index}/{len(texts)}]",
                            lambda value=value: provider.embed([value], purpose=purpose), run)
        vectors.append(vector[0])
    return vectors


def _baseline_vectors(baseline: Path, sample: list[dict[str, Any]]) -> list[list[float]]:
    """Read saved float32 vectors only for an identical literal-part sample."""
    import lancedb

    saved = json.loads((baseline / "corpus.json").read_text(encoding="utf-8"))["parts"]
    saved_by_key = {
        (item["material_id"], item["part_index"], hashlib.sha256(item["text"].encode()).hexdigest()): item
        for item in saved
    }
    keys = [(item["material_id"], item["part_index"], hashlib.sha256(item["text"].encode()).hexdigest())
            for item in sample]
    if any(key not in saved_by_key for key in keys):
        raise AssertionError("Current probe sample differs from the saved baseline corpus")
    table = lancedb.connect(str(baseline / "lancedb")).open_table("parts")
    rows = table.to_arrow().to_pylist()
    vectors = {
        (row["material_id"], row["part_index"], hashlib.sha256(row["text"].encode()).hexdigest()): row["vector"]
        for row in rows
    }
    if any(key not in vectors for key in keys):
        raise AssertionError("Saved baseline LanceDB lacks one or more probe vectors")
    return [vectors[key] for key in keys]


def _compare_vectors(actual: list[list[float]], baseline: list[list[float]]) -> dict[str, Any]:
    if len(actual) != len(baseline):
        raise AssertionError("Product probe returned a different vector count")
    max_abs_difference, minimum_cosine = 0.0, 1.0
    for index, (left, right) in enumerate(zip(actual, baseline)):
        if len(left) != 1024 or len(right) != 1024:
            raise AssertionError(f"Vector {index} does not have 1024 dimensions")
        if not all(math.isfinite(value) for value in (*left, *right)):
            raise AssertionError(f"Vector {index} contains a non-finite value")
        max_abs_difference = max(max_abs_difference, max(abs(a - b) for a, b in zip(left, right)))
        denominator = math.sqrt(math.fsum(a * a for a in left) * math.fsum(b * b for b in right))
        if denominator == 0:
            raise AssertionError(f"Vector {index} has zero norm")
        minimum_cosine = min(minimum_cosine, math.fsum(a * b for a, b in zip(left, right)) / denominator)
    comparison = {"count": len(actual), "dimensions": 1024, "all_finite": True,
                  "max_abs_difference": max_abs_difference, "minimum_cosine": minimum_cosine,
                  "max_abs_tolerance": 2e-6, "minimum_cosine_tolerance": 0.999999}
    comparison["within_tolerance"] = (max_abs_difference <= comparison["max_abs_tolerance"]
                                        and minimum_cosine >= comparison["minimum_cosine_tolerance"])
    if not comparison["within_tolerance"]:
        raise AssertionError("Bounded batching changed vectors beyond float32 storage tolerance")
    return comparison


def run_default_batch_probe(parts: list[dict[str, Any]], config: dict[str, Any], output: Path,
                            run: DurableRun, limits: dict[str, Any], baseline: Path) -> int:
    """One bounded probe of the current, unmodified production call path.

    The selection deliberately consists of 32 actual, longest fixture parts:
    it sends a conservative real-text stress sample through current production
    batching.  No truncation, monkeypatch or alternate encode implementation
    is involved.
    """
    from pcw_runtime.embeddings import LocalProvider

    sample = sorted(parts, key=lambda item: len(item["text"]), reverse=True)[:32]
    provider = LocalProvider(config)
    probe = {
        "status": "started",
        "mode": "product_provider_default_batch_probe",
        "part_count": len(sample),
        "selection": "32 longest real literal assessment parts",
        "part_characters": [len(item["text"]) for item in sample],
        "threads": THREAD_LIMIT,
        "limits": limits,
        "offline": True,
        "implementation": "unmodified LocalProvider.embed(texts, purpose='document')",
        "baseline": str(baseline),
    }
    _write(output / "probe.json", probe)
    try:
        vectors, measurement = measure(
            "product_provider_default_batch_32",
            lambda: provider.embed([item["text"] for item in sample], purpose="document"), run)
        baseline_vectors = _baseline_vectors(baseline, sample)
        comparison = _compare_vectors(vectors, baseline_vectors)
        probe.update({"status": "ok", "measurement": measurement, "vectors": len(vectors),
                      "dimensions": len(vectors[0]) if vectors else 0, "comparison": comparison})
        _write(output / "probe.json", probe)
        run.event("probe_completed", status="ok", vectors=len(vectors), measurement=measurement)
        print(json.dumps({"status": "ok", "probe": str(output / "probe.json")}, ensure_ascii=False), flush=True)
        return 0
    except BaseException as exc:
        probe.update({"status": "failed", "exception_type": type(exc).__name__, "message": str(exc),
                      "memory": _memory_snapshot()})
        _write(output / "probe.json", probe)
        run.event("probe_completed", status="failed", exception_type=type(exc).__name__)
        raise
    finally:
        provider.close()


def run_v2_rerank(output: Path, run: DurableRun, limits: dict[str, Any], baseline: Path,
                  fixture_path: Path, fixture_sha256: str) -> int:
    """Embed only frozen v2 queries and evaluate the baseline LanceDB table."""
    fixture, baseline_verification = _load_and_verify_v2_fixture(baseline, fixture_path, fixture_sha256)
    _write(output / "baseline_verification.json", baseline_verification)
    run.event("baseline_verified", **baseline_verification)

    from pcw_runtime.embeddings import LocalProvider, QWEN_MODEL, QWEN_REVISION, default_config
    snapshot = _snapshot_exists()
    config = default_config()
    config["provider"]["device"] = "cpu"
    config["provider"]["local_files_only"] = True
    config["profile"]["precision"] = "float32"
    if config["profile"]["model"] != QWEN_MODEL or config["profile"]["revision"] != QWEN_REVISION:
        raise AssertionError("v2 rerank profile differs from the required cached Qwen revision")

    calibration, holdout = fixture["calibration"], fixture["holdout"]
    all_queries = calibration + holdout
    provider = LocalProvider(config)
    timings: dict[str, Any] = {}
    try:
        query_vectors, timings["query_embedding_single_item"] = measure(
            "query_embedding_single_item", lambda: embed_individually(
                provider, [query["query"] for query in all_queries], "query", run, "v2_query_embedding"), run)

        import lancedb
        table = lancedb.connect(str(baseline / "lancedb")).open_table("parts")
        raw: dict[str, list[dict[str, Any]]] = {}
        search_measurements = []
        for query, vector in zip(all_queries, query_vectors):
            def search_one(vector=vector):
                return table.search(vector).distance_type("cosine").select(
                    ["material_id", "source", "heading"]).limit(30).to_list()
            lance_rows, measured = measure("baseline_lancedb_search", search_one, run)
            search_measurements.append({"query_id": query["id"], **measured})
            raw[query["id"]] = [
                {"id": row["material_id"], "path": row["source"], "heading": row["heading"],
                 "distance": row["_distance"]}
                for row in lance_rows
            ]
        timings["baseline_lancedb_search"] = {
            "runs": len(search_measurements),
            "mean_wall_ms": round(sum(item["wall_ms"] for item in search_measurements) / len(search_measurements), 3),
            "mean_cpu_ms": round(sum(item["cpu_ms"] for item in search_measurements) / len(search_measurements), 3),
            "per_query": search_measurements,
        }
        _write(output / "raw_top_30.json", {"query_ids": [query["id"] for query in all_queries], "raw": raw})

        grid = _candidate_grid_v2(raw, calibration)
        winner = grid[0]
        # This is deliberately persisted before any holdout scoring occurs.
        winner_artifact = {
            "status": "selected_from_calibration_only", "fixture_sha256": fixture_sha256,
            "selection_contract": fixture["pre_registered_selection"],
            "calibration_query_ids": [query["id"] for query in calibration],
            "settings_compared": len(grid), "winner": winner, "top_five": grid[:5],
        }
        _write(output / "calibration_winner.json", winner_artifact)
        run.event("calibration_winner_persisted", settings=winner["settings"],
                  balanced_score=winner["metrics"]["balanced_score"])

        from pcw_runtime.runtime import SearchConfig
        selected_config = SearchConfig(**winner["settings"])
        holdout_returned = {query["id"]: selected_ids(raw[query["id"]], selected_config) for query in holdout}
        holdout_metrics = _v2_metric_summary(holdout, holdout_returned)
        _write(output / "calibration_grid.json", {"settings_compared": len(grid), "grid": grid})
        report = {
            "status": "ok", "mode": "frozen-v2-query-rerank",
            "scope": {
                "fixture": str(fixture_path), "fixture_sha256": fixture_sha256,
                "baseline_run": str(baseline), "baseline_table_reused": True,
                "corpus_reembedded": False, "query_embeddings": len(all_queries),
                "offline": True, "snapshot": str(snapshot), "metric": "cosine",
            },
            "profile": config, "resources": {**_memory_snapshot(), "limits": limits},
            "baseline_verification": baseline_verification, "timings": timings,
            "calibration": winner_artifact,
            "holdout": {"evaluated_after_persisted_calibration_winner": True,
                        "query_ids": [query["id"] for query in holdout], "metrics": holdout_metrics},
            "limitations": [
                "This is one frozen PCW project-fixture assessment, not a universal retrieval claim.",
                "The selected settings are not applied to product defaults by this assessment.",
                "The heading splitter and old assessment corpus are frozen fixtures, not native semantic-splitting proof.",
            ],
        }
        _write(output / "report.json", report)
        run.event("run_completed", status="ok", report=str(output / "report.json"))
        print(json.dumps({"status": "ok", "report": str(output / "report.json"),
                          "winner": winner["settings"]}, ensure_ascii=False), flush=True)
        return 0
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="New durable directory below .pcw/project-update/.pcw-runtime/verification/qwen")
    parser.add_argument("--probe-default-batch", action="store_true",
                        help="one bounded product-provider batching probe; skips retrieval assessment")
    parser.add_argument("--baseline", type=Path,
                        default=VERIFY_ROOT / "run-20260915T024000Z",
                        help="saved completed assessment used for vector comparison with --probe-default-batch")
    parser.add_argument("--rerank-v2", action="store_true",
                        help="reuse frozen baseline parts/vectors; embed only frozen v2 query fixtures")
    parser.add_argument("--v2-fixture", type=Path, default=V2_QUERIES,
                        help="immutable v2 query fixture used only with --rerank-v2")
    parser.add_argument("--v2-fixture-sha256", default=V2_QUERIES_SHA256,
                        help="required SHA256 for --v2-fixture")
    args = parser.parse_args()
    if args.output is not None:
        output = args.output.resolve()
        if not output.is_relative_to(VERIFY_ROOT):
            raise SystemExit(f"--output must stay below {VERIFY_ROOT}")
    else:
        output = VERIFY_ROOT / time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime())
    if output.exists():
        raise SystemExit(f"Refusing to overwrite assessment artifacts: {output}")
    output.mkdir(parents=True)
    run = DurableRun(output)
    limits = _set_memory_limit()
    run.event("run_started", artifact_directory=str(output), offline=True,
              network="disabled by HF offline variables and local_files_only", limits=limits)
    print(f"artifact_directory={output}", flush=True)

    if args.probe_default_batch and args.rerank_v2:
        raise SystemExit("Choose either --probe-default-batch or --rerank-v2")
    if args.rerank_v2:
        return run_v2_rerank(output, run, limits, args.baseline.resolve(),
                             args.v2_fixture.resolve(), args.v2_fixture_sha256)

    snapshot = _snapshot_exists()
    parts, coverage = build_corpus()
    _write(output / "corpus.json", {"coverage": coverage, "parts": parts})
    _write(output / "queries.json", {"calibration": CALIBRATION_QUERIES, "evaluation": EVALUATION_QUERIES})
    run.event("corpus_written", literal_parts=len(parts), sources=len(coverage),
              semantic_split_claim="not_proven: heading splitter is an assessment fixture only")

    from pcw_runtime.embeddings import LocalProvider, QWEN_MODEL, QWEN_REVISION, default_config

    config = default_config()
    config["provider"]["device"] = "cpu"
    config["provider"]["local_files_only"] = True
    config["profile"]["precision"] = "float32"
    if config["profile"]["model"] != QWEN_MODEL or config["profile"]["revision"] != QWEN_REVISION:
        raise AssertionError("assessment profile differs from the required cached Qwen revision")
    if args.probe_default_batch:
        return run_default_batch_probe(parts, config, output, run, limits, args.baseline.resolve())

    import lancedb
    import pyarrow as pa
    provider = LocalProvider(config)
    timings: dict[str, Any] = {}
    try:
        _, timings["cold_model_load"] = measure("cold_model_load", provider._load, run)
        _, timings["first_document_inference"] = measure(
            "first_document_inference", lambda: provider.embed([parts[0]["text"]], purpose="document"), run)
        _, timings["warm_document_single"] = measure(
            "warm_document_single", lambda: provider.embed([parts[min(1, len(parts) - 1)]["text"]], purpose="document"), run)
        _, timings["warm_query"] = measure(
            "warm_query", lambda: provider.embed([CALIBRATION_QUERIES[0]["text"]], purpose="query"), run)
        document_vectors, timings["document_indexing"] = measure(
            "document_indexing", lambda: embed_individually(
                provider, [part["text"] for part in parts], "document", run, "document_embedding"), run)
        all_queries = CALIBRATION_QUERIES + EVALUATION_QUERIES
        query_vectors, timings["query_embedding"] = measure(
            "query_embedding", lambda: embed_individually(
                provider, [query["text"] for query in all_queries], "query", run, "query_embedding"), run)

        schema = pa.schema([
            ("material_id", pa.string()), ("part_index", pa.int32()), ("source", pa.string()),
            ("heading", pa.string()), ("text", pa.string()), ("vector", pa.list_(pa.float32(), 1024)),
        ])
        rows = []
        for part, vector in zip(parts, document_vectors):
            rows.append({**part, "vector": array("f", vector).tolist()})
        db = lancedb.connect(str(output / "lancedb"))
        table = db.create_table("parts", schema=schema)
        _, timings["lancedb_add"] = measure("lancedb_add", lambda: table.add(rows), run)

        query_vectors_by_id = {query["id"]: vector for query, vector in zip(all_queries, query_vectors)}
        raw: dict[str, list[dict[str, Any]]] = {}
        search_measurements = []
        for query in all_queries:
            def search_one(query=query):
                return table.search(query_vectors_by_id[query["id"]]).distance_type("cosine").select(
                    ["material_id", "source", "heading"]).limit(30).to_list()
            lance_rows, measured = measure("lancedb_search", search_one, run)
            search_measurements.append({"query_id": query["id"], **measured})
            raw[query["id"]] = [
                {"id": row["material_id"], "path": row["source"], "distance": row["_distance"], "heading": row["heading"]}
                for row in lance_rows
            ]
        timings["warm_lancedb_search"] = {
            "runs": len(search_measurements),
            "mean_wall_ms": round(sum(item["wall_ms"] for item in search_measurements) / len(search_measurements), 3),
            "mean_cpu_ms": round(sum(item["cpu_ms"] for item in search_measurements) / len(search_measurements), 3),
            "peak_rss_kib": max(item["peak_rss_kib"] for item in search_measurements),
            "per_query": search_measurements,
        }
        grid = candidate_grid(raw, CALIBRATION_QUERIES)
        chosen = grid[0]
        from pcw_runtime.runtime import SearchConfig
        selected_config = SearchConfig(**chosen["settings"])
        evaluation_returned = {query["id"]: selected_ids(raw[query["id"]], selected_config) for query in EVALUATION_QUERIES}
        evaluation = score(EVALUATION_QUERIES, evaluation_returned)
        _write(output / "retrieval.json", {"raw_top_30": raw, "calibration_grid": grid, "evaluation": evaluation})
        report = {
            "status": "ok",
            "scope": {
                "repository": str(REPO), "sources": [item[1] for item in CORPUS_SOURCES],
                "parts": len(parts), "source_count": len(coverage), "metric": "cosine",
                "offline": True, "snapshot": str(snapshot), "vectors": "LanceDB table under this artifact directory",
            },
            "profile": config,
            "resources": {
                "cpu_count": os.cpu_count(), "platform": platform.platform(), "python": sys.version,
                **_memory_snapshot(), "rss_unit": "KiB; process high-water mark",
                "limits": limits,
            },
            "timings": timings,
            "calibration": {"settings_compared": len(grid), "chosen": chosen, "top_five": grid[:5]},
            "evaluation": {"settings_from_calibration_only": chosen["settings"], "quality": evaluation},
            "limitations": [
                "This is one local CPU run over the listed PCW documentation fixture, not a universal retrieval claim.",
                "Labels are a small hand-annotated material-level fixture; they do not establish end-user answer correctness.",
                "Peak RSS is process high-water mark, so individual stage deltas can be zero after an earlier peak.",
                "The literal heading splitter proves source-byte preservation for this fixture only. It is not a semantic-model validation of the required production splitting workflow.",
                "No product search defaults or guide numbers were changed by this assessment.",
            ],
        }
        _write(output / "report.json", report)
        run.event("run_completed", status="ok", report=str(output / "report.json"))
        print(json.dumps({"status": "ok", "report": str(output / "report.json"), "parts": len(parts)}, ensure_ascii=False), flush=True)
        return 0
    finally:
        provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
