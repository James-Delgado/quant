"""Tests for src/quant/features/catalog.py + the drift-enforcement test.

The drift test (``TestCatalogDrift``) is the milestone's teeth: it builds a
*maximal* feature matrix (price + FRED + regime + cross-sectional + sentiment)
and asserts the produced column set equals the registered catalog name set.
If either side changes without the other, the test fails naming every
offending column.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from quant.features.catalog import (
    FeatureRecord,
    load_catalog,
    validate_catalog_coverage,
)
from quant.features.cross_sectional import add_cross_sectional_features
from quant.features.engineering import build_features


# --------------------------------------------------------------------- #
# Fixtures (mirror tests/test_features.py helpers)
# --------------------------------------------------------------------- #

def _ohlcv(n: int, seed: int) -> pd.DataFrame:
    """Synthetic OHLCV with ``n`` business days and a deterministic seed."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    dates = pd.bdate_range("2022-01-03", periods=n, tz="UTC")
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": rng.integers(100_000, 1_000_000, n).astype(float),
        },
        index=dates,
    )


def _fred_wide(n: int) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=n, tz="UTC")
    return pd.DataFrame(
        {
            "DGS10": np.linspace(3.5, 4.0, n),
            "DFF":   np.linspace(5.0, 5.25, n),
            "VIXCLS": np.linspace(20.0, 25.0, n),
        },
        index=dates,
    )


def _sentiment_df(symbols: list[str], start: str = "2022-01-03") -> pd.DataFrame:
    rng = np.random.default_rng(123)
    rows: list[dict[str, object]] = []
    dates = pd.bdate_range(start, periods=300, tz="UTC")
    for sym in symbols:
        for date in dates[::5]:  # one doc per ~week per symbol
            rows.append(
                {
                    "symbol": sym,
                    "published_at": date,
                    "sentiment_score": float(rng.uniform(-1, 1)),
                    "document_id": f"{sym}_{date.date()}",
                }
            )
    return pd.DataFrame(rows)


def _maximal_feature_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, pd.DataFrame]:
    """Build the maximal 27-column feature matrix offline.

    Five symbols × 320 business days so 252-bar warmups produce valid values
    and cross-sectional ranks (min_symbols=5) aren't NaN'd wholesale.
    """
    symbols = ["A", "B", "C", "D", "E"]
    prices = {sym: _ohlcv(320, seed=i) for i, sym in enumerate(symbols)}
    fred_wide = _fred_wide(320)
    monkeypatch.setattr(
        "quant.features.engineering._load_fred_wide", lambda con: fred_wide
    )
    features = build_features(symbols, prices, sentiment_df=_sentiment_df(symbols))
    return add_cross_sectional_features(features)


# --------------------------------------------------------------------- #
# Loader behavior
# --------------------------------------------------------------------- #

class TestLoadCatalog:
    def test_default_catalog_loads(self):
        catalog = load_catalog()
        assert isinstance(catalog, dict)
        assert len(catalog) >= 27
        for name, record in catalog.items():
            assert isinstance(record, FeatureRecord)
            assert record.name == name

    def test_records_keyed_by_name(self):
        catalog = load_catalog()
        for name, record in catalog.items():
            assert name == record.name

    def test_duplicate_names_raise(self, tmp_path: Path):
        path = tmp_path / "dup.yaml"
        path.write_text(
            textwrap.dedent(
                """
                features:
                  - name: x
                    family: price
                    source: alpaca_ohlcv
                    formula: f
                    lookback_bars: 0
                    publication_lag_days: 0
                    point_in_time_rule: rule
                    added_phase: "2"
                    glossary_ref: ref
                    ablation_status: untested
                  - name: x
                    family: price
                    source: alpaca_ohlcv
                    formula: f
                    lookback_bars: 0
                    publication_lag_days: 0
                    point_in_time_rule: rule
                    added_phase: "2"
                    glossary_ref: ref
                    ablation_status: untested
                """
            )
        )
        with pytest.raises(ValueError, match=r"duplicate.*\bx\b"):
            load_catalog(path)

    def test_bad_enum_value_raises_validation_error(self, tmp_path: Path):
        path = tmp_path / "bad_enum.yaml"
        path.write_text(
            textwrap.dedent(
                """
                features:
                  - name: x
                    family: bogus
                    source: alpaca_ohlcv
                    formula: f
                    lookback_bars: 0
                    publication_lag_days: 0
                    point_in_time_rule: rule
                    added_phase: "2"
                    glossary_ref: ref
                    ablation_status: untested
                """
            )
        )
        with pytest.raises(ValidationError):
            load_catalog(path)

    def test_dangling_depends_on_raises(self, tmp_path: Path):
        path = tmp_path / "dangling.yaml"
        path.write_text(
            textwrap.dedent(
                """
                features:
                  - name: x
                    family: macro_derived
                    source: derived
                    formula: f
                    lookback_bars: 0
                    publication_lag_days: 0
                    point_in_time_rule: rule
                    added_phase: "2"
                    glossary_ref: ref
                    ablation_status: untested
                    depends_on: [does_not_exist]
                """
            )
        )
        with pytest.raises(ValueError, match=r"x -> does_not_exist"):
            load_catalog(path)

    def test_unknown_top_level_key_raises(self, tmp_path: Path):
        path = tmp_path / "bad_top.yaml"
        path.write_text(
            textwrap.dedent(
                """
                feechers:
                  - name: x
                """
            )
        )
        with pytest.raises(ValueError, match=r"feechers"):
            load_catalog(path)

    def test_unknown_per_feature_key_raises(self, tmp_path: Path):
        path = tmp_path / "extra.yaml"
        path.write_text(
            textwrap.dedent(
                """
                features:
                  - name: x
                    family: price
                    source: alpaca_ohlcv
                    formula: f
                    lookback_bars: 0
                    publication_lag_days: 0
                    point_in_time_rule: rule
                    added_phase: "2"
                    glossary_ref: ref
                    ablation_status: untested
                    surprise_field: nope
                """
            )
        )
        with pytest.raises(ValidationError):
            load_catalog(path)

    def test_missing_required_field_raises(self, tmp_path: Path):
        path = tmp_path / "missing.yaml"
        path.write_text(
            textwrap.dedent(
                """
                features:
                  - name: x
                    family: price
                    source: alpaca_ohlcv
                    formula: f
                    lookback_bars: 0
                    publication_lag_days: 0
                    point_in_time_rule: rule
                    added_phase: "2"
                    glossary_ref: ref
                """
            )
        )
        with pytest.raises(ValidationError):
            load_catalog(path)


# --------------------------------------------------------------------- #
# Coverage validator (the drift API)
# --------------------------------------------------------------------- #

class TestValidateCatalogCoverage:
    def test_matching_set_passes(self):
        catalog = load_catalog()
        validate_catalog_coverage(catalog.keys(), catalog=catalog)

    def test_unregistered_column_reported(self):
        catalog = load_catalog()
        produced = set(catalog.keys()) | {"sneaky_new_feature"}
        with pytest.raises(ValueError, match=r"sneaky_new_feature"):
            validate_catalog_coverage(produced, catalog=catalog)

    def test_phantom_entry_reported(self):
        catalog = load_catalog()
        produced = set(catalog.keys()) - {"ret_1d"}
        with pytest.raises(ValueError, match=r"phantom.*ret_1d"):
            validate_catalog_coverage(produced, catalog=catalog)


# --------------------------------------------------------------------- #
# Drift-enforcement test (the milestone's teeth)
# --------------------------------------------------------------------- #

class TestCatalogDrift:
    def test_maximal_matrix_matches_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        features_by_symbol = _maximal_feature_matrix(monkeypatch)
        produced_per_symbol = {
            sym: set(df.columns) for sym, df in features_by_symbol.items()
        }
        # Every symbol must produce the same column set.
        sample = next(iter(produced_per_symbol.values()))
        for sym, cols in produced_per_symbol.items():
            assert cols == sample, f"{sym} produced a different column set: {cols ^ sample}"

        catalog = load_catalog()
        produced = sample
        registered = set(catalog.keys())
        unregistered = sorted(produced - registered)
        phantom = sorted(registered - produced)
        assert not unregistered and not phantom, (
            f"feature catalog drift: unregistered={unregistered}, phantom={phantom}"
        )

    def test_drift_test_fails_when_catalog_misses_a_column(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Negative path: a partial catalog must be rejected naming the gap."""
        features_by_symbol = _maximal_feature_matrix(monkeypatch)
        produced = set(next(iter(features_by_symbol.values())).columns)

        # Write a temp catalog with ret_1d intentionally removed.
        full = load_catalog().values()
        partial_yaml = "features:\n"
        for record in full:
            if record.name == "ret_1d":
                continue
            partial_yaml += textwrap.indent(
                "- " + record.model_dump_json(exclude_none=False) + "\n",
                "  ",
            )
        # Re-emit a parseable YAML by piping through json (json is valid YAML).
        partial_path = tmp_path / "partial.yaml"
        partial_path.write_text(partial_yaml)
        partial = load_catalog(partial_path)

        with pytest.raises(ValueError, match=r"unregistered.*ret_1d"):
            validate_catalog_coverage(produced, catalog=partial)


# --------------------------------------------------------------------- #
# Glossary anchor check
# --------------------------------------------------------------------- #

class TestGlossaryAnchors:
    GLOSSARY_PATH = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "concepts"
        / "feature-glossary.md"
    )

    @classmethod
    def _heading_anchors(cls) -> set[str]:
        text = cls.GLOSSARY_PATH.read_text()
        return {
            m.group(1)
            for m in re.finditer(r"^### (\w+)", text, flags=re.MULTILINE)
        }

    def test_every_catalog_ref_resolves_to_a_glossary_heading(self):
        anchors = self._heading_anchors()
        missing: list[str] = []
        for record in load_catalog().values():
            fragment = record.glossary_ref.split("#", 1)[-1]
            if fragment not in anchors:
                missing.append(f"{record.name} -> #{fragment}")
        assert not missing, (
            f"catalog glossary_ref anchors not found in feature-glossary.md: {missing}"
        )
