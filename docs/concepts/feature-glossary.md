# Feature Glossary

> Succinct reference for all 17 features produced by
> `src/quant/features/engineering.py:build_features()`.
> All features are point-in-time correct — no lookahead.

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
| `ret_252d` | 252 | 12 months |
