"""Tests for src/quant/features/cross_sectional.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.cross_sectional import add_cross_sectional_features

SOURCE_COLS = ("ret_21d", "ret_252d", "vol_21d")


def _dates(n: int, start: str = "2023-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n, tz="UTC")


def _sym_frame(
    dates: pd.DatetimeIndex,
    value: float,
    cols: tuple[str, ...] = SOURCE_COLS,
) -> pd.DataFrame:
    return pd.DataFrame({c: value for c in cols}, index=dates, dtype=float)


def _three_symbol_panel(n: int = 3) -> dict[str, pd.DataFrame]:
    dates = _dates(n)
    return {
        "A": _sym_frame(dates, 0.01),
        "B": _sym_frame(dates, 0.02),
        "C": _sym_frame(dates, 0.03),
    }


class TestRankCorrectness:
    def test_percentile_ranks_hand_computed(self):
        panel = _three_symbol_panel()

        result = add_cross_sectional_features(
            panel, columns=("ret_21d",), min_symbols=3
        )

        for date in panel["A"].index:
            assert result["A"].loc[date, "xs_rank_ret_21d"] == pytest.approx(1 / 3)
            assert result["B"].loc[date, "xs_rank_ret_21d"] == pytest.approx(2 / 3)
            assert result["C"].loc[date, "xs_rank_ret_21d"] == pytest.approx(1.0)

    def test_ranks_bounded_zero_one(self):
        panel = _three_symbol_panel()

        result = add_cross_sectional_features(
            panel, columns=SOURCE_COLS, min_symbols=3
        )

        for sym in panel:
            for col in SOURCE_COLS:
                ranks = result[sym][f"xs_rank_{col}"].dropna()
                assert ((ranks > 0) & (ranks <= 1)).all()

    def test_nan_symbol_excluded_from_rank_pool(self):
        panel = _three_symbol_panel()
        nan_date = panel["B"].index[0]
        panel["B"].loc[nan_date, "ret_21d"] = np.nan

        result = add_cross_sectional_features(
            panel, columns=("ret_21d",), min_symbols=2
        )

        # At nan_date the pool is {A, C}: ranks 1/2 and 2/2.
        assert result["A"].loc[nan_date, "xs_rank_ret_21d"] == pytest.approx(0.5)
        assert result["C"].loc[nan_date, "xs_rank_ret_21d"] == pytest.approx(1.0)
        assert np.isnan(result["B"].loc[nan_date, "xs_rank_ret_21d"])

    def test_min_symbols_rule_nans_thin_dates(self):
        panel = _three_symbol_panel()
        thin_date = panel["B"].index[0]
        panel["B"].loc[thin_date, "ret_21d"] = np.nan

        result = add_cross_sectional_features(
            panel, columns=("ret_21d",), min_symbols=3
        )

        for sym in panel:
            assert np.isnan(result[sym].loc[thin_date, "xs_rank_ret_21d"]), (
                f"{sym}: 2 non-NaN symbols < min_symbols=3 must yield NaN rank"
            )
        full_date = panel["A"].index[1]
        assert result["A"].loc[full_date, "xs_rank_ret_21d"] == pytest.approx(1 / 3)

    def test_union_of_indices_late_entrant(self):
        dates = _dates(4)
        late_dates = dates[2:]
        panel = {
            "A": _sym_frame(dates, 0.01),
            "B": _sym_frame(dates, 0.02),
            "C": _sym_frame(late_dates, 0.03),
        }

        result = add_cross_sectional_features(
            panel, columns=("ret_21d",), min_symbols=2
        )

        # Early dates: pool is {A, B} only.
        assert result["A"].loc[dates[0], "xs_rank_ret_21d"] == pytest.approx(0.5)
        assert result["B"].loc[dates[0], "xs_rank_ret_21d"] == pytest.approx(1.0)
        # Late dates: pool is {A, B, C}.
        assert result["A"].loc[dates[2], "xs_rank_ret_21d"] == pytest.approx(1 / 3)
        assert result["C"].loc[dates[2], "xs_rank_ret_21d"] == pytest.approx(1.0)
        # C's frame keeps its own (shorter) index.
        assert result["C"].index.equals(late_dates)


class TestContract:
    def test_inputs_not_mutated(self):
        panel = _three_symbol_panel()
        snapshots = {sym: df.copy(deep=True) for sym, df in panel.items()}

        result = add_cross_sectional_features(
            panel, columns=("ret_21d",), min_symbols=3
        )

        for sym, df in panel.items():
            pd.testing.assert_frame_equal(df, snapshots[sym])
            assert result[sym] is not df

    def test_output_contains_rank_and_original_columns(self):
        dates = _dates(3)
        panel = {
            sym: _sym_frame(dates, val)
            for sym, val in zip("ABCDE", [0.01, 0.02, 0.03, 0.04, 0.05], strict=True)
        }

        result = add_cross_sectional_features(panel)

        for sym in panel:
            for col in SOURCE_COLS:
                assert col in result[sym].columns
                assert f"xs_rank_{col}" in result[sym].columns
            assert result[sym]["xs_rank_ret_21d"].notna().all()

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            add_cross_sectional_features({})

    def test_missing_source_column_raises_naming_symbols(self):
        dates = _dates(3)
        panel = {
            "A": _sym_frame(dates, 0.01),
            "B": _sym_frame(dates, 0.02, cols=("ret_21d", "ret_252d")),
        }

        with pytest.raises(ValueError, match=r"vol_21d.*\['B'\]"):
            add_cross_sectional_features(panel)
