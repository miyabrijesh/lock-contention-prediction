"""Fair experiment-level comparison of Logistic Regression and Random Forest.

Uses only the 51 development experiments (train + validation). The frozen
9-experiment final test set is not loaded into this comparison.
"""

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold

from model_training.train_baselines import (
    BASELINE_RANDOM_SEED,
    build_baseline_models,
)
from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    EXPERIMENT_ID_COLUMN,
    TARGET_COLUMN,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "analysis_outputs"
    / "grouped_model_comparison_v1.json"
)

N_SPLITS: Final[int] = 5
N_REPEATS: Final[int] = 5
CLASSIFICATION_THRESHOLD: Final[float] = 0.5


class GroupedComparisonError(ValueError):
    """Raised when grouped model comparison cannot run safely."""


def _metrics(
    labels: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, float]:
    """Calculate comparable fold-level metrics."""

    predictions = (
        probabilities >= CLASSIFICATION_THRESHOLD
    ).astype(np.int8)

    return {
        "pr_auc": float(
            average_precision_score(labels, probabilities)
        ),
        "roc_auc": float(
            roc_auc_score(labels, probabilities)
        ),
        "accuracy_at_0_50": float(
            accuracy_score(labels, predictions)
        ),
        "precision_at_0_50": float(
            precision_score(labels, predictions, zero_division=0)
        ),
        "recall_at_0_50": float(
            recall_score(labels, predictions, zero_division=0)
        ),
    }


def _summary(values: list[float]) -> dict[str, float]:
    """Summarise scores across grouped folds."""

    scores = np.asarray(values, dtype=np.float64)

    return {
        "mean": float(np.mean(scores)),
        "standard_deviation": float(np.std(scores, ddof=1)),
        "minimum": float(np.min(scores)),
        "maximum": float(np.max(scores)),
        "percentile_2_5": float(np.quantile(scores, 0.025)),
        "percentile_97_5": float(np.quantile(scores, 0.975)),
    }


def run_grouped_comparison() -> dict[str, Any]:
    """Run repeated stratified group cross-validation."""

    data = load_training_data(
        split_directory=DEFAULT_SPLIT_DIRECTORY,
        manifest_file=DEFAULT_MANIFEST_FILE,
    )

    development_frame = pd.concat(
        [data.train.frame, data.validation.frame],
        ignore_index=True,
    )

    features = development_frame.loc[
        :,
        list(data.feature_columns),
    ].to_numpy(dtype=np.float64)

    labels = development_frame.loc[
        :,
        TARGET_COLUMN,
    ].to_numpy(dtype=np.int8)

    groups = development_frame.loc[
        :,
        EXPERIMENT_ID_COLUMN,
    ].astype(str).to_numpy()

    if len(np.unique(groups)) != 51:
        raise GroupedComparisonError(
            "Expected exactly 51 development experiments "
            "(42 train + 9 validation)."
        )

    if len(np.unique(labels)) != 2:
        raise GroupedComparisonError(
            "Development data must contain both classes."
        )

    model_names = (
        "logistic_regression",
        "random_forest",
    )

    fold_results: list[dict[str, Any]] = []
    all_scores: dict[str, dict[str, list[float]]] = {
        model_name: {
            "pr_auc": [],
            "roc_auc": [],
            "accuracy_at_0_50": [],
            "precision_at_0_50": [],
            "recall_at_0_50": [],
        }
        for model_name in model_names
    }

    for repeat_index in range(N_REPEATS):
        seed = BASELINE_RANDOM_SEED + repeat_index

        splitter = StratifiedGroupKFold(
            n_splits=N_SPLITS,
            shuffle=True,
            random_state=seed,
        )

        for fold_index, (fit_index, evaluation_index) in enumerate(
            splitter.split(features, labels, groups),
            start=1,
        ):
            fit_groups = set(groups[fit_index])
            evaluation_groups = set(groups[evaluation_index])

            if not fit_groups.isdisjoint(evaluation_groups):
                raise GroupedComparisonError(
                    "Experiment leakage detected between folds."
                )

            if len(np.unique(labels[evaluation_index])) != 2:
                raise GroupedComparisonError(
                    "A grouped evaluation fold contains one class only."
                )

            models = build_baseline_models(random_seed=seed)

            fold_record: dict[str, Any] = {
                "repeat": repeat_index + 1,
                "fold": fold_index,
                "random_seed": seed,
                "fit_experiment_count": len(fit_groups),
                "evaluation_experiment_count": len(
                    evaluation_groups
                ),
                "evaluation_row_count": len(evaluation_index),
                "models": {},
            }

            for model_name in model_names:
                model = models[model_name]
                model.fit(
                    features[fit_index],
                    labels[fit_index],
                )

                probabilities = model.predict_proba(
                    features[evaluation_index]
                )[:, 1]

                metric_values = _metrics(
                    labels[evaluation_index],
                    probabilities,
                )

                fold_record["models"][model_name] = metric_values

                for metric_name, value in metric_values.items():
                    all_scores[model_name][metric_name].append(
                        value
                    )

            fold_results.append(fold_record)

    model_summaries = {
        model_name: {
            metric_name: _summary(values)
            for metric_name, values in scores.items()
        }
        for model_name, scores in all_scores.items()
    }

    logistic_pr_auc = model_summaries[
        "logistic_regression"
    ]["pr_auc"]["mean"]

    random_forest_pr_auc = model_summaries[
        "random_forest"
    ]["pr_auc"]["mean"]

    winner = (
        "logistic_regression"
        if logistic_pr_auc >= random_forest_pr_auc
        else "random_forest"
    )

    report = {
        "schema_version": "1.0.0",
        "analysis_name": (
            "repeated_stratified_group_model_comparison"
        ),
        "purpose": (
            "Fair comparison of Logistic Regression and Random Forest "
            "using the same 18 features and experiment-grouped "
            "development-data folds."
        ),
        "dataset": {
            "included_splits": ["train", "validation"],
            "excluded_split": "test",
            "row_count": len(development_frame),
            "experiment_count": len(np.unique(groups)),
            "feature_count": len(data.feature_columns),
            "feature_columns": list(data.feature_columns),
            "target_column": TARGET_COLUMN,
            "experiment_id_column": EXPERIMENT_ID_COLUMN,
            "assignment_sha256": data.assignment_sha256,
        },
        "method": {
            "splitter": "StratifiedGroupKFold",
            "splits_per_repeat": N_SPLITS,
            "repeats": N_REPEATS,
            "total_grouped_evaluations": N_SPLITS * N_REPEATS,
            "classification_threshold_for_operating_metrics": (
                CLASSIFICATION_THRESHOLD
            ),
            "primary_comparison_metric": "mean_pr_auc",
            "model_settings_source": (
                "model_training.train_baselines."
                "build_baseline_models"
            ),
        },
        "results": model_summaries,
        "primary_metric_result": {
            "logistic_regression_mean_pr_auc": logistic_pr_auc,
            "random_forest_mean_pr_auc": random_forest_pr_auc,
            "mean_pr_auc_difference_logistic_minus_forest": (
                logistic_pr_auc - random_forest_pr_auc
            ),
            "winner": winner,
        },
        "fold_results": fold_results,
        "interpretation_boundary": {
            "test_split_used": False,
            "new_hyperparameter_tuning_performed": False,
            "final_model_or_threshold_changed": False,
            "percentile_ranges_are_fold_score_distributions": True,
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")

    return report


def main() -> int:
    """Run and print the comparison."""

    try:
        report = run_grouped_comparison()
    except GroupedComparisonError as error:
        print(f"Grouped comparison failed: {error}")
        return 1

    results = report["results"]

    print("Repeated experiment-grouped comparison completed:")
    print(
        "  Development data: "
        f"{report['dataset']['row_count']} rows from "
        f"{report['dataset']['experiment_count']} experiments"
    )
    print("  Frozen test split: excluded")

    for model_name in (
        "logistic_regression",
        "random_forest",
    ):
        pr_auc = results[model_name]["pr_auc"]
        print(
            f"  {model_name}: mean PR-AUC "
            f"{pr_auc['mean']:.4f} "
            f"(fold range {pr_auc['minimum']:.4f}–"
            f"{pr_auc['maximum']:.4f})"
        )

    primary = report["primary_metric_result"]
    print(f"  Winner by mean PR-AUC: {primary['winner']}")
    print(f"  Saved: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())