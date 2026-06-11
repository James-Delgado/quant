"""Regime detection for per-regime evaluation of model performance.

A regime is a contiguous span of time during which the data-generating
process has stable statistical properties. Per-regime evaluation lets the
researcher tell whether a model has edge *in some regime* even when its
aggregate-OOS Sharpe (across regimes) is uninformative.

Two detectors are provided on **orthogonal axes**:

* `VIXThresholdDetector` — volatility axis (`low_vol`, `mid_vol`, `high_vol`).
  Labels each date by the contemporaneous VIX close.
* `DateRangeDetector` — macro-era axis (`pre_qe`, `qe_bull`, `covid`,
  `rate_cycle`). Labels each date by its calendar position.

The two axes are independent — a date can simultaneously be `high_vol` and
`covid`. Per the Phase 4A PRD, the success-metric gate runs on the era axis
(`qe_bull`/`covid`/`rate_cycle`); the volatility axis is provided as a
complementary diagnostic.

Both detectors satisfy a `RegimeDetector` protocol — any object with a
`label(dates) -> pd.Series[str]` method works, so an HMM-based regime
detector can be added later without changing the consumer API.

Hard invariant — point-in-time
------------------------------
Both detectors are point-in-time: labelling a date `D` must use only
information available as of `D`. `VIXThresholdDetector` enforces this by
indexing `vix_series.loc[D]` (raises on missing keys, no forward fill).
`DateRangeDetector` enforces this trivially — its date ranges are fixed
constants independent of any series being labeled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class RegimeDetector(Protocol):
    """Anything with `label(dates) -> pd.Series[str]` is a regime detector."""

    def label(self, dates: pd.DatetimeIndex) -> pd.Series:
        ...


# ─── VIXThresholdDetector — volatility axis ──────────────────────────────────


@dataclass(frozen=True)
class VIXThresholdDetector:
    """Map each date to a volatility regime by the VIX close on that date.

    Thresholds default to ``low=15`` and ``high=25``, anchored to the
    long-run VIX distribution: the ~25th and ~75th percentiles of daily
    VIX closes since 1990 are approximately 15 and 25 respectively, so
    these bounds carve the distribution into roughly thirds — useful for
    per-regime statistics with comparable sample sizes.

    Bins (boundaries inclusive on the outside):

    * ``vix <= low``   → ``"low_vol"``
    * ``vix >= high``  → ``"high_vol"``
    * otherwise        → ``"mid_vol"``

    Citation: VIX historical distribution from CBOE
    (https://www.cboe.com/tradable_products/vix/), summarised in the
    long-run-distribution section of ``docs/concepts/regime-evaluation.md``.
    """

    vix_series: pd.Series
    low: float = 15.0
    high: float = 25.0

    def __post_init__(self) -> None:
        if self.low >= self.high:
            raise ValueError(
                f"VIXThresholdDetector requires low ({self.low}) < high ({self.high})"
            )

    def label(self, dates: pd.DatetimeIndex) -> pd.Series:
        idx = pd.DatetimeIndex(dates)
        missing = idx.difference(self.vix_series.index)
        if len(missing) > 0:
            raise ValueError(
                f"missing VIX values for {len(missing)} requested dates "
                f"(first: {missing[0]!s}); align VIX series with the OOS "
                "calendar or filter out the missing dates before calling .label()"
            )
        values = self.vix_series.loc[idx]
        labels = pd.Series("mid_vol", index=values.index, dtype=object)
        labels[values <= self.low] = "low_vol"
        labels[values >= self.high] = "high_vol"
        return labels


# ─── DateRangeDetector — macro-era axis ──────────────────────────────────────


# Default macro-era ranges referenced by the Phase 4A PRD success metric.
# Ranges are inclusive of both endpoints (a date equal to either bound is
# inside the range). Adjacent ranges must not overlap; any date outside all
# ranges receives the detector's ``default_label`` (``"pre_qe"`` by default).
_DEFAULT_RANGES: tuple[tuple[str, str, str], ...] = (
    ("qe_bull", "2010-01-01", "2019-12-31"),
    ("covid", "2020-01-01", "2021-12-31"),
    ("rate_cycle", "2022-01-01", "2099-12-31"),
)


@dataclass(frozen=True)
class DateRangeDetector:
    """Map each date to a macro-era regime by calendar position.

    Default eras follow the Phase 4A PRD success-metric definition:
    ``qe_bull`` (2010-01-01 → 2019-12-31), ``covid`` (2020-01-01 →
    2021-12-31), ``rate_cycle`` (2022-01-01 → present). Anything earlier
    receives ``default_label`` (``"pre_qe"``).

    Ranges are validated at construction time to be non-overlapping. Adjacent
    ranges must abut (start of one immediately after end of previous) or
    leave a gap that falls through to ``default_label``.
    """

    ranges: tuple[tuple[str, str, str], ...] = field(default=_DEFAULT_RANGES)
    default_label: str = "pre_qe"

    def __post_init__(self) -> None:
        parsed: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
        for name, start, end in self.ranges:
            s, e = pd.Timestamp(start), pd.Timestamp(end)
            if s > e:
                raise ValueError(
                    f"DateRangeDetector range {name!r} has start ({start}) > end ({end})"
                )
            parsed.append((name, s, e))

        parsed.sort(key=lambda r: r[1])
        for prev, curr in zip(parsed[:-1], parsed[1:], strict=False):
            if curr[1] <= prev[2]:
                raise ValueError(
                    f"DateRangeDetector ranges overlap: {prev[0]!r} "
                    f"({prev[1].date()}–{prev[2].date()}) and {curr[0]!r} "
                    f"({curr[1].date()}–{curr[2].date()})"
                )

        # Stash parsed for fast lookup; bypass frozen=True via object.__setattr__.
        object.__setattr__(self, "_parsed_ranges", tuple(parsed))

    def label(self, dates: pd.DatetimeIndex) -> pd.Series:
        idx = pd.DatetimeIndex(dates)
        labels = pd.Series(self.default_label, index=idx, dtype=object)
        parsed = self._parsed_ranges  # type: ignore[attr-defined]
        for name, start, end in parsed:
            mask = (idx >= start) & (idx <= end)
            labels[mask] = name
        return labels


# ─── Convenience function ────────────────────────────────────────────────────


def tag_regimes(dates: pd.DatetimeIndex, detector: RegimeDetector) -> pd.Series:
    """Apply ``detector`` to ``dates`` and return the resulting label Series.

    Thin wrapper around ``detector.label(dates)`` provided so notebooks and
    pipelines can use a single import (``tag_regimes``) regardless of which
    detector they wire in.
    """
    return detector.label(dates)
