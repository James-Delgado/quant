# Refit cadence & data hunger — model-deployment design conventions

> **Audience**: whoever scopes a real (non-placeholder) model deployment —
> Project B when a signal clears a gate, Project C3/C4 when sizing/confidence
> land, or a future model-infrastructure project (frozen-checkpoint serving).
>
> **Status**: reference/design-rationale doc. Captures conventions derived from
> the codebase's validated backtest config and standard quant-equity practice,
> so the reasoning is not re-derived each time. Not binding like
> [`METHODOLOGY.md`](../METHODOLOGY.md); it informs, the gate functions decide.

This note answers three recurring questions: **how often do we refit a model**,
**why a frozen model is not a stale model**, and **how do we feed a data-hungry
model on a weak-signal daily universe**.

---

## 1 · Two clocks, not one

"Refit" conflates two cadences that run at deliberately different speeds:

| Clock | What it does | Cadence here |
|---|---|---|
| **Rebalance / signal** | recompute features, score the model, adjust positions | **daily** (the `scripts/trade_daily.py` cron) |
| **Parameter re-estimation** | re-learn the model's weights | **slow** (see §3) |

Keeping these separate is the whole design. The portfolio adapts daily; the
learned mapping is refreshed on a much slower clock.

---

## 2 · Frozen parameters ≠ frozen predictions

A prediction is `ŷ_t = f_θ(x_t)`. Refitting freezes **θ** (the learned mapping).
But **x_t** — today's feature vector — is recomputed daily from fresh market
data (`storage/realtime.py::get_pit_panel` → `features/engineering.py`). So a
frozen model still tracks new information within a refit interval: a VIX spike, a
momentum flip, a curve move all land in `x_t` and the frozen model responds. It
is **not** predicting from stale inputs — it is applying a stale *mapping* to
fresh inputs (the same reason you feed a language model a new prompt rather than
retraining it per query).

The failure mode a refit protects against is therefore **not** input staleness —
it is **concept drift**: the feature→return *relationship* itself shifting so
that a frozen θ becomes miscalibrated to a new regime. Refit cadence is the only
lever that addresses that; fresh features do not.

---

## 3 · What is validated in this repo, and the parity rule

The purged walk-forward (`backtest/walkforward.py`, defaults in
`backtest/harness.py`) uses a **fixed-length rolling** train window:

```
train_window = 504   # ~2 trading years, ROLLING (not expanding)
test_window  = 63    # ~1 quarter held frozen before the next refit
step         = 63    # advance a quarter per fold
embargo      = 3
```

So the **validated deployment regime is: train on a rolling ~2 years, refit
each quarter.** Each walk-forward test fold *is* a simulation of "fit once, hold
frozen for a quarter, score daily on fresh features" — which means the OOS Sharpe
already accounts for whatever within-quarter decay occurs (§4).

**The parity rule (binding in spirit):** whatever refit cadence + train window
you *deploy* is the config the backtest must *simulate*, because the OOS number
reflects a specific staleness profile. Deploy quarterly-refit / 2yr-rolling
because that is what earned the number. Want weekly, or an expanding window, or
time-decay weights? Re-run the walk-forward under that config and trust *that*
number — do not deploy an unvalidated regime. This is train/serve parity applied
to the training schedule, not just the feature pipeline.

### Refit cadence by strategy regime (industry context)

| Regime | Rebalance | Param refit | Why |
|---|---|---|---|
| HFT / microstructure | continuous | intraday–daily | data-rich; models simple/fast; no data hunger |
| **daily stat-arb / quant equity** (this repo) | daily | **monthly–quarterly** (sometimes weekly) | weak, non-stationary signal; refit slowly for stability, adapt via fresh features |
| slow factor / risk premia | weekly–monthly | quarterly–annual | structural, slow-moving relationships |

Daily *parameter* refits of a complex learned alpha model are rare — reserved for
fast, simple, data-rich models. A stochastic search (`models/gbm.py` is
`RandomizedSearchCV(n_iter=50)`) refit daily also injects day-to-day
nondeterminism into the live signal, independent of any predictive benefit — an
argument for freezing the fitted model and refitting on a schedule.

---

## 4 · The days-since-refit decay diagnostic

The gate reports **aggregate** fold Sharpe, which *averages over* the
within-interval decay curve. To *see* whether predictive power sags late in a
refit interval, bucket OOS performance by **days-since-refit** (e.g. days 1–10 vs
50–63 of each 63-bar fold) and compare. A visible late-interval sag is direct
evidence to shorten the cadence; a flat profile says the quarterly hold is fine.
This is the cheapest principled way to choose cadence and is tracked as a backlog
task (`A-REFIT-DECAY-DIAGNOSTIC`).

---

## 5 · Data hunger — the cross-section is the answer, not the window

A complex model (light NN, distilled model) needs far more data than a rolling
2-year window of a small universe provides. The levers, in priority order:

1. **Pool across symbols (primary).** Do not train one model per name. Train
   **one** model on the pooled panel of the whole universe, so each day
   contributes N training rows (one per symbol), not one. Features are
   **cross-sectionally normalized** (ranked/z-scored within each day —
   `features/cross_sectional.py::add_cross_sectional_features`, the `xs_rank_*`
   columns) so the model learns *relative* relationships that transfer across
   names and dates. This is the standard quant-equity workhorse and the main way
   firms feed complex models on only a few years of history.

   > **The binding constraint here is breadth, not depth.** This repo's universe
   > is **33 symbols** (Dow-30 + SPY/QQQ/IWM, `config.py`). Real quant equity runs
   > hundreds–thousands of names precisely for cross-sectional breadth. Expanding
   > the universe — **Project B4 (universe shift)** — is almost certainly a
   > higher-leverage enabler for any complex model than any refit-cadence or
   > window tuning.

2. **Long window + time-decay weighting (secondary).** Train on many years but
   exponentially down-weight old bars (half-life of months to ~2 years), so rare
   regimes (2008, COVID) are seen without treating 2004 as equal to last month.
   The hook exists — `features/weights.py::compute_sample_weights` — but currently
   implements only López de Prado overlap-*uniqueness*, **not** recency decay.
   Adding an exponential time-decay term is the natural extension and is the
   training regime a frozen NN would realistically use (versus GBM's short
   rolling window).

3. **Deliberate simplicity / regularization (consensus).** In weak-signal,
   non-stationary daily equity, linear and shallow models frequently beat deep
   learning because signal-to-noise is low enough that flexible models fit noise.
   The deep-learning quant shops win largely through data scale and
   cross-sectional breadth, not architecture on a small universe. Simple-model-
   first is not timidity — it is what tends to win. Complex models are the *last*
   lever, after breadth (§5.1) and signal.

What firms generally **do not** rely on: data augmentation (you cannot synthesize
realistic market data without smuggling in assumptions) and per-symbol models
(throws away the cross-section). Cross-market pretrain-then-finetune is emerging,
not yet standard.

---

## 6 · How this composes with the strategy registry

The registry (`execution/strategy_registry.py`) already resolves models by name
(`model_ref → resolve_model_class → importlib`) against a duck-typed
`fit(X, y) → predict(X)` contract, so a serving layer is *additive*, not a
rewrite. Two serving modes cover every model:

- **fit-on-read** — cheap, adaptive models refit inline each run (ARIMA today:
  `lean_bridge.daily_signal`; the generic path: `trade_daily._feature_model_signal`).
  Leakage-safe for free because it only reads PIT data (`timestamp ≤ asof`).
- **frozen-checkpoint** — train offline, freeze weights, **load and predict only**.
  Required for anything a cron can't refit (GBM done properly, and any NN). Needs
  a versioned artifact store, a loader, and — critically — a recorded
  **training-data cutoff** with a live assertion `cutoff ≤ asof`, because the
  frozen path does *not* get look-ahead safety for free the way fit-on-read does.

A `serving_mode: fit_on_read | frozen_checkpoint` discriminator (+ an
`artifact_ref` for the frozen case) and one branch in `_strategy_signal` is the
whole registry change. Provenance discipline (METHODOLOGY §12–13) extends
naturally: a frozen checkpoint's hash + training cutoff is a ledger entry, and a
*scheduled non-selective* refit is one ongoing strategy — but selecting among
refits/architectures is a multiple-testing surface the ledger's DSR deflation
must count. Building this out is the scope of a future model-infrastructure
project, gated on a model actually worth freezing (something clearing a
pre-committed gate). See the `F-GATE` tracker in
[`PRIORITIES.yaml`](../PRIORITIES.yaml).

---

*Cross-refs: [`purging-and-embargo.md`](purging-and-embargo.md) (why the window
is rolling + purged), [`METHODOLOGY.md`](../METHODOLOGY.md) §11–13 (slice/full-panel
discipline, ledger, DSR gates), `strategy-registry.md` (the deployment contract).*
