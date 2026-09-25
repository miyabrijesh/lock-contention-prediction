"""Benchmark local model-only inference latency for the frozen v1 model."""

import json
import platform
import sys
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Final

import joblib
import numpy as np

from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

MODEL_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "analysis_outputs"
    / "inference_latency_benchmark_v1.json"
)

WARMUP_ITERATIONS: Final[int] = 500
MEASURED_ITERATIONS: Final[int] = 5_000


class LatencyBenchmarkError(ValueError):
    """Raised when latency benchmarking cannot run safely."""


def run_benchmark() -> dict[str, Any]:
    """Measure one-row local model scoring latency."""

    if not MODEL_FILE.is_file():
        raise LatencyBenchmarkError(
            f"Frozen model artifact not found: {MODEL_FILE}"
        )

    artifact = joblib.load(MODEL_FILE)

    if not isinstance(artifact, dict) or artifact.get("model") is None:
        raise LatencyBenchmarkError(
            "Frozen model artifact is invalid."
        )

    data = load_training_data(
        split_directory=DEFAULT_SPLIT_DIRECTORY,
        manifest_file=DEFAULT_MANIFEST_FILE,
    )

    feature_rows = data.validation.features.to_numpy(
        dtype=np.float64
    )

    if len(feature_rows) == 0:
        raise LatencyBenchmarkError(
            "Validation feature rows are unavailable."
        )

    model = artifact["model"]

    for index in range(WARMUP_ITERATIONS):
        row = feature_rows[index % len(feature_rows)].reshape(1, -1)
        model.predict_proba(row)

    durations_ns: list[int] = []

    for index in range(MEASURED_ITERATIONS):
        row = feature_rows[index % len(feature_rows)].reshape(1, -1)

        started_at = perf_counter_ns()
        model.predict_proba(row)
        durations_ns.append(perf_counter_ns() - started_at)

    durations_ms = np.asarray(durations_ns, dtype=np.float64) / 1_000_000

    report = {
        "schema_version": "1.0.0",
        "analysis_name": "local_model_inference_latency",
        "model": {
            "model_name": artifact.get("model_name"),
            "model_file": str(MODEL_FILE),
            "feature_count": len(data.feature_columns),
        },
        "benchmark": {
            "input_type": "one telemetry row",
            "warmup_iterations": WARMUP_ITERATIONS,
            "measured_iterations": MEASURED_ITERATIONS,
            "input_source": "validation feature rows only",
            "includes_database_query": False,
            "includes_http_network_time": False,
            "includes_feature_collection_time": False,
            "latency_unit": "milliseconds",
        },
        "latency_ms": {
            "mean": float(np.mean(durations_ms)),
            "median_p50": float(np.quantile(durations_ms, 0.50)),
            "p95": float(np.quantile(durations_ms, 0.95)),
            "p99": float(np.quantile(durations_ms, 0.99)),
            "minimum": float(np.min(durations_ms)),
            "maximum": float(np.max(durations_ms)),
        },
        "environment": {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
        },
        "interpretation_boundary": {
            "result_scope": (
                "Local model scoring only; not end-to-end "
                "production request latency."
            ),
            "model_or_threshold_changed": False,
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")

    return report


def main() -> int:
    """Run the benchmark and print its summary."""

    try:
        report = run_benchmark()
    except LatencyBenchmarkError as error:
        print(f"Latency benchmark failed: {error}")
        return 1

    latency = report["latency_ms"]

    print("Local model inference latency benchmark completed:")
    print(
        f"  Median (p50): {latency['median_p50']:.4f} ms"
    )
    print(f"  p95: {latency['p95']:.4f} ms")
    print(f"  p99: {latency['p99']:.4f} ms")
    print(f"  Mean: {latency['mean']:.4f} ms")
    print(
        "  Scope: model scoring only; excludes database, "
        "feature collection, and HTTP/network time."
    )
    print(f"  Saved: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())