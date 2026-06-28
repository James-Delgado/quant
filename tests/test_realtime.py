"""Tests for the C1-M2 same-day point-in-time reader and its gates.

Covers the two merge-blocking gates from the C1 PRD:
  * G1 (no look-ahead): ``get_pit_bar`` never returns a bar stamped after the
    as-of instant, across a property-based sweep including intraday and
    weekend/holiday as-of instants.
  * G2 (train/serve parity): features built from an ``asof``-truncated history
    equal the full-history batch features for the retained dates, to within the
    pinned ``rtol`` with zero material mismatches, over >= 250 (symbol, date)
    pairs spanning >= 2 regimes.

Plus the gate-function unit behaviour (both pass and fail directions) and the
``build_features(asof=...)`` integration (truncation + default equivalence).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from quant.features.engineering import build_features
from quant.storage import lake
from quant.storage.realtime import (
    PARITY_MIN_PAIRS,
    PARITY_RTOL,
    ParityGateResult,
    PitGateResult,
    get_pit_bar,
    get_pit_panel,
    parity_gate_report,
    pit_gate_report,
)
from quant.utils.calendar import last_trading_day


# ─── Lake fixtures ─────────────────────────────────────────────────────────────


def _write_tiingo(frame: pd.DataFrame) -> None:
    """Write a synthetic equity_eod_tiingo processed dataset to the temp lake."""
    df = frame.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["ingested_at"] = pd.Timestamp("2026-01-01", tz="UTC")
    df["year"] = df["timestamp"].dt.year.astype("int64")
    df["month"] = df["timestamp"].dt.month.astype("int64")
    lake.write_processed(df, dataset="equity_eod_tiingo", partition_cols=["year", "month"])


def _tiingo_rows(symbol: str, dates: pd.DatetimeIndex, base: float) -> pd.DataFrame:
    """OHLCV rows in the tiingo processed shape (adjClose present)."""
    n = len(dates)
    close = base + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": dates,
            "open": close - 1.0,
            "high": close + 1.0,
            "low": close - 2.0,
            "close": close,
            "adjClose": close,
            "volume": 1_000_000.0,
        }
    )


@pytest.fixture()
def three_session_lake(lake_root):
    """Three real sessions around a weekend: Thu, Fri, then the next Mon."""
    dates = pd.DatetimeIndex(
        [
            pd.Timestamp("2020-03-12", tz="UTC"),  # Thursday
            pd.Timestamp("2020-03-13", tz="UTC"),  # Friday
            pd.Timestamp("2020-03-16", tz="UTC"),  # Monday
        ]
    )
    _write_tiingo(_tiingo_rows("AAPL", dates, base=100.0))
    return dates


# ─── get_pit_bar: G1 look-ahead guard + weekend/holiday behaviour ──────────────


def test_get_pit_bar_returns_latest_on_or_before_asof(three_session_lake):
    bar = get_pit_bar("AAPL", pd.Timestamp("2020-03-13 12:00", tz="UTC"))
    assert bar is not None
    assert bar.name == pd.Timestamp("2020-03-13", tz="UTC")
    assert list(bar.index) == ["open", "high", "low", "close", "volume"]


def test_get_pit_bar_never_returns_future_bar_intraday_boundary(three_session_lake):
    # asof is the evening BEFORE Friday's 00:00 UTC stamp — must return Thursday,
    # not leak the 2020-03-13 00:00 UTC bar.
    bar = get_pit_bar("AAPL", pd.Timestamp("2020-03-12 23:00", tz="UTC"))
    assert bar is not None
    assert bar.name == pd.Timestamp("2020-03-12", tz="UTC")


def test_get_pit_bar_weekend_returns_prior_session(three_session_lake):
    saturday = pd.Timestamp("2020-03-14 12:00", tz="UTC")
    bar = get_pit_bar("AAPL", saturday)
    assert bar is not None
    # The reader returns Friday's bar; the trading calendar agrees Friday is the
    # last session on or before Saturday.
    assert bar.name.date() == dt.date(2020, 3, 13)
    assert last_trading_day(saturday.date()) == dt.date(2020, 3, 13)


def test_get_pit_bar_returns_none_before_history(three_session_lake):
    assert get_pit_bar("AAPL", pd.Timestamp("2019-01-01", tz="UTC")) is None


def test_get_pit_bar_absent_symbol_returns_none(three_session_lake):
    assert get_pit_bar("ZZZZ", pd.Timestamp("2020-03-16", tz="UTC")) is None


def test_get_pit_bar_naive_asof_treated_as_utc(three_session_lake):
    naive = get_pit_bar("AAPL", "2020-03-13 12:00")
    aware = get_pit_bar("AAPL", pd.Timestamp("2020-03-13 12:00", tz="UTC"))
    assert naive is not None and aware is not None
    assert naive.name == aware.name


# ─── get_pit_panel ─────────────────────────────────────────────────────────────


def test_get_pit_panel_assembles_history_up_to_asof(three_session_lake):
    panel = get_pit_panel(["AAPL"], pd.Timestamp("2020-03-13 12:00", tz="UTC"))
    assert set(panel) == {"AAPL"}
    px = panel["AAPL"]
    assert list(px.columns) == ["open", "high", "low", "close", "volume"]
    assert px.index.max() == pd.Timestamp("2020-03-13", tz="UTC")
    assert len(px) == 2  # Thursday + Friday, Monday excluded


def test_get_pit_panel_omits_absent_symbols(three_session_lake):
    panel = get_pit_panel(["AAPL", "ZZZZ"], pd.Timestamp("2020-03-16", tz="UTC"))
    assert set(panel) == {"AAPL"}


def test_get_pit_bar_missing_dataset_returns_none(lake_root):
    # Empty temp lake — the dataset was never written; the reader degrades to
    # "no bar" rather than raising.
    assert get_pit_bar("AAPL", pd.Timestamp("2020-03-16", tz="UTC")) is None


def test_get_pit_panel_missing_dataset_returns_empty(lake_root):
    assert get_pit_panel(["AAPL"], pd.Timestamp("2020-03-16", tz="UTC")) == {}


def test_get_pit_panel_close_is_adjclose(lake_root):
    dates = pd.DatetimeIndex([pd.Timestamp("2021-06-01", tz="UTC")])
    rows = _tiingo_rows("MSFT", dates, base=200.0)
    rows["adjClose"] = 150.0  # adjusted differs from raw close
    rows["close"] = 200.0
    _write_tiingo(rows)
    panel = get_pit_panel(["MSFT"], pd.Timestamp("2021-06-02", tz="UTC"))
    assert panel["MSFT"]["close"].iloc[0] == 150.0


# ─── G1 property-based sweep ───────────────────────────────────────────────────


def test_g1_property_sweep_zero_future_bars(lake_root):
    """Sweep many as-of instants (intraday, weekend) across symbols; the gate
    must report exactly 0 future bars."""
    sessions = pd.bdate_range("2020-01-02", periods=120, tz="UTC")
    for sym, base in (("AAPL", 100.0), ("MSFT", 200.0)):
        _write_tiingo(_tiingo_rows(sym, sessions, base=base))

    # As-of instants: a daily grid crossed with intraday/weekend offsets, so the
    # sweep straddles the 00:00 UTC bar-stamp boundary (-2h just before a stamp,
    # +6h just after) and lands on weekends. ~360 checks is ample to catch an
    # off-by-one without thousands of DuckDB queries.
    offsets = [pd.Timedelta(h, "h") for h in (-2, 6)]
    rng = pd.date_range("2020-01-01", "2020-04-01", freq="1D", tz="UTC")
    checks: list[tuple[pd.Timestamp, pd.Timestamp | None]] = []
    for sym in ("AAPL", "MSFT"):
        for base_asof in rng:
            for off in offsets:
                asof = base_asof + off
                bar = get_pit_bar(sym, asof)
                checks.append((asof, None if bar is None else bar.name))

    result = pit_gate_report(checks)
    assert isinstance(result, PitGateResult)
    assert result.n_future_bars == 0
    assert result.passed
    assert result.n_checked == len(checks)


# ─── pit_gate_report unit behaviour ────────────────────────────────────────────


def test_pit_gate_flags_future_bar():
    asof = pd.Timestamp("2020-01-10", tz="UTC")
    future = pd.Timestamp("2020-01-11", tz="UTC")
    result = pit_gate_report([(asof, future)])
    assert result.n_future_bars == 1
    assert not result.passed


def test_pit_gate_none_is_not_a_violation():
    asof = pd.Timestamp("2020-01-10", tz="UTC")
    result = pit_gate_report([(asof, None), (asof, asof)])
    assert result.n_future_bars == 0
    assert result.passed


# ─── build_features(asof=...) integration ──────────────────────────────────────


def _ohlcv(n: int, *, tz: str | None = "UTC", start: str = "2018-01-02") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="B", tz=tz)
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
        },
        index=idx,
    )


def test_build_features_asof_none_is_full_history(lake_root):
    prices = {"AAPL": _ohlcv(300)}
    full = build_features(["AAPL"], prices)["AAPL"]
    with_none = build_features(["AAPL"], prices, asof=None)["AAPL"]
    pd.testing.assert_frame_equal(full, with_none)


def test_build_features_asof_truncates_to_retained_rows(lake_root):
    prices = {"AAPL": _ohlcv(300)}
    cutoff = prices["AAPL"].index[200]
    truncated = build_features(["AAPL"], prices, asof=cutoff)["AAPL"]
    assert truncated.index.max() <= cutoff
    assert len(truncated) == 201


def test_build_features_asof_row_equals_batch_row(lake_root):
    """The structural G2 guarantee at the single-row level."""
    prices = {"AAPL": _ohlcv(300)}
    cutoff = prices["AAPL"].index[250]
    batch = build_features(["AAPL"], prices)["AAPL"].loc[cutoff]
    live = build_features(["AAPL"], prices, asof=cutoff)["AAPL"].iloc[-1]
    pd.testing.assert_series_equal(batch, live, check_names=False)


# ─── G2 structural parity over >= 250 pairs x >= 2 regimes ─────────────────────


def test_g2_structural_parity_gate_passes(lake_root):
    """Build features live (asof-truncated) vs batch (full history) for many
    (symbol, date) pairs across two date regimes; the parity gate must pass."""
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    prices = {s: _ohlcv(600, start="2015-01-02") for s in symbols}
    batch_feats = build_features(symbols, prices)

    # Two disjoint "regimes": an early span and a late span.
    idx = prices["AAA"].index
    regime_early = idx[260:286]   # 26 dates
    regime_late = idx[560:586]    # 26 dates
    sampled = list(regime_early) + list(regime_late)

    batch_rows: dict[str, pd.Series] = {}
    live_rows: dict[str, pd.Series] = {}
    # One asof-truncated build per date covers all symbols at once (each
    # symbol's last retained row is its row for `date`), keeping the call count
    # to len(sampled) rather than len(symbols) * len(sampled).
    for date in sampled:
        live_feats = build_features(symbols, prices, asof=date)
        for sym in symbols:
            key = f"{sym}|{date.isoformat()}"
            batch_rows[key] = batch_feats[sym].loc[date]
            live_rows[key] = live_feats[sym].iloc[-1]

    batch_df = pd.DataFrame(batch_rows).T
    live_df = pd.DataFrame(live_rows).T
    assert len(batch_df) >= PARITY_MIN_PAIRS  # 5 x 52 = 260

    result = parity_gate_report(batch_df, live_df, n_regimes=2)
    assert isinstance(result, ParityGateResult)
    assert result.n_pairs >= PARITY_MIN_PAIRS
    assert result.n_mismatches == 0
    assert result.max_rel_diff <= PARITY_RTOL
    assert result.passed


# ─── parity_gate_report unit behaviour ─────────────────────────────────────────


def _parity_frame(n: int = PARITY_MIN_PAIRS) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        rng.normal(size=(n, 4)),
        columns=["a", "b", "c", "d"],
        index=[f"row{i}" for i in range(n)],
    )


def test_parity_gate_passes_on_identical():
    frame = _parity_frame()
    result = parity_gate_report(frame, frame.copy(), n_regimes=2)
    assert result.n_mismatches == 0
    assert result.passed


def test_parity_gate_fails_on_material_divergence():
    batch = _parity_frame()
    live = batch.copy()
    live.iloc[0, 0] += 1.0  # one materially divergent cell
    result = parity_gate_report(batch, live, n_regimes=2)
    assert result.n_mismatches == 1
    assert not result.passed


def test_parity_gate_nan_warmup_compares_equal():
    batch = _parity_frame()
    live = batch.copy()
    batch.iloc[0, 0] = np.nan
    live.iloc[0, 0] = np.nan
    result = parity_gate_report(batch, live, n_regimes=2)
    assert result.n_mismatches == 0
    assert result.passed


def test_parity_gate_requires_min_pairs():
    frame = _parity_frame(n=PARITY_MIN_PAIRS - 1)
    result = parity_gate_report(frame, frame.copy(), n_regimes=2)
    assert result.n_mismatches == 0
    assert not result.passed  # too few pairs


def test_parity_gate_requires_min_regimes():
    frame = _parity_frame()
    result = parity_gate_report(frame, frame.copy(), n_regimes=1)
    assert not result.passed  # only one regime


def test_parity_gate_within_rtol_not_a_mismatch():
    batch = _parity_frame()
    live = batch.copy()
    # Perturb by less than rtol relative to the value — must NOT count as a
    # mismatch.
    live.iloc[0, 0] = batch.iloc[0, 0] * (1.0 + PARITY_RTOL / 10.0)
    result = parity_gate_report(batch, live, n_regimes=2)
    assert result.n_mismatches == 0
    assert result.passed
