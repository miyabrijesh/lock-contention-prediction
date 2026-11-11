"""Portable scoring for the original frozen logistic model."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_demo():
    return json.loads((ROOT / "demo_data.json").read_text())


def score(features, model):
    order = model["feature_order"]
    logit = model["intercept"] + math.fsum(
        ((float(features[name]) - mean) / scale) * weight
        for name, mean, scale, weight in zip(
            order, model["mean"], model["scale"], model["weights"], strict=True
        )
    )
    if logit >= 0:
        return 1 / (1 + math.exp(-logit))
    exp_logit = math.exp(logit)
    return exp_logit / (1 + exp_logit)
