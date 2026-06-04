"""Gradient-boosted model (XGBoost) for forward-return regression.

Design notes
------------
- Continuous output: predict() returns floats. run_portfolio_backtest() applies
  sign() internally. Do NOT use evaluate_panel() — that function calls
  run_backtest() which expects discrete {-1, 0, +1} signals.

- Hyperparameter tuning is done inside the walk-forward training window via
  RandomizedSearchCV + TimeSeriesSplit(n_splits). The test fold never touches
  the search. N=50 is both the computational budget and the DSR N parameter.

- Sample uniqueness weighting (López de Prado): overlapping forward-return
  labels (horizon > 1) mean adjacent samples share future bars. Weights equal
  the mean uniqueness of each sample's label window, normalized to mean=1.
  Passed to XGBRegressor via sample_weight in fit().

- macOS / M2 constraint: XGBRegressor uses n_jobs=1 to avoid nested
  multiprocessing deadlock. RandomizedSearchCV uses n_jobs=-1 (outer parallelism
  over CV folds is safe).
"""
from __future__ import annotations

import numpy as np
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from xgboost import XGBRegressor

from quant.features.weights import compute_sample_weights

_PARAM_DIST: dict[str, list] = {
    "max_depth": [3, 4, 5, 6],
    "learning_rate": [0.01, 0.05, 0.1, 0.2],
    "n_estimators": [50, 100, 200],
    "subsample": [0.6, 0.8, 1.0],
    "colsample_bytree": [0.6, 0.8, 1.0],
    "reg_alpha": [0, 0.1, 1.0],
    "reg_lambda": [1.0, 2.0, 5.0],
}


class GBMModel:
    """XGBoost gradient-boosted regressor with walk-forward hyperparameter tuning.

    Parameters
    ----------
    label_horizon : int
        Forward-return horizon in bars (from LabelResult.horizon_bars).
        Used to compute sample uniqueness weights.
    n_iter : int
        Number of hyperparameter configurations to try (RandomizedSearchCV).
        Hard cap 50 — do not raise without recalculating the DSR threshold.
    n_splits : int
        Inner TimeSeriesSplit folds for CV scoring.
    random_state : int
        Seed for reproducibility.
    """

    def __init__(
        self,
        label_horizon: int = 5,
        n_iter: int = 50,
        n_splits: int = 3,
        random_state: int = 0,
    ) -> None:
        self.label_horizon = label_horizon
        self.n_iter = n_iter
        self.n_splits = n_splits
        self.random_state = random_state
        self._model: XGBRegressor | None = None
        self._feature_importances: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "GBMModel":
        """Fit XGBoost on the training window with inner TimeSeriesSplit CV.

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_features)
        y : np.ndarray, shape (n_samples,) — forward returns (continuous)

        Returns
        -------
        self
        """
        min_samples = self.n_splits * 5
        if len(y) < min_samples:
            raise ValueError(
                f"Training window too small: need >= {min_samples} samples "
                f"(n_splits={self.n_splits} × 5), got {len(y)}"
            )

        weights = compute_sample_weights(len(y), self.label_horizon)

        base = XGBRegressor(
            n_jobs=1,
            verbosity=0,
            random_state=self.random_state,
        )
        cv = TimeSeriesSplit(n_splits=self.n_splits)
        search = RandomizedSearchCV(
            base,
            param_distributions=_PARAM_DIST,
            n_iter=self.n_iter,
            cv=cv,
            scoring="neg_mean_squared_error",
            n_jobs=-1,
            random_state=self.random_state,
        )
        search.fit(X, y, sample_weight=weights)
        self._model = search.best_estimator_
        self._feature_importances = self._model.feature_importances_
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return continuous forward-return forecasts for each row of X.

        Returns floats. sign() is applied by run_portfolio_backtest() — do NOT
        discretize here.
        """
        if self._model is None:
            raise RuntimeError("GBMModel.fit() must be called before predict()")
        return self._model.predict(X).astype(float)

    @property
    def feature_importances_(self) -> np.ndarray:
        """Feature importances from the best estimator after fitting."""
        if self._feature_importances is None:
            raise RuntimeError("GBMModel.fit() must be called before feature_importances_")
        return self._feature_importances
