# Model Activation Contract v1

## Status

Checkpoint 35A implementation baseline.

## Purpose

This contract defines how a registered lock-contention prediction model becomes
the single active model used by the application.

Model registration and model activation are separate operations. Registration
stores a verified model version, while activation explicitly authorizes that
version for application inference.

The registered Phase 6 model must remain inactive until the controlled
activation operation succeeds.

## Registered candidate

The Phase 6 candidate has the following identity:

| Field | Value |
|---|---|
| Model name | `contention-logistic-v1` |
| Algorithm | `StandardScaler + LogisticRegression` |
| Target horizon | 30 seconds |
| Feature count | 18 |
| Classification threshold | 0.34 |
| Artifact path | `artifacts/models/contention_candidate_v1.joblib` |
| Artifact SHA-256 | `e255db6ee5d3a59c075b6249fc7d355e5107dd56b2ed7ddab695bb029db24704` |
| Initial active state | `false` |

The database UUID is installation-specific and must not be hard-coded in
application source code.

## Activation invariants

The activation implementation must preserve all of the following rules:

1. At most one model version may have `is_active = true`.
2. A model must already exist in `model_versions` before it can be activated.
3. The artifact file must exist before activation.
4. The artifact SHA-256 must match the value stored in `model_versions`.
5. The artifact must pass the existing inference-engine loading checks.
6. Activation must require the administrator API token.
7. The request must identify the administrator through a validated `actor`.
8. Activation and its audit event must be committed in one database
   transaction.
9. Any failure must roll back both the activation state and the audit event.
10. Activating an already-active model must be idempotent.
11. An activation request must never modify immutable model provenance.
12. The application must not silently select the newest model when no model is
    active.

## Single-active-model enforcement

PostgreSQL already contains the partial unique index:

```text
uq_model_versions_single_active
```

The index applies only when:

```text
is_active = true
```

This database constraint is the final protection against two simultaneously
active model versions.

The service must also lock relevant model rows while changing activation
state. Application-level checks alone are not sufficient because concurrent
requests could otherwise both observe the same earlier state.

## Transaction sequence

A successful activation must execute the following operations as one
transaction:

1. Load and lock the requested model version.
2. Reject the request if the model does not exist.
3. Resolve the stored artifact path safely inside the project directory.
4. Verify that the artifact file exists.
5. Calculate the artifact SHA-256.
6. Compare the calculated checksum with the registered checksum.
7. Load the artifact through the verified inference engine.
8. Lock any currently active model version.
9. Set other active model versions to inactive.
10. Set the requested model version to active.
11. Add one model activation audit event.
12. Commit the transaction.
13. Refresh and return the activated model version.

If any step fails, the transaction must be rolled back.

## Artifact validation

Activation must not trust the database record by itself.

The implementation must verify:

- the path is a project-relative artifact path;
- the path cannot escape the project directory;
- the file exists and is a regular file;
- the file SHA-256 matches `artifact_sha256`;
- the artifact can be loaded by `ContentionPredictor`;
- the artifact feature order matches the registered feature schema;
- the artifact threshold matches the registered risk threshold;
- the artifact target horizon matches the registered target horizon.

A checksum or contract mismatch must prevent activation.

## Administrative API

All model-management endpoints use the prefix:

```text
/api/v1/models
```

Every endpoint in this contract requires a valid:

```text
X-Admin-Token
```

### List registered models

```http
GET /api/v1/models
```

Returns all registered model versions in descending creation order.

### Read one registered model

```http
GET /api/v1/models/{model_version_id}
```

Returns one registered model version.

A missing model returns:

```text
404 MODEL_VERSION_NOT_FOUND
```

### Read the active model

```http
GET /api/v1/models/active
```

Returns the currently active model version.

If no model is active, the endpoint returns:

```text
404 ACTIVE_MODEL_NOT_FOUND
```

It must not automatically activate or select a model.

### Activate one model

```http
POST /api/v1/models/{model_version_id}/activate
```

Request body:

```json
{
  "actor": "local-admin",
  "reason": "Approved Phase 7 deployment"
}
```

Successful response:

```text
200 OK
```

The response contains the activated model version.

## Request validation

The activation request uses the following limits:

| Field | Requirement |
|---|---|
| `actor` | Required, stripped, 1–100 characters |
| `reason` | Optional, stripped, maximum 1000 characters |
| Extra fields | Rejected |

Whitespace-only actor values must be rejected.

A whitespace-only reason must be normalized to `null`.

## API error contract

Expected service failures use machine-readable error codes:

| HTTP status | Error code | Meaning |
|---:|---|---|
| 401 | `INVALID_ADMIN_TOKEN` | Admin authentication failed |
| 404 | `MODEL_VERSION_NOT_FOUND` | Requested model does not exist |
| 404 | `ACTIVE_MODEL_NOT_FOUND` | No active model exists |
| 409 | `MODEL_ARTIFACT_INVALID` | Artifact or inference contract is invalid |
| 409 | `MODEL_PROVENANCE_INVALID` | Stored model provenance is inconsistent |
| 500 | `INTERNAL_ERROR` | Unexpected database or filesystem failure |

Error responses use this structure:

```json
{
  "detail": {
    "code": "MODEL_VERSION_NOT_FOUND",
    "message": "The requested model version was not found."
  }
}
```

Internal exception text, local absolute paths, database details, and secrets
must not be returned to API clients.

## Idempotent activation

If the requested model is already active and its artifact still passes
validation:

- the request succeeds;
- the model remains active;
- no second model becomes active;
- immutable provenance remains unchanged;
- an audit event records that activation was requested for the already-active
  model.

The audit details distinguish an initial activation from an idempotent
activation request.

## Audit event

A successful activation creates an `audit_events` record with:

| Column | Value |
|---|---|
| `experiment_run_id` | `null` |
| `action` | `model_activated` |
| `actor` | Validated request actor |
| `resource_type` | `model_version` |
| `resource_id` | Activated model UUID as text |
| `details` | Structured activation information |

The details object contains:

```json
{
  "model_name": "contention-logistic-v1",
  "artifact_sha256": "e255db6ee5d3a59c075b6249fc7d355e5107dd56b2ed7ddab695bb029db24704",
  "previous_active_model_id": null,
  "already_active": false,
  "reason": "Approved Phase 7 deployment"
}
```

The audit event must not contain API tokens, environment secrets, or serialized
model data.

## Immutable provenance

Activation must not change any of these fields:

- `name`
- `algorithm`
- `feature_schema`
- `target_horizon_sec`
- `training_run_ids`
- `validation_strategy`
- `risk_thresholds`
- `evaluation_metrics`
- `artifact_path`
- `artifact_sha256`
- `code_version`
- `created_at`

Only `is_active` may change during activation.

## Deactivation behaviour

Checkpoint 35 does not provide a public endpoint that leaves the system with
no active model.

Switching models happens by activating another verified model. The transaction
deactivates the old model and activates the new one together.

An emergency deactivation workflow may be introduced later, but it must be an
explicit, audited administrative operation.

## Security requirements

- Activation requires `X-Admin-Token`.
- Token comparison uses constant-time comparison.
- Tokens must never be printed, logged, stored in audit details, or returned.
- Artifact paths must be validated before file access.
- Joblib artifacts are trusted project artifacts and must pass checksum
  verification before deserialization.
- Arbitrary user-supplied artifact paths are not accepted by the activation
  API.
- Database changes must be transactional.
- Client errors must not expose internal exception details.

## Checkpoint 35 completion boundary

Checkpoint 35 is complete when:

- this contract is versioned;
- activation schemas are implemented;
- model repository operations are implemented;
- transactional activation service is implemented;
- administrative model endpoints are implemented;
- artifact and provenance checks pass;
- an activation audit event is stored;
- exactly one model is active;
- repeat activation is idempotent;
- invalid activation attempts preserve the existing active model;
- API and service tests pass;
- the complete project test suite passes;
- `git diff --check` reports no whitespace errors.