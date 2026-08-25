import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np

from backend.app.models.enums import RiskLevel
from model_training.training_data import (
    FEATURE_COLUMNS,
)


PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

DEFAULT_MODEL_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

MODEL_CONTRACT_VERSION: Final[str] = "1.0.0"

REGISTERED_MODEL_NAME: Final[str] = (
    "contention-logistic-v1"
)

ARTIFACT_MODEL_NAME: Final[str] = (
    "logistic_regression"
)

EXPECTED_ARTIFACT_SHA256: Final[str] = (
    "e255db6ee5d3a59c075b6249fc7d355e"
    "5107dd56b2ed7ddab695bb029db24704"
)

EXPECTED_THRESHOLD: Final[float] = 0.34
TARGET_HORIZON_SECONDS: Final[int] = 30

RATIO_FEATURES: Final[frozenset[str]] = frozenset({
    "connection_utilization",
    "failure_rate",
    "hotspot_ratio",
    "booking_success_rate",
    "conflict_rate",
    "timeout_rate",
})


class InferenceError(ValueError):
    """Raised when safe model inference cannot run."""


@dataclass(frozen=True)
class InferenceResult:
    """One deterministic contention prediction."""

    probability: float
    risk_level: RiskLevel
    threshold_used: float
    model_name: str
    model_artifact_sha256: str
    feature_schema_version: str
    target_horizon_sec: int

    def as_dictionary(self) -> dict[str, object]:
        """Return a JSON-compatible prediction result."""

        return {
            "probability": self.probability,
            "risk_level": self.risk_level.value,
            "threshold_used": self.threshold_used,
            "model_name": self.model_name,
            "model_artifact_sha256": (
                self.model_artifact_sha256
            ),
            "feature_schema_version": (
                self.feature_schema_version
            ),
            "target_horizon_sec": (
                self.target_horizon_sec
            ),
        }


def _sha256(path: Path) -> str:
    """Calculate the SHA-256 checksum of one file."""

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _validated_numeric_feature(
    *,
    feature_name: str,
    raw_value: object,
) -> float:
    """Validate and normalize one model feature."""

    if isinstance(raw_value, bool):
        raise InferenceError(
            f"{feature_name} must be numeric, "
            "not boolean."
        )

    if not isinstance(
        raw_value,
        (int, float, Decimal),
    ):
        raise InferenceError(
            f"{feature_name} must be numeric."
        )

    value = float(raw_value)

    if not np.isfinite(value):
        raise InferenceError(
            f"{feature_name} must be finite."
        )

    if value < 0:
        raise InferenceError(
            f"{feature_name} must not be negative."
        )

    if (
        feature_name in RATIO_FEATURES
        and value > 1
    ):
        raise InferenceError(
            f"{feature_name} must be between "
            "0 and 1."
        )

    return value


def validate_feature_mapping(
    features: Mapping[str, object],
) -> tuple[float, ...]:
    """Validate and order exactly 18 feature values."""

    supplied_features = set(features)
    required_features = set(FEATURE_COLUMNS)

    missing_features = (
        required_features - supplied_features
    )

    if missing_features:
        raise InferenceError(
            "Missing required features: "
            + ", ".join(sorted(missing_features))
        )

    unexpected_features = (
        supplied_features - required_features
    )

    if unexpected_features:
        raise InferenceError(
            "Unexpected features: "
            + ", ".join(sorted(unexpected_features))
        )

    return tuple(
        _validated_numeric_feature(
            feature_name=feature_name,
            raw_value=features[feature_name],
        )
        for feature_name in FEATURE_COLUMNS
    )


def risk_level_for_probability(
    probability: float,
    *,
    threshold: float = EXPECTED_THRESHOLD,
) -> RiskLevel:
    """Map one probability to LOW or HIGH risk."""

    if not np.isfinite(probability):
        raise InferenceError(
            "Prediction probability must be finite."
        )

    if probability < 0 or probability > 1:
        raise InferenceError(
            "Prediction probability must be "
            "between 0 and 1."
        )

    if not np.isfinite(threshold):
        raise InferenceError(
            "Prediction threshold must be finite."
        )

    if threshold <= 0 or threshold >= 1:
        raise InferenceError(
            "Prediction threshold must be "
            "between 0 and 1."
        )

    if probability >= threshold:
        return RiskLevel.HIGH

    return RiskLevel.LOW


def features_from_metric_sample(
    metric_sample: Any,
) -> dict[str, object]:
    """Extract model inputs from one complete metric sample."""

    is_complete = getattr(
        metric_sample,
        "is_complete",
        None,
    )

    if is_complete is not True:
        raise InferenceError(
            "Metric sample must be complete "
            "before inference."
        )

    extracted_features: dict[str, object] = {}

    for feature_name in FEATURE_COLUMNS:
        try:
            feature_value = getattr(
                metric_sample,
                feature_name,
            )
        except AttributeError as error:
            raise InferenceError(
                "Metric sample is missing feature: "
                f"{feature_name}."
            ) from error

        extracted_features[feature_name] = (
            feature_value
        )

    return extracted_features


def _load_verified_artifact(
    *,
    artifact_path: Path,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Verify checksum before loading the Joblib artifact."""

    if not artifact_path.is_file():
        raise InferenceError(
            "Configured model artifact was not found."
        )

    if (
        len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_sha256.lower()
        )
    ):
        raise InferenceError(
            "Expected artifact SHA-256 is invalid."
        )

    actual_sha256 = _sha256(
        artifact_path
    )

    if actual_sha256 != expected_sha256:
        raise InferenceError(
            "Model artifact checksum verification failed."
        )

    loaded_value: Any = joblib.load(
        artifact_path
    )

    if not isinstance(loaded_value, dict):
        raise InferenceError(
            "Model artifact must contain a dictionary."
        )

    artifact: dict[str, Any] = loaded_value

    return artifact, actual_sha256


def _validate_artifact_contract(
    artifact: dict[str, Any],
) -> tuple[Any, float]:
    """Validate model metadata stored in the artifact."""

    if (
        artifact.get("schema_version")
        != MODEL_CONTRACT_VERSION
    ):
        raise InferenceError(
            "Model artifact schema version "
            "does not match the inference contract."
        )

    if (
        artifact.get("model_name")
        != ARTIFACT_MODEL_NAME
    ):
        raise InferenceError(
            "Model artifact contains an "
            "unexpected model name."
        )

    artifact_features = artifact.get(
        "feature_columns"
    )

    if artifact_features != list(
        FEATURE_COLUMNS
    ):
        raise InferenceError(
            "Model artifact feature order does "
            "not match the inference contract."
        )

    threshold_value = artifact.get(
        "classification_threshold"
    )

    if isinstance(threshold_value, bool):
        raise InferenceError(
            "Model artifact threshold is invalid."
        )

    if not isinstance(
        threshold_value,
        (int, float, Decimal),
    ):
        raise InferenceError(
            "Model artifact threshold is invalid."
        )

    threshold = float(threshold_value)

    if not np.isclose(
        threshold,
        EXPECTED_THRESHOLD,
    ):
        raise InferenceError(
            "Model artifact threshold does not "
            "match the inference contract."
        )

    model = artifact.get("model")

    if model is None:
        raise InferenceError(
            "Model artifact does not contain a model."
        )

    if not hasattr(model, "predict_proba"):
        raise InferenceError(
            "Model artifact does not support "
            "probability prediction."
        )

    return model, threshold


class ContentionPredictor:
    """Checksum-verified contention inference engine."""

    def __init__(
        self,
        *,
        model: Any,
        threshold: float,
        artifact_sha256: str,
    ) -> None:
        self._model = model
        self._threshold = threshold
        self._artifact_sha256 = (
            artifact_sha256
        )

    @property
    def threshold(self) -> float:
        """Return the frozen classification threshold."""

        return self._threshold

    @property
    def artifact_sha256(self) -> str:
        """Return the verified artifact checksum."""

        return self._artifact_sha256

    @property
    def feature_columns(self) -> tuple[str, ...]:
        """Return the frozen ordered feature names."""

        return FEATURE_COLUMNS

    @classmethod
    def load(
        cls,
        *,
        artifact_path: Path = DEFAULT_MODEL_FILE,
        expected_sha256: str = (
            EXPECTED_ARTIFACT_SHA256
        ),
    ) -> "ContentionPredictor":
        """Load a checksum-verified model artifact."""

        artifact, actual_sha256 = (
            _load_verified_artifact(
                artifact_path=artifact_path,
                expected_sha256=expected_sha256,
            )
        )

        model, threshold = (
            _validate_artifact_contract(
                artifact
            )
        )

        return cls(
            model=model,
            threshold=threshold,
            artifact_sha256=actual_sha256,
        )

    def _predict_probability(
        self,
        ordered_features: tuple[float, ...],
    ) -> float:
        """Run the underlying probability classifier."""

        feature_matrix = np.asarray(
            [ordered_features],
            dtype=np.float64,
        )

        predict_proba = getattr(
            self._model,
            "predict_proba",
            None,
        )

        if predict_proba is None:
            raise InferenceError(
                "Loaded model does not support "
                "probability prediction."
            )

        probability_matrix = np.asarray(
            predict_proba(feature_matrix),
            dtype=np.float64,
        )

        if probability_matrix.shape != (1, 2):
            raise InferenceError(
                "Loaded model returned an invalid "
                "probability matrix."
            )

        probability = float(
            probability_matrix[0, 1]
        )

        if not np.isfinite(probability):
            raise InferenceError(
                "Loaded model returned a non-finite "
                "probability."
            )

        if probability < 0 or probability > 1:
            raise InferenceError(
                "Loaded model returned a probability "
                "outside the range 0 to 1."
            )

        return probability

    def predict(
        self,
        features: Mapping[str, object],
    ) -> InferenceResult:
        """Predict future contention from validated features."""

        ordered_features = (
            validate_feature_mapping(
                features
            )
        )

        probability = self._predict_probability(
            ordered_features
        )

        risk_level = risk_level_for_probability(
            probability,
            threshold=self._threshold,
        )

        return InferenceResult(
            probability=probability,
            risk_level=risk_level,
            threshold_used=self._threshold,
            model_name=REGISTERED_MODEL_NAME,
            model_artifact_sha256=(
                self._artifact_sha256
            ),
            feature_schema_version=(
                MODEL_CONTRACT_VERSION
            ),
            target_horizon_sec=(
                TARGET_HORIZON_SECONDS
            ),
        )

    def predict_metric_sample(
        self,
        metric_sample: Any,
    ) -> InferenceResult:
        """Predict from one complete MetricSample."""

        features = features_from_metric_sample(
            metric_sample
        )

        return self.predict(features)