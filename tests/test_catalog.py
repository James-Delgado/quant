"""Tests for src/quant/features/catalog.py + the drift-enforcement test.

The drift test (``TestCatalogDrift``) is the milestone's teeth: it builds a
*maximal* feature matrix (price + FRED + regime + cross-sectional + sentiment)
and asserts the produced column set equals the registered catalog name set.
If either side changes without the other, the test fails naming every
offending column.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import textwrap
import typing
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from quant.backtest.attribution import ATTRIBUTION_STATUS_VALUES
from quant.features.catalog import (
    FeatureRecord,
    load_catalog,
    validate_catalog_coverage,
)
from quant.features.cross_sectional import add_cross_sectional_features
from quant.features.engineering import build_features


def _load_script(name: str, filename: str) -> Any:
    """Load a ``scripts/<filename>`` module by path (scripts/ is not a package)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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


# --------------------------------------------------------------------- #
# attribution_status field (Project B2-M3 — METHODOLOGY §6 drift contract)
# --------------------------------------------------------------------- #

class TestAttributionStatusSchema:
    """The enum constant, the schema default, and back-compat all stay in lock-step."""

    def test_enum_constant_matches_schema_literal(self):
        # ATTRIBUTION_STATUS_VALUES (attribution.py) is the single source of truth
        # the population + drift test consume; it must equal the FeatureRecord
        # Literal so the two cannot drift apart.
        literal = typing.get_args(
            FeatureRecord.model_fields["attribution_status"].annotation
        )
        assert set(literal) == set(ATTRIBUTION_STATUS_VALUES)

    def test_default_is_none_when_field_omitted(self):
        # Back-compat: an entry written before B2-M3 (no attribution_status) stays
        # valid and defaults to "none" — existing YAML needs no edit to load.
        record = FeatureRecord(
            name="x",
            family="price",
            source="alpaca_ohlcv",
            formula="f",
            lookback_bars=0,
            publication_lag_days=0,
            point_in_time_rule="rule",
            added_phase="2",
            glossary_ref="ref",
            ablation_status="untested",
        )
        assert record.attribution_status == "none"

    def test_bad_attribution_status_enum_raises(self, tmp_path: Path):
        path = tmp_path / "bad_attr.yaml"
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
                    attribution_status: bogus
                """
            )
        )
        with pytest.raises(ValidationError):
            load_catalog(path)

    def test_every_catalog_entry_has_valid_attribution_status(self):
        catalog = load_catalog()
        offenders = [
            f"{name}={r.attribution_status}"
            for name, r in catalog.items()
            if r.attribution_status not in ATTRIBUTION_STATUS_VALUES
        ]
        assert not offenders, f"invalid attribution_status values: {offenders}"


class TestAttributionStatusDrift:
    """Both-directions code-vs-config drift (METHODOLOGY §6).

    The catalog's ``attribution_status`` must agree with the B2 attribution
    *surface* — which features got which signal(s) — defined by the runner's
    frozen feature sets:

      * G1 set (``FINAL_FEATURE_COLUMNS``, 25 cols) ran **both** the canonical
        ablation reference and the permutation proxy → status ``both``/``agreed``.
      * the candidate-only features (in ``CANDIDATES`` but not the G1 set) ran
        ablation only → status ``ablation_only``.
      * every other feature ran no OOS attribution → status ``none``.

    The agreed-vs-both split within the G1 set is data-driven (populated from the
    slice checkpoint, recorded via the ledger); this test enforces the *class*
    contract, which is what a future catalog edit could silently violate.
    """

    @classmethod
    def _surface(cls) -> tuple[set[str], set[str]]:
        runner = _load_script("b2_runner_for_catalog", "run_b2_attribution.py")
        g1 = set(runner.FINAL_FEATURE_COLUMNS)
        ablation_only = set(runner.CANDIDATES) - g1
        return g1, ablation_only

    def test_g1_features_have_both_signal_status(self):
        catalog = load_catalog()
        g1, _ = self._surface()
        offenders = [
            f"{name}={catalog[name].attribution_status}"
            for name in sorted(g1)
            if catalog[name].attribution_status not in {"both", "agreed"}
        ]
        assert not offenders, (
            "G1 features (both signals computed) must be 'both' or 'agreed': "
            f"{offenders}"
        )

    def test_candidate_only_features_are_ablation_only(self):
        catalog = load_catalog()
        _, ablation_only = self._surface()
        offenders = [
            f"{name}={catalog[name].attribution_status}"
            for name in sorted(ablation_only)
            if catalog[name].attribution_status != "ablation_only"
        ]
        assert not offenders, (
            f"candidate-only features must be 'ablation_only': {offenders}"
        )

    def test_unattributed_features_stay_none(self):
        # Reverse direction: a feature outside the B2 surface must NOT claim any
        # attribution it never received (no phantom attribution).
        catalog = load_catalog()
        g1, ablation_only = self._surface()
        attributed = g1 | ablation_only
        offenders = [
            f"{name}={catalog[name].attribution_status}"
            for name in sorted(catalog)
            if name not in attributed and catalog[name].attribution_status != "none"
        ]
        assert not offenders, (
            f"features outside the B2 attribution surface must be 'none': {offenders}"
        )

    def test_both_status_set_equals_m6_25col_input_set(self):
        """The 25-vs-27 reconciliation, pinned in code (METHODOLOGY §6).

        docs/concepts/oos-attribution.md §"The attributed set" and
        docs/PHASE_4A_REPORT.md §"The attributed set" both assert the identity
        ``27 = 25 + 2``: the 25 catalog rows with ``attribution_status == "both"``
        are *precisely* the M6 model-*input* set, and the 2 ``ablation_only`` rows
        are the catalog-only cross-sectional ranks. Only prose + the runner config
        carried that 25-col list, so the catalog's own 27-row drift test could not
        see the 25-vs-27 seam. The single source of truth for the M6 list is the
        frozen runner constant ``FINAL_FEATURE_COLUMNS`` (loaded via ``_surface``).

        Set equality (both directions) is strictly tighter than the
        ``test_g1_features_have_both_signal_status`` subset check: it also catches a
        25-col feature silently flipped to ``"agreed"`` and any stray 26th ``"both"``.
        """
        catalog = load_catalog()
        g1, _ = self._surface()
        assert len(g1) == 25, (
            f"canonical M6 model-input set must be 25 columns, got {len(g1)}: "
            f"{sorted(g1)}"
        )
        both = {
            name for name, r in catalog.items() if r.attribution_status == "both"
        }
        missing = sorted(g1 - both)  # M6 inputs not marked 'both'
        extra = sorted(both - g1)    # 'both' rows that are not M6 inputs
        assert both == g1, (
            "attribution_status=='both' set must equal the M6 25-column input set: "
            f"missing={missing}, extra={extra}"
        )

    def test_two_noninput_xs_ranks_are_ablation_only(self):
        """The 2-column catalog/model gap is exactly the two non-input ranks.

        ``xs_rank_ret_21d`` / ``xs_rank_ret_252d`` are registered in the catalog and
        ran an add-one ablation, but are not M6 model inputs, so no OOS permutation
        score is defined → ``ablation_only`` (oos-attribution.md §"The attributed set").
        They are the entirety of the 27 − 25 = 2 gap, so they must NOT be ``"both"``.
        """
        catalog = load_catalog()
        for name in ("xs_rank_ret_21d", "xs_rank_ret_252d"):
            assert name in catalog, f"expected {name} registered in the catalog"
            assert catalog[name].attribution_status == "ablation_only", (
                f"{name} is a catalog-only rank (not an M6 input) and must be "
                f"'ablation_only', got {catalog[name].attribution_status!r}"
            )
