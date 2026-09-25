"""Investigate the specificity drop of the active v1 model on Phase 8A."""

import json
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
import pandas as pd

from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

PHASE_8A_FILE: Final[Path] = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "phase_8a_verified_dataset_v2.csv"
)

MODEL_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "analysis_outputs"
    / "phase_8a_specificity_root_cause_v1.json"
)


class SpecificityAnalysisError(ValueError):
    """Raised when Phase 8A analysis cannot run safely."""


def _scenario_fields(experiment_name: str) -> tuple[str, str]:
    """Extract demand profile and traffic pattern from a scenario name."""

    parts = experiment_name.split("-")

    if len(parts) < 5:
        raise SpecificityAnalysisError(
            f"Unexpected experiment name: {experiment_name}"
        )

    return parts[2], parts[3]


def _confusion_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Summarise outcomes for one subset."""

    actual = frame[TARGET_COLUMN].to_numpy(dtype=np.int8)
    predicted = frame["predicted_label"].to_numpy(dtype=np.int8)

    true_negative = int(((actual == 0) & (predicted == 0)).sum())
    false_positive = int(((actual == 0) & (predicted == 1)).sum())
    false_negative = int(((actual == 1) & (predicted == 0)).sum())
    true_positive = int(((actual == 1) & (predicted == 1)).sum())

    negative_count = true_negative + false_positive

    specificity = (
        true_negative / negative_count
        if negative_count > 0
        else None
    )

    return {
        "row_count": len(frame),
        "positive_label_count": int((actual == 1).sum()),
        "negative_label_count": negative_count,
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_positive": true_positive,
        "specificity": specificity,
    }


def _group_summaries(
    frame: pd.DataFrame,
    column: str,
) -> dict[str, dict[str, Any]]:
    """Return outcome summaries for each scenario category."""

    return {
        str(group_name): _confusion_summary(group_frame)
        for group_name, group_frame in frame.groupby(
            column,
            sort=True,
        )
    }


def _feature_shift(
    original: pd.DataFrame,
    phase_8a: pd.DataFrame,
) -> list[dict[str, float | str]]:
    """Calculate standardized mean differences between datasets."""

    results: list[dict[str, float | str]] = []

    for column in FEATURE_COLUMNS:
        original_values = original[column].to_numpy(
            dtype=np.float64
        )
        phase_8a_values = phase_8a[column].to_numpy(
            dtype=np.float64
        )

        pooled_standard_deviation = np.sqrt(
            (
                np.var(original_values, ddof=1)
                + np.var(phase_8a_values, ddof=1)
            )
            / 2
        )

        standardized_mean_difference = (
            (
                (
                    np.mean(phase_8a_values)
                    - np.mean(original_values)
                )
                / pooled_standard_deviation
            )
            if pooled_standard_deviation > 0
            else 0.0
        )

        results.append(
            {
                "feature": column,
                "original_mean": float(np.mean(original_values)),
                "phase_8a_mean": float(np.mean(phase_8a_values)),
                "standardized_mean_difference": float(
                    standardized_mean_difference
                ),
                "absolute_standardized_mean_difference": float(
                    abs(standardized_mean_difference)
                ),
            }
        )

    return sorted(
        results,
        key=lambda item: item[
            "absolute_standardized_mean_difference"
        ],
        reverse=True,
    )


def run_analysis() -> dict[str, Any]:
    """Evaluate the frozen active model without tuning it."""

    if not PHASE_8A_FILE.is_file():
        raise SpecificityAnalysisError(
            f"Phase 8A dataset not found: {PHASE_8A_FILE}"
        )

    if not MODEL_FILE.is_file():
        raise SpecificityAnalysisError(
            f"Active model artifact not found: {MODEL_FILE}"
        )

    artifact = joblib.load(MODEL_FILE)

    if not isinstance(artifact, dict):
        raise SpecificityAnalysisError(
            "Active model artifact must be a dictionary."
        )

    model = artifact.get("model")
    threshold = artifact.get("classification_threshold")

    if model is None or not isinstance(threshold, (int, float)):
        raise SpecificityAnalysisError(
            "Active model artifact is incomplete."
        )

    phase_8a = pd.read_csv(PHASE_8A_FILE)

    required_columns = {
        "experiment_name",
        TARGET_COLUMN,
        *FEATURE_COLUMNS,
    }

    missing_columns = required_columns - set(phase_8a.columns)

    if missing_columns:
        raise SpecificityAnalysisError(
            "Phase 8A dataset is missing: "
            + ", ".join(sorted(missing_columns))
        )

    feature_matrix = phase_8a.loc[
        :,
        list(FEATURE_COLUMNS),
    ].to_numpy(dtype=np.float64)

    probabilities = model.predict_proba(feature_matrix)[:, 1]

    phase_8a = phase_8a.copy()
    phase_8a["predicted_probability"] = probabilities
    phase_8a["predicted_label"] = (
        probabilities >= float(threshold)
    ).astype(np.int8)

    scenario_values = phase_8a["experiment_name"].map(
        _scenario_fields
    )

    phase_8a["demand_profile"] = scenario_values.map(
        lambda value: value[0]
    )
    phase_8a["traffic_pattern"] = scenario_values.map(
        lambda value: value[1]
    )

    actual_negative = phase_8a.loc[
        phase_8a[TARGET_COLUMN] == 0,
        "predicted_probability",
    ]

    actual_positive = phase_8a.loc[
        phase_8a[TARGET_COLUMN] == 1,
        "predicted_probability",
    ]

    original_data = load_training_data(
        split_directory=DEFAULT_SPLIT_DIRECTORY,
        manifest_file=DEFAULT_MANIFEST_FILE,
    )

    original_frame = pd.concat(
        [
            original_data.train.frame,
            original_data.validation.frame,
            original_data.test.frame,
        ],
        ignore_index=True,
    )

    report = {
        "schema_version": "1.0.0",
        "analysis_name": "phase_8a_specificity_root_cause",
        "model": {
            "model_name": artifact.get("model_name"),
            "classification_threshold": float(threshold),
            "model_file": str(MODEL_FILE),
        },
        "phase_8a_dataset": {
            "row_count": len(phase_8a),
            "experiment_count": phase_8a[
                "experiment_run_id"
            ].nunique(),
            "configured_users": sorted(
                phase_8a["configured_users"].unique().tolist()
            ),
            "simulated_work_ms": sorted(
                phase_8a["simulated_work_ms"].unique().tolist()
            ),
        },
        "overall_confusion_matrix": _confusion_summary(
            phase_8a
        ),
        "probability_distribution": {
            "actual_negative_mean": float(actual_negative.mean()),
            "actual_negative_median": float(
                actual_negative.median()
            ),
            "actual_negative_minimum": float(
                actual_negative.min()
            ),
            "actual_negative_maximum": float(
                actual_negative.max()
            ),
            "actual_negative_fraction_above_threshold": float(
                (actual_negative >= float(threshold)).mean()
            ),
            "actual_positive_mean": float(actual_positive.mean()),
            "actual_positive_median": float(
                actual_positive.median()
            ),
        },
        "outcomes_by_demand_profile": _group_summaries(
            phase_8a,
            "demand_profile",
        ),
        "outcomes_by_traffic_pattern": _group_summaries(
            phase_8a,
            "traffic_pattern",
        ),
        "largest_feature_distribution_shifts": _feature_shift(
            original_frame,
            phase_8a,
        )[:8],
        "interpretation_boundary": {
            "model_retrained": False,
            "threshold_retuned": False,
            "phase_8a_used_to_change_active_model": False,
            "purpose": (
                "Post-hoc diagnosis of the active model's "
                "specificity degradation under a new workload."
            ),
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")

    return report


def main() -> int:
    """Run and print the root-cause summary."""

    try:
        report = run_analysis()
    except SpecificityAnalysisError as error:
        print(f"Specificity analysis failed: {error}")
        return 1

    overall = report["overall_confusion_matrix"]
    probabilities = report["probability_distribution"]

    print("Phase 8A specificity root-cause analysis completed:")
    print(
        f"  Rows: {overall['row_count']} from "
        f"{report['phase_8a_dataset']['experiment_count']} experiments"
    )
    print(
        "  Specificity: "
        f"{overall['specificity']:.4f}"
    )
    print(
        "  Negative rows above the fixed threshold: "
        f"{probabilities['actual_negative_fraction_above_threshold']:.4f}"
    )
    print(
        "  Mean predicted probability: "
        f"negatives={probabilities['actual_negative_mean']:.4f}, "
        f"positives={probabilities['actual_positive_mean']:.4f}"
    )
    print("  By demand profile:")

    for profile, summary in report[
        "outcomes_by_demand_profile"
    ].items():
        specificity = summary["specificity"]
        specificity_text = (
            f"{specificity:.4f}"
            if specificity is not None
            else "not measurable (no negatives)"
        )
        print(
            f"    {profile}: negatives="
            f"{summary['negative_label_count']}, "
            f"false positives={summary['false_positive']}, "
            f"specificity={specificity_text}"
        )

    print("  Largest feature shifts:")
    for item in report[
        "largest_feature_distribution_shifts"
    ]:
        print(
            f"    {item['feature']}: SMD="
            f"{item['standardized_mean_difference']:.3f}"
        )

    print(f"  Saved: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())