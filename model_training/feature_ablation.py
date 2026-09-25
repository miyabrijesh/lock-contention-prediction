"""Experiment-grouped top-k feature ablation for the v1 dataset."""

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    EXPERIMENT_ID_COLUMN,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "analysis_outputs"
    / "feature_ablation_v1.json"
)

TOP_K_VALUES: Final[tuple[int, ...]] = (3, 6, 9, 12, 18)
N_SPLITS: Final[int] = 5
N_REPEATS: Final[int] = 5
BASE_RANDOM_SEED: Final[int] = 3301


class FeatureAblationError(ValueError):
    """Raised when feature ablation cannot run safely."""


def _build_model(seed: int) -> Pipeline:
    """Create the approved Logistic Regression configuration."""

    return Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=2000,
                    random_state=seed,
                    solver="lbfgs",
                ),
            ),
        ]
    )


def _summary(values: list[float]) -> dict[str, float]:
    """Summarise scores across grouped folds."""

    scores = np.asarray(values, dtype=np.float64)

    return {
        "mean": float(np.mean(scores)),
        "standard_deviation": float(np.std(scores, ddof=1)),
        "minimum": float(np.min(scores)),
        "maximum": float(np.max(scores)),
    }


def run_feature_ablation() -> dict[str, Any]:
    """Run leakage-safe top-k feature ablation."""

    data = load_training_data(
        split_directory=DEFAULT_SPLIT_DIRECTORY,
        manifest_file=DEFAULT_MANIFEST_FILE,
    )

    development_frame = pd.concat(
        [data.train.frame, data.validation.frame],
        ignore_index=True,
    )

    feature_names = list(FEATURE_COLUMNS)

    features = development_frame.loc[
        :,
        feature_names,
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
        raise FeatureAblationError(
            "Expected 51 development experiments."
        )

    scores_by_k: dict[int, list[float]] = {
        value: [] for value in TOP_K_VALUES
    }

    feature_rank_totals = {
        name: 0.0 for name in feature_names
    }

    feature_top_k_counts = {
        name: {value: 0 for value in TOP_K_VALUES}
        for name in feature_names
    }

    fold_results: list[dict[str, Any]] = []

    for repeat_index in range(N_REPEATS):
        seed = BASE_RANDOM_SEED + repeat_index

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
                raise FeatureAblationError(
                    "Experiment leakage detected between folds."
                )

            ranking_model = _build_model(seed)
            ranking_model.fit(
                features[fit_index],
                labels[fit_index],
            )

            coefficients = np.abs(
                ranking_model.named_steps[
                    "classifier"
                ].coef_[0]
            )

            ranked_indices = np.lexsort(
                (
                    np.arange(len(feature_names)),
                    -coefficients,
                )
            )

            ranked_features = [
                feature_names[index]
                for index in ranked_indices
            ]

            for rank, feature_name in enumerate(
                ranked_features,
                start=1,
            ):
                feature_rank_totals[feature_name] += rank

            fold_record: dict[str, Any] = {
                "repeat": repeat_index + 1,
                "fold": fold_index,
                "fit_experiment_count": len(fit_groups),
                "evaluation_experiment_count": len(
                    evaluation_groups
                ),
                "ranked_features": ranked_features,
                "top_k_pr_auc": {},
            }

            for top_k in TOP_K_VALUES:
                selected_indices = ranked_indices[:top_k]

                model = _build_model(seed)
                model.fit(
                    features[fit_index][:, selected_indices],
                    labels[fit_index],
                )

                probabilities = model.predict_proba(
                    features[evaluation_index][:, selected_indices]
                )[:, 1]

                pr_auc = float(
                    average_precision_score(
                        labels[evaluation_index],
                        probabilities,
                    )
                )

                scores_by_k[top_k].append(pr_auc)
                fold_record["top_k_pr_auc"][str(top_k)] = pr_auc

                for feature_name in ranked_features[:top_k]:
                    feature_top_k_counts[feature_name][top_k] += 1

            fold_results.append(fold_record)

    total_folds = N_SPLITS * N_REPEATS

    top_k_results = {
        str(top_k): _summary(scores)
        for top_k, scores in scores_by_k.items()
    }

    feature_ranking = [
        {
            "feature": feature_name,
            "mean_rank": (
                feature_rank_totals[feature_name] / total_folds
            ),
            "top_k_selection_count": feature_top_k_counts[
                feature_name
            ],
        }
        for feature_name in feature_names
    ]

    feature_ranking.sort(
        key=lambda item: item["mean_rank"]
    )

    report = {
        "schema_version": "1.0.0",
        "analysis_name": "leakage_safe_feature_ablation",
        "purpose": (
            "Assess whether smaller top-k feature subsets retain the "
            "ranking performance of the approved 18-feature model."
        ),
        "dataset": {
            "included_splits": ["train", "validation"],
            "excluded_split": "test",
            "row_count": len(development_frame),
            "experiment_count": len(np.unique(groups)),
            "candidate_feature_count": len(feature_names),
            "target_column": TARGET_COLUMN,
        },
        "method": {
            "model": "logistic_regression",
            "feature_ranking_method": (
                "absolute standardized logistic-regression "
                "coefficient, learned inside each fit fold"
            ),
            "splitter": "StratifiedGroupKFold",
            "splits_per_repeat": N_SPLITS,
            "repeats": N_REPEATS,
            "total_grouped_evaluations": total_folds,
            "evaluation_metric": "pr_auc",
            "top_k_values": list(TOP_K_VALUES),
        },
        "top_k_results": top_k_results,
        "feature_ranking": feature_ranking,
        "fold_results": fold_results,
        "interpretation_boundary": {
            "test_split_used": False,
            "model_retrained_for_deployment": False,
            "feature_selection_performed_inside_fit_fold": True,
            "full_18_feature_model_remains_the_deployed_model": True,
        },
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")

    return report


def main() -> int:
    """Run and print the ablation result."""

    try:
        report = run_feature_ablation()
    except FeatureAblationError as error:
        print(f"Feature ablation failed: {error}")
        return 1

    print("Leakage-safe feature ablation completed:")
    print(
        f"  Development data: {report['dataset']['row_count']} "
        f"rows from {report['dataset']['experiment_count']} "
        "experiments"
    )
    print("  Frozen test split: excluded")

    for top_k, result in report["top_k_results"].items():
        print(
            f"  Top {top_k}: mean PR-AUC "
            f"{result['mean']:.4f} "
            f"(range {result['minimum']:.4f}–"
            f"{result['maximum']:.4f})"
        )

    print("  Most consistently important features:")
    for item in report["feature_ranking"][:5]:
        print(
            f"    {item['feature']}: mean rank "
            f"{item['mean_rank']:.2f}"
        )

    print(f"  Saved: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())