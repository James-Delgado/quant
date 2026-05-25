"""ARIMA(1,0,0) baseline model.

Fixed order (not auto_arima) for two reasons:
  1. Bounds per-fold fit time — auto_arima runs multiple candidate fits.
  2. Keeps the Deflated Sharpe Ratio's N parameter honest: if the ARIMA order
     is selected per-fold it becomes an implicit hyperparameter that inflates N.

Order is (1,0,0) — AR(1) on the returns series without differencing. d=0
because the label series is already stationary forward returns (I(0)). Using
d=1 would over-difference a stationary series and produce forecasts that
converge to a flat constant, carrying no return-predicting signal.

Single-fit-per-fold protocol
-----------------------------
ARIMABaseline.fit() estimates the model on the training label series.
ARIMABaseline.predict() returns sequential 1-step-ahead forecasts for the
entire test window from the already-fitted model — no re-fitting per bar.

When used as a feature column in GBM:
  - Fit ARIMA once per fold on the training window.
  - At predict time, call predict() or predict_one_step() for each test bar.
  - Pass the forecast as "arima_forecast" in the GBM feature matrix.
  - Do NOT re-fit between test bars — that would use test-period data.

Note on predict_one_step(): always returns the 1-step forecast from the
training window end — does not update state with realized test-bar returns.
"""
from __future__ import annotations

import numpy as np
from statsmodels.tsa.arima.model import ARIMA


class ARIMABaseline:
    """AR(1) baseline fitted on the forward-return label series once per fold.

    Parameters
    ----------
    order:  ARIMA (p, d, q) order. Default (1, 0, 0) — AR(1) on stationary
            returns. Do not use d=1; the label series is already I(0).
    """

    def __init__(self, order: tuple[int, int, int] = (1, 0, 0)) -> None:
        self.order = order
        self._fitted: object | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "ARIMABaseline":
        """Fit ARIMA on the training label series y.

        X is accepted for interface compatibility but not used.
        """
        if len(y) < sum(self.order) + 2:
            raise ValueError(
                f"Training series too short for ARIMA{self.order}: "
                f"need > {sum(self.order) + 1} observations, got {len(y)}"
            )
        model = ARIMA(y, order=self.order)
        self._fitted = model.fit()
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return sequential 1-step-ahead forecasts for the test window.

        Generates len(X) forecasts from the fitted model. No re-fitting
        is performed — this is the correct single-fit-per-fold protocol.
        """
        if self._fitted is None:
            raise RuntimeError("ARIMABaseline.fit() must be called before predict()")

        n_steps = len(X)
        if n_steps == 0:
            return np.array([], dtype=float)

        forecast = self._fitted.forecast(steps=n_steps)  # type: ignore[union-attr]
        return np.asarray(forecast, dtype=float)

    def predict_one_step(self) -> float:
        """Single next-step forecast from the already-fitted model.

        Use this when inserting the ARIMA forecast as a feature column
        in GBM: call once per test bar using the model fitted on the
        fold's training window. Do NOT call fit() between bars.
        """
        if self._fitted is None:
            raise RuntimeError("ARIMABaseline.fit() must be called before predict_one_step()")
        result = self._fitted.forecast(steps=1)  # type: ignore[union-attr]
        return float(result.iloc[0] if hasattr(result, "iloc") else result[0])
