# Phase 8A Model Promotion Decision

## Decision

The Phase 8A random-forest candidate will not replace the currently active
`contention-logistic-v1` model.

The candidate substantially improved recognition of non-contention conditions,
but its loss of contention recall is too large to justify activation.

## Evaluation Protocol

The comparison used the frozen Phase 8A test split:

- Test experiments: 3
- Test rows: 133
- Negative labels: 23
- Positive labels: 110
- Candidate model: `random_forest`
- Candidate threshold: `0.43`
- Threshold-selection source: validation split only
- Test split used for training or threshold selection: no

The candidate test evaluation was performed once and recorded in:

```text
docs/model_training/final_test_evaluation_phase_8a_v2.json
```

No threshold was changed after inspecting the test results.

## Same-Test Comparison

| Metric | Active model | Phase 8A candidate |
|---|---:|---:|
| Accuracy | 83.46% | 78.20% |
| Balanced accuracy | 52.17% | 78.22% |
| Precision | 83.33% | 94.51% |
| Recall | 100.00% | 78.18% |
| F1-score | 90.91% | 85.57% |
| F2-score | 96.15% | 80.98% |
| Specificity | 4.35% | 78.26% |

## Confusion Matrices

| Outcome | Active model | Phase 8A candidate |
|---|---:|---:|
| True negative | 1 | 18 |
| False positive | 22 | 5 |
| False negative | 0 | 24 |
| True positive | 110 | 86 |

## Interpretation

The active model detected every positive contention case in the test split, but
it classified 22 of the 23 negative cases as contention. It therefore behaves
like an overly sensitive warning system and has poor ability to recognize safe
conditions.

The Phase 8A candidate reduced false-positive warnings from 22 to 5 and
increased true negatives from 1 to 18. Its balanced accuracy improved from
52.17% to 78.22%.

However, the candidate missed 24 of the 110 positive contention cases. Recall
fell from 100% to 78.18%, and F2-score fell from 96.15% to 80.98%. This is an
unacceptable recall regression for promotion without an explicitly approved
trade-off policy.

## Generalization Finding

Validation recall at the selected threshold was 99.07%, while frozen-test
recall was 78.18%. This gap indicates that the candidate did not generalize
consistently across held-out experiment scenarios.

The test set will not be used to retune the threshold. Doing so would leak test
information into model selection and invalidate the final evaluation.

## Data and Model Provenance

The Phase 8A evaluation used the following frozen evidence:

- Completed controlled experiments: 15
- Collected metric samples and predictions: 900
- Verified `live-label-v2` rows: 662
- Training experiments: 9
- Validation experiments: 3
- Test experiments: 3
- Training rows: 396
- Validation rows: 133
- Test rows: 133
- Feature count: 18
- Split assignment SHA-256:
  `2c7c02ca4019252fc62c09b74d8f5366d741c7f9c0d53c128a4087a25e207ebe`
- Candidate model SHA-256:
  `0d11f108dd40c421ef05ea2e4f434530a97fa45ace7cbb2f8b94a70d4f0032c8`

The model features excluded future evidence, persisted model probability, risk
level, and threshold values. This prevents target leakage and model-output
leakage.

## Promotion Outcome

- Candidate activation: rejected
- Existing active model: retained
- Candidate artifact: preserved for reproducibility
- Candidate threshold: frozen at `0.43`
- Phase 8A dataset and splits: preserved
- Final test evaluation: completed once
- Post-test threshold tuning: prohibited
- Production-performance claim: not made

## Recommended Follow-Up

Future work should collect additional independently seeded experiments,
especially LOW and borderline MEDIUM demand scenarios.

Candidate development should use stronger experiment-level validation, such as
grouped cross-validation. A formal promotion policy should also define minimum
acceptable recall, specificity, and balanced accuracy before model selection.

A future candidate should be evaluated on a new untouched test set. The current
Phase 8A test split must not be reused for model or threshold selection.

## Demonstration Readiness

The current system remains suitable for a controlled working demonstration of:

1. simulated ticket-booking traffic;
2. PostgreSQL lock-contention generation;
3. real-time database telemetry collection;
4. real-time contention-risk prediction;
5. future-window label assignment;
6. versioned and verified dataset export;
7. experiment-grouped training, validation, and test splitting;
8. deterministic baseline model training;
9. validation-only model and threshold selection;
10. one-time held-out test evaluation; and
11. evidence-based rejection of an unsafe candidate.

The existing active model remains available for the live predictive
demonstration. The Phase 8A candidate is retained as research evidence showing
the trade-off between reducing false alarms and preserving contention recall.

## Final Conclusion

Phase 8A successfully identified a major limitation in the existing active
model and tested a candidate designed to improve discrimination.

The random-forest candidate recognized non-contention cases much more
effectively than the active model. Nevertheless, its held-out recall regression
made it unsuitable for activation.

Rejecting the candidate is the technically defensible outcome. It demonstrates
that the project uses frozen evidence and explicit evaluation safeguards rather
than promoting a model solely because selected headline metrics improved.

No production-performance claim is made from this controlled experimental
evaluation.