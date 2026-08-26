from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session

from backend.app.models.prediction import ModelVersion
from backend.app.repositories.model_repository import (
    add_model_audit_event,
    find_locked_active_model,
    find_locked_model_version,
    get_active_model_version,
    get_model_version,
    list_model_versions,
    lock_model_versions,
)
from backend.app.schemas.model import (
    ModelActivationRequest,
)
from model_training.inference import (
    MODEL_CONTRACT_VERSION,
    REGISTERED_MODEL_NAME,
    ContentionPredictor,
    InferenceError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_TARGET_HORIZON_SEC = 30

EXPECTED_TARGET_COLUMN = (
    "future_high_contention_30s"
)


class ModelServiceError(Exception):
    """Expected model-management service failure."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message
        self.status_code = status_code


def _database_error() -> ModelServiceError:
    """Create the standard unexpected database error."""

    return ModelServiceError(
        code="INTERNAL_ERROR",
        message="An unexpected database error occurred.",
        status_code=500,
    )


def _artifact_error(
    message: str,
) -> ModelServiceError:
    """Create a safe model-artifact validation error."""

    return ModelServiceError(
        code="MODEL_ARTIFACT_INVALID",
        message=message,
        status_code=409,
    )


def _provenance_error(
    message: str,
) -> ModelServiceError:
    """Create a safe registered-provenance error."""

    return ModelServiceError(
        code="MODEL_PROVENANCE_INVALID",
        message=message,
        status_code=409,
    )


def _resolve_artifact_path(
    stored_path: str,
) -> Path:
    """Resolve a safe artifact path inside the project."""

    normalized_path = stored_path.strip()

    if not normalized_path:
        raise _artifact_error(
            "The registered model artifact path is empty."
        )

    relative_path = Path(normalized_path)

    if relative_path.is_absolute():
        raise _artifact_error(
            "The registered model artifact path must "
            "be project-relative."
        )

    resolved_path = (
        PROJECT_ROOT / relative_path
    ).resolve()

    if not resolved_path.is_relative_to(
        PROJECT_ROOT
    ):
        raise _artifact_error(
            "The registered model artifact path "
            "escapes the project directory."
        )

    if not resolved_path.is_file():
        raise _artifact_error(
            "The registered model artifact was not found."
        )

    return resolved_path


def _required_ordered_features(
    model_version: ModelVersion,
) -> tuple[str, ...]:
    """Read and validate registered ordered features."""

    ordered_features_value = (
        model_version.feature_schema.get(
            "ordered_features"
        )
    )

    if not isinstance(
        ordered_features_value,
        list,
    ):
        raise _provenance_error(
            "The registered ordered feature list is invalid."
        )

    if not ordered_features_value:
        raise _provenance_error(
            "The registered ordered feature list is empty."
        )

    if not all(
        isinstance(feature_name, str)
        and bool(feature_name)
        for feature_name in ordered_features_value
    ):
        raise _provenance_error(
            "The registered ordered feature list "
            "contains an invalid name."
        )

    return tuple(ordered_features_value)


def _required_feature_count(
    model_version: ModelVersion,
) -> int:
    """Read and validate the registered feature count."""

    feature_count_value = (
        model_version.feature_schema.get(
            "feature_count"
        )
    )

    if (
        not isinstance(feature_count_value, int)
        or isinstance(feature_count_value, bool)
        or feature_count_value <= 0
    ):
        raise _provenance_error(
            "The registered feature count is invalid."
        )

    return feature_count_value


def _required_threshold(
    model_version: ModelVersion,
) -> Decimal:
    """Read and validate the registered high-risk threshold."""

    threshold_value = (
        model_version.risk_thresholds.get(
            "high_contention_probability"
        )
    )

    if (
        not isinstance(
            threshold_value,
            (int, float, Decimal),
        )
        or isinstance(threshold_value, bool)
    ):
        raise _provenance_error(
            "The registered classification "
            "threshold is invalid."
        )

    threshold = Decimal(
        str(threshold_value)
    )

    if threshold < 0 or threshold > 1:
        raise _provenance_error(
            "The registered classification threshold "
            "must be between zero and one."
        )

    return threshold


def _validate_registered_provenance(
    model_version: ModelVersion,
    predictor: ContentionPredictor,
) -> None:
    """Match registered metadata to the verified artifact."""

    if model_version.name != REGISTERED_MODEL_NAME:
        raise _provenance_error(
            "The registered model name does not match "
            "the inference contract."
        )

    if (
        model_version.target_horizon_sec
        != EXPECTED_TARGET_HORIZON_SEC
    ):
        raise _provenance_error(
            "The registered target horizon does not "
            "match the inference contract."
        )

    feature_schema_version = (
        model_version.feature_schema.get(
            "schema_version"
        )
    )

    if (
        feature_schema_version
        != MODEL_CONTRACT_VERSION
    ):
        raise _provenance_error(
            "The registered feature schema version "
            "does not match the inference contract."
        )

    target_column = (
        model_version.feature_schema.get(
            "target_column"
        )
    )

    if target_column != EXPECTED_TARGET_COLUMN:
        raise _provenance_error(
            "The registered target column does not "
            "match the inference contract."
        )

    ordered_features = (
        _required_ordered_features(
            model_version
        )
    )

    if ordered_features != predictor.feature_columns:
        raise _provenance_error(
            "The registered feature order does not "
            "match the model artifact."
        )

    feature_count = _required_feature_count(
        model_version
    )

    if feature_count != len(ordered_features):
        raise _provenance_error(
            "The registered feature count does not "
            "match the ordered feature list."
        )

    threshold = _required_threshold(
        model_version
    )

    predictor_threshold = Decimal(
        str(predictor.threshold)
    )

    if threshold != predictor_threshold:
        raise _provenance_error(
            "The registered classification threshold "
            "does not match the model artifact."
        )

    threshold_schema_version = (
        model_version.risk_thresholds.get(
            "schema_version"
        )
    )

    if (
        threshold_schema_version
        != MODEL_CONTRACT_VERSION
    ):
        raise _provenance_error(
            "The registered threshold schema version "
            "does not match the inference contract."
        )

    registered_sha256 = (
        model_version.artifact_sha256.strip()
    )

    if (
        predictor.artifact_sha256
        != registered_sha256
    ):
        raise _provenance_error(
            "The verified artifact checksum does not "
            "match the registered model."
        )


def load_validated_predictor(
    model_version: ModelVersion,
) -> ContentionPredictor:
    """Load and validate one registered model artifact."""

    artifact_path = _resolve_artifact_path(
        model_version.artifact_path
    )

    expected_sha256 = (
        model_version.artifact_sha256.strip()
    )

    try:
        predictor = ContentionPredictor.load(
            artifact_path=artifact_path,
            expected_sha256=expected_sha256,
        )
    except InferenceError as error:
        raise _artifact_error(
            "The registered model artifact failed "
            "inference validation."
        ) from error
    except OSError as error:
        raise _artifact_error(
            "The registered model artifact could "
            "not be read."
        ) from error

    _validate_registered_provenance(
        model_version,
        predictor,
    )

    return predictor


def list_registered_models(
    session: Session,
) -> list[ModelVersion]:
    """Return every registered model version."""

    try:
        return list_model_versions(session)
    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def read_registered_model(
    session: Session,
    model_version_id: UUID,
) -> ModelVersion:
    """Return one registered model version."""

    try:
        model_version = get_model_version(
            session,
            model_version_id,
        )

        if model_version is None:
            raise ModelServiceError(
                code="MODEL_VERSION_NOT_FOUND",
                message=(
                    "The requested model version "
                    "was not found."
                ),
                status_code=404,
            )

        return model_version

    except ModelServiceError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def read_active_model(
    session: Session,
) -> ModelVersion:
    """Return the active model without selecting a fallback."""

    try:
        model_version = get_active_model_version(
            session
        )

        if model_version is None:
            raise ModelServiceError(
                code="ACTIVE_MODEL_NOT_FOUND",
                message=(
                    "No active model version was found."
                ),
                status_code=404,
            )

        return model_version

    except ModelServiceError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def activate_model_version(
    session: Session,
    model_version_id: UUID,
    command: ModelActivationRequest,
) -> ModelVersion:
    """Verify and activate one model in a transaction."""

    try:
        locked_models = lock_model_versions(
            session
        )

        requested_model = (
            find_locked_model_version(
                locked_models,
                model_version_id,
            )
        )

        if requested_model is None:
            raise ModelServiceError(
                code="MODEL_VERSION_NOT_FOUND",
                message=(
                    "The requested model version "
                    "was not found."
                ),
                status_code=404,
            )

        load_validated_predictor(
            requested_model
        )

        previous_active_model = (
            find_locked_active_model(
                locked_models
            )
        )

        already_active = (
            previous_active_model is not None
            and previous_active_model.id
            == requested_model.id
        )

        previous_active_model_id = (
            str(previous_active_model.id)
            if previous_active_model is not None
            else None
        )

        if (
            previous_active_model is not None
            and not already_active
        ):
            previous_active_model.is_active = False

            # Release the partial unique index entry before
            # activating the replacement model. Both updates
            # remain inside the same database transaction.
            session.flush()

        requested_model.is_active = True

        audit_details: dict[str, object] = {
            "model_name": requested_model.name,
            "artifact_sha256": (
                requested_model.artifact_sha256.strip()
            ),
            "previous_active_model_id": (
                previous_active_model_id
            ),
            "already_active": already_active,
            "reason": command.reason,
        }

        add_model_audit_event(
            session,
            model_version_id=requested_model.id,
            action="model_activated",
            actor=command.actor,
            details=audit_details,
        )

        session.commit()
        session.refresh(requested_model)

        return requested_model

    except ModelServiceError:
        session.rollback()
        raise

    except IntegrityError as error:
        session.rollback()

        raise ModelServiceError(
            code="MODEL_ACTIVATION_CONFLICT",
            message=(
                "The model activation conflicted with "
                "another activation request."
            ),
            status_code=409,
        ) from error

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error