"""Buy-and-hold SPY baseline.

Always predicts +1 (long) regardless of features or labels.
This is the practically relevant benchmark: a model that cannot beat
buy-and-hold SPY on a risk-adjusted, cost-net basis destroys value
relative to the cheapest available alternative.

Satisfies the same duck-typed interface as all other baselines:
  .fit(X, y) — no-op
  .predict(X) — returns np.ones(len(X))

sign() applied by the harness produces +1 for every bar.
"""
from __future__ import annotations

import numpy as np


class BuyAndHoldBaseline:
    """Always-long model; zero parameters, no fitting required."""

    def fit(self, X: np.ndarray, y: np.ndarray) -> "BuyAndHoldBaseline":
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.ones(len(X), dtype=float)
