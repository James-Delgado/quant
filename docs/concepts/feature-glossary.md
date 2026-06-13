# Feature Glossary

> Succinct reference for all features produced by
> `src/quant/features/engineering.py:build_features()` (17 base + 4 regime
> indicators + 3 sentiment when `sentiment_df` is passed) and
> `src/quant/features/cross_sectional.py:add_cross_sectional_features()`
> (3 cross-sectional ranks). All features are point-in-time correct — no
> lookahead.
>
> **Catalog ↔ glossary division of labor.**
> `src/quant/features/catalog.yaml` (Phase 4A M4) is the *machine-readable
> index*: one row per column, with family/source/lookback/lag/ablation
> metadata. This file is the *prose rationale*: what each feature means and
> why we expect it to carry signal. The drift-enforcement test in
> `tests/test_catalog.py` checks every catalog entry's `glossary_ref`
> resolves to a `### <name>` heading below, so prose and registry can't
> silently diverge.

---

## Price features (`_compute_price_features`)

### ret_1d
`close.pct_change(1)` — one-bar return.

The most recent daily return. Noisy but captures overnight moves and fast
news reactions. Feeds into volatility estimates.

### ret_5d
`close.pct_change(5)` — 5-bar (one-week) return.

Short-term momentum over one trading week. Captures the tail of weekly news
cycles and short-lived price trends.

### ret_21d
`close.pct_change(21)` — 21-bar (one-month) return.

Monthly momentum. At this horizon some stocks show reversal, others
continuation; the model learns which regime it is in from context.

### vol_21d
`ret.rolling(21).std()` — 21-bar realized volatility.

Short-term risk estimate. High values signal stressed conditions. Volatility
clustering means high vol today predicts high vol tomorrow.

### vol_63d
`ret.rolling(63).std()` — 63-bar (one-quarter) realized volatility.

Slower volatility baseline. The implicit ratio `vol_21d / vol_63d` captures
whether near-term volatility is expanding or contracting.

### mom_21d
`sign(ret.rolling(21).sum())` — discrete 21-bar momentum signal.

+1 if the stock is up over the past month, −1 if down. Used directly by
`MomentumBaseline` (column index 5 in the feature matrix). Encodes trend
direction without being distorted by return magnitude.

### rsi_14
14-bar Relative Strength Index: `100 − 100 / (1 + avg_gain / avg_loss)`.

Classic mean-reversion oscillator, bounded [0, 100]. RSI > 70 ≈ overbought;
RSI < 30 ≈ oversold. In strongly trending markets RSI can stay extreme — the
regime features (`ma200_ratio`) help the model condition on this.

### log_volume
`log1p(volume)` — natural log of share volume.

Reduces skew and makes volume comparable across stocks of different sizes.
Abnormal volume often precedes directional moves and proxies for liquidity.

### ret_252d *(Phase 2.5)*
`close.pct_change(252)` — trailing 12-month return.

Annual momentum — the most replicated return-predictability finding in finance
(Jegadeesh & Titman, 1993). Past 12-month winners tend to continue over the
next 3–12 months. Requires 252 bars of history before producing valid values.

### ret_126d *(Phase 2.5)*
`close.pct_change(126)` — trailing 6-month return.

Intermediate momentum. Paired with `ret_252d` it distinguishes stocks
accelerating into 12-month strength from those peaking 6 months ago.

### ma200_ratio *(Phase 2.5)*
`close / close.rolling(200).mean()` — price relative to 200-day moving average.

Primary regime filter. Above 1.0 → bull regime; below 1.0 → bear regime.
The most important conditioning feature: RSI and other mean-reversion signals
work in ranging markets but destroy value in strong trends. Requires 200 bars
of history.

### ma50_ratio *(Phase 2.5)*
`close / close.rolling(50).mean()` — price relative to 50-day moving average.

Faster trend signal than `ma200_ratio`. Together they capture momentum
confirmation: `ma50 > ma200` (golden cross) vs `ma50 < ma200` (death cross).

### volume_ratio *(Phase 2.5)*
`volume / volume.rolling(63).mean()` — volume relative to trailing 63-bar average.

Normalises volume across symbols and time. Values > 1.0 = above-average
participation. High relative volume is associated with institutional activity
and increases the probability that price moves are sustained.

---

## FRED macro features (`_attach_fred_features`)

Joined via backward ASOF merge: each bar receives the most recent FRED
observation whose **publication-lag-shifted** date ≤ bar date. Weekend and
holiday gaps are forward-filled. Since Phase 4A Milestone 5, observation
dates are shifted forward by each series' pinned publication lag
(`FRED_PUBLICATION_LAGS`, business days) before the merge — see
[fred-publication-lag.md](fred-publication-lag.md) for the evidence,
decision-time convention, and update protocol. Pass
`fred_publication_lags=None` to `build_features()` for the legacy unlagged
join (Phase 2.5/3 historical results).

### DFF
Federal Funds Effective Rate (%), daily. Source: FRED `DFF`.

Publication lag: **1 business day** (NY Fed releases EFFR the next business
day ~9am ET — see [fred-publication-lag.md](fred-publication-lag.md)).

The overnight interbank lending rate, set by Fed policy. The dominant driver of
equity discount rates. Rising DFF compresses valuations; falling DFF expands
them.

### DGS10
10-Year Treasury Constant Maturity Rate (%), daily. Source: FRED `DGS10`.

Publication lag: **1 business day** (H.15 release; FRED vintage lands the
next morning — see [fred-publication-lag.md](fred-publication-lag.md)).

Benchmark long-term risk-free rate. Rising yields raise the hurdle for equity
returns and discount future cash flows at a higher rate. Published Mon–Thu;
Friday gaps are forward-filled.

### VIXCLS *(Phase 2.5)*
CBOE Volatility Index, daily. Source: FRED `VIXCLS`.

Publication lag: **1 business day** by decision-time convention (Cboe
disseminates the close ~4:15pm ET, after the 4:00pm signal close — see
[fred-publication-lag.md](fred-publication-lag.md)).

The market's 30-day implied volatility for the S&P 500, derived from options
prices. High VIX signals fear and has historically preceded positive realized
risk premia. Provides macro sentiment information distinct from realized
volatility (`vol_21d`, `vol_63d`).

---

## Derived macro feature

### yield_curve *(Phase 2.5)*
`DGS10 − DFF` — the term spread.

Publication lag: inherited — computed *after* the asof merge from the
already-shifted DGS10 and DFF columns, so it is point-in-time correct by
construction ([fred-publication-lag.md](fred-publication-lag.md)).

The single most-cited macro leading indicator: every US recession since 1970
has been preceded by an inverted yield curve (yield_curve < 0). A compressed or
negative spread tightens bank lending margins and signals credit pessimism.
Computed post-merge from existing series — no additional ingestion needed.

---

## Regime-indicator features (`_add_regime_features`) *(Phase 4A)*

Appended after the 17 base columns on both FRED paths. All four are
NaN-propagating: a missing input produces a NaN indicator, never a silent
default. Point-in-time rule: each indicator is a deterministic transform of
already-point-in-time-correct columns at the same bar — no additional
lookback, no additional leakage surface.

### vix_regime *(Phase 4A)*
Ordinal {0, 1, 2} bucket of `VIXCLS`: 0 = calm (VIX ≤ low threshold),
1 = normal, 2 = stressed (VIX ≥ high threshold).

Thresholds are read from `VIXThresholdDetector`'s dataclass defaults in
`backtest/regimes.py` (single source of truth), so the Milestone 1
*evaluation* axis and this *feature* can never drift apart. Gives the model
an explicit handle for the volatility-regime conditioning that nb05 showed
the evaluation needs. Lookback: none beyond the (already-lagged) VIXCLS
join. NaN when VIXCLS is NaN.

### curve_inverted *(Phase 4A)*
Binary: `yield_curve < 0` (DGS10 − DFF inverted).

The discrete version of the glossary's `yield_curve` thesis: every US
recession since 1970 was preceded by an inversion. The binarization hands
the GBM the economically meaningful threshold directly instead of asking it
to discover the zero crossing from a continuous spread. Lookback: none;
inherits the publication-lagged DGS10/DFF. NaN when `yield_curve` is NaN.

### vol_regime_ratio *(Phase 4A)*
`vol_21d / vol_63d` — near-term realized volatility relative to its
quarterly baseline.

Values > 1 mean volatility is expanding (stress building), < 1 contracting.
Makes the ratio that `vol_21d`/`vol_63d` only encode *implicitly* an
explicit input. `vol_63d == 0` maps to NaN, not inf. Lookback: 63 bars
(inherited from `vol_63d`).

### trend_regime *(Phase 4A)*
Binary: `ma200_ratio > 1` — price above its 200-day moving average.

The discrete bull/bear regime switch behind nb05's trend-fighting
diagnosis: mean-reversion signals (e.g. `rsi_14`) pay in ranging markets
and destroy value in persistent trends. Lookback: 200 bars (inherited from
`ma200_ratio`). NaN during the MA warmup.

---

## Cross-sectional rank features (`add_cross_sectional_features`) *(Phase 4A)*

Produced by `src/quant/features/cross_sectional.py`, not `build_features` —
they need the whole panel at once. For each source column, every symbol
receives its percentile rank (0–1] across the universe symbols with data on
that date: "where does this symbol sit relative to the panel today".

**Point-in-time rule:** each rank at bar *t* uses ONLY the same-date values
of point-in-time features already produced by `build_features` —
**same-date cross-sectional, no temporal aggregation, no lookahead by
construction**. Dates where fewer than `min_symbols` symbols have data are
NaN wholesale (a rank over two symbols is noise).

**Survivorship caveat:** the ranked universe (DJIA 30 + ETFs, chosen in
Phase 2.5) was selected with hindsight of which constituents survived to
selection time. The rank features *inherit* that universe-membership bias;
they do not add to it.

On the 5-symbol slice notebooks the ranks are coarse quintiles
({0.2, 0.4, 0.6, 0.8, 1.0}); the full 33-symbol panel gives finer ranks.

### xs_rank_ret_21d *(Phase 4A)*

Cross-sectional percentile rank of `ret_21d`.

Relative 1-month momentum: is this symbol leading or lagging the panel this
month? Cross-sectional momentum is more robust than time-series momentum to
market-wide shocks, which shift every symbol's raw return but not the
ordering. Lookback: 21 bars (inherited from `ret_21d`).

### xs_rank_ret_252d *(Phase 4A)*
Cross-sectional percentile rank of `ret_252d`.

The classic cross-sectional momentum sort (Jegadeesh & Titman, 1993 ranks
stocks exactly this way — the finding is *relative*, not absolute,
momentum). Lookback: 252 bars (inherited from `ret_252d`).

### xs_rank_vol_21d *(Phase 4A)*
Cross-sectional percentile rank of `vol_21d`.

Relative riskiness axis — the low-volatility anomaly (Ang et al., 2006) is
a cross-sectional finding: the *quietest* names in the panel, not "low vol"
in absolute terms, historically earn superior risk-adjusted returns.
Lookback: 21 bars (inherited from `vol_21d`).

---

## Sentiment features (`aggregate_sentiment`) *(Phase 3)*

Produced by `src/quant/features/sentiment.py` via FinBERT-scored 8-K / 10-K
/ 10-Q filings and RSS items (see `src/quant/features/finbert.py` and
`src/quant/ingest/edgar.py`). Attached when `build_features()` is called
with a non-`None` `sentiment_df`.

**Point-in-time rule:** `aggregate_sentiment()` uses a strict-less-than
filter on `published_at` (`published_at < bar t`) and a fixed lookback
window (default 30 calendar days), so the model sees only documents
publicly available before the bar close.

### sentiment_score
Mean FinBERT sentiment score over documents published in the trailing
`sentiment_lookback_days` window for this symbol. Range roughly [-1, +1];
NaN when no documents in window.

### doc_count
Number of documents in the trailing window for this symbol. Provides the
denominator behind `sentiment_score` and a coarse coverage signal in its
own right.

### has_coverage
Binary flag: `(doc_count > 0).astype(float)`. Distinguishes "no news" from
a neutral mean score and lets the model condition on whether sentiment
information is present at all.

---

## Future candidates (parking lot)

Candidate features surfaced *mid-milestone* are recorded here — **not**
added to a running ablation. The Milestone 3 candidate list was
pre-committed before any result was computed; widening it after seeing
results would reintroduce the winner's-curse problem the ablation noise
guard exists to prevent. Parking-lot entries graduate by being pre-committed
into a *future* milestone's candidate list with their own ablation budget.

*(empty)*

---

## NaN warmup periods

Rows with any NaN are dropped before backtesting. With 20 years of data the
252-bar warmup costs ~5% of observations.

| Feature | Warmup bars | ~Calendar time |
|---------|------------|----------------|
| `rsi_14` | 14 | 3 weeks |
| `ret_21d`, `vol_21d`, `mom_21d` | 21 | 1 month |
| `ma50_ratio` | 50 | 2.5 months |
| `vol_63d`, `volume_ratio` | 63 | 3 months |
| `ret_126d` | 126 | 6 months |
| `ma200_ratio` | 200 | 10 months |
| `trend_regime` | 200 | 10 months |
| `ret_252d` | 252 | 12 months |
| `xs_rank_ret_252d` | 252 (every panel symbol) | 12 months |
