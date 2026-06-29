"""C2-M2 execution bridge — daily ARIMA signal → paper-account target position.

This module stands up the missing path between a model forecast and a broker
order (PRD "Problem" items 1–2): a **prediction-emission contract**
(``daily_signal``) and a **broker boundary** (the ``ExecutionBridge`` Protocol),
plus the **G1 signal-parity gate** that proves the bridge emits the *same*
decision the Phase 1 backtest path would.

Platform (decided in C2-M1, ``docs/concepts/lean-setup.md``)
------------------------------------------------------------
LEAN-local is paywalled, so the ratified §8.3 fallback was taken:
``AlpacaPaperBridge`` is the **primary** impl; ``LeanBridge`` is a documented
deferred swap. Both sit behind one ``ExecutionBridge`` Protocol so the platform
is a swap, not a rewrite. The file keeps the pinned ``lean_bridge.py`` name (the
deliverable path frozen in ``PRIORITIES.yaml``/the PRD before compute,
METHODOLOGY §1) — the same precedent as ``lean-setup.md`` retaining its name
after the Alpaca decision. The ``C2-DOC-PLATFORM-SYNC`` follow-up owns the
prose wording sweep.

The parity rule (why G1 can reach zero mismatches)
--------------------------------------------------
The Phase 1 portfolio backtest derives a per-symbol signal as
``signals = np.sign(raw_pred).astype(int)`` (``backtest/harness.py``), i.e.
long/short/flat ∈ {-1, 0, +1}. ``derive_target_position`` reproduces that exact
mapping, so given the *same* forecast both paths emit the *same* target
position — the G1 gate (``signal_parity_gate_report``) then verifies it on real
forecasts with a pinned **0-mismatch** threshold. This resolves the PRD's
"long/flat vs long/short" open question: it must be long/short/flat, because G1
reconciles against the backtest's ``sign()`` convention and the two must be
identical.

What ARIMA uses (and what build_features is for)
------------------------------------------------
The ARIMA(1,0,0) placeholder forecasts from the **forward-return label series**
(``features/labels.generate_labels`` on the adjusted close), matching the
backtest ARIMA arm where ``fit(X, y)`` ignores the feature matrix ``X`` and
fits on ``y``. ``build_feature_row`` is provided as the reader→features seam a
future feature-consuming B-model plugs into (exercised in the E2E notebook);
the ARIMA placeholder does not consume it. Sizing/vol-targeting (C3) and
confidence intervals (C4) are out of scope — the placeholder trades a fixed
share quantity whose P&L is deliberately uninteresting.

Scope boundary
--------------
C2-M2 ships the bridge + ``daily_signal`` + the G1 gate + position-state
persistence *format*. The G2 backtest↔paper reconciliation and the G3 ≥5-cycle
liveness loop are C2-M3. This module touches **no** walk-forward split logic
(``backtest/CLAUDE.md``): it only consumes forecasts and feature rows.

Design — pure logic vs network adapters
---------------------------------------
``derive_target_position`` / ``plan_order`` / ``build_market_order`` /
``signal_parity_gate_report`` / position-state load+save are pure (no network)
and unit-tested directly. ``AlpacaPaperBridge`` methods are the thin adapters
that touch the live paper API; they are exercised against fakes, mirroring
``tests/test_c2_hello_world.py``.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from quant.features.labels import generate_labels
from quant.models.arima_baseline import ARIMABaseline
from quant.storage.realtime import PRICE_DATASET, get_pit_panel

# ─── Pinned constants (METHODOLOGY §1/§2 — the code is the source of truth) ─────
# G1 signal-parity gate: the bridge-emitted target position must equal the
# backtest-path target position for every checked (symbol, date). The material-
# mismatch count must be exactly this value to pass (C2 PRD G1). Changing it
# after a result is visible invalidates the run and requires a PRD revision plus
# a new ledger entry (METHODOLOGY §1). The same constant is consumed by the gate
# and its tests under a drift contract (METHODOLOGY §6).
G1_MAX_MISMATCHES: int = 0

# The placeholder's fixed share quantity. C2 emits a *fixed* position whose P&L
# is uninteresting by design — sizing/vol-targeting is C3 (PRD "Out of scope").
# A fixed share count (not a fixed notional) keeps the placeholder free of any
# price-to-shares sizing logic; the exact value is immaterial against the $1M
# paper account. Mirrors the C2-M1 hello-world's 1-share order.
PLACEHOLDER_QTY: float = 1.0

# Cash-fraction sizing constants (C2-M2-SIZING-PARITY). The Phase 1 simulator
# (``backtest/simulator.py``) deploys ~all available capital — it sizes a
# position as ``int(cash / entry_fill)`` capped by ``int(volume * liquidity_cap)``
# — not a fixed share. For C2-M3's G2 (backtest⇔paper per-period total-return
# reconciliation, ≤1%) to hold honestly, the bridge must be able to size by the
# SAME rule. These mirror ``simulate``'s signature defaults under a §6 drift
# contract: ``test_sizing_constants_match_simulator_defaults`` asserts equality
# via ``inspect.signature(simulate)``, so the two cannot drift apart silently.
SIM_SLIPPAGE_BPS: float = 5.0
SIM_LIQUIDITY_CAP: float = 0.10

# Minimum non-NaN label observations before ARIMA(1,0,0) is fit for a symbol.
# Below this the symbol is skipped (no signal emitted) rather than fitting on a
# series too short to be meaningful. ARIMABaseline.fit itself requires only
# > sum(order)+1 = 2 observations; this is a stricter sanity floor.
MIN_LABEL_OBS: int = 30

# Float tolerance for "already at target" position comparisons (fractional
# shares are possible on Alpaca; a sub-epsilon delta is a no-op).
_QTY_EPS: float = 1e-9


# ─── Signal emission: forecast → target position ───────────────────────────────


def derive_target_position(forecast: float) -> int:
    """Map a return forecast to a target position ∈ {-1, 0, +1}.

    This is the **shared parity rule**: it reproduces the Phase 1 backtest
    convention ``np.sign(raw_pred).astype(int)`` (``backtest/harness.py``,
    per-symbol signal derivation), so the bridge and the backtest emit the same
    decision from the same forecast — the structural basis for G1 = 0 mismatches.

    A non-finite forecast maps to 0 (flat) as a hardening guard. The backtest
    path applies no such guard, but its ARIMA forecasts are always finite, so
    this branch never fires inside the G1 parity window and cannot perturb the
    gate (declared, parity-neutral; METHODOLOGY §9).
    """
    if not np.isfinite(forecast):
        return 0
    return int(np.sign(forecast))


def backtest_path_target_position(forecast: float) -> int:
    """The target position the Phase 1 backtest path derives from *forecast*.

    Independently reimplements the backtest's ``np.sign(raw_pred).astype(int)``
    step (``backtest/harness.py``) on a scalar forecast, so the G1 gate compares
    two *independently computed* values rather than calling the same function
    twice (which would be a tautology). On finite forecasts it equals
    :func:`derive_target_position` by construction — that equality is exactly
    what G1 asserts on real data.
    """
    return int(np.sign(np.asarray([forecast], dtype=float)).astype(int)[0])


@dataclass(frozen=True)
class TargetSignal:
    """A single symbol's daily decision: the forecast and the target position."""

    symbol: str
    asof: pd.Timestamp
    forecast: float
    target_position: int


def daily_signal(
    asof: pd.Timestamp | str,
    symbols: Sequence[str] | None = None,
    *,
    label_horizon: int = 1,
    dataset: str = PRICE_DATASET,
    min_label_obs: int = MIN_LABEL_OBS,
) -> dict[str, TargetSignal]:
    """Emit today's ARIMA target position for each symbol with enough history.

    Pipeline (PRD scope item 2): ``get_pit_panel(asof) → forward-return labels →
    ARIMA(1,0,0) fit on the label series → predict_one_step → sign → target``.
    The reader returns only point-in-time-correct bars (timestamp ≤ asof), so the
    forecast carries no look-ahead. Symbols absent from the lake at *asof*, or
    with fewer than *min_label_obs* non-NaN labels, are omitted from the result.

    The ARIMA placeholder forecasts from the label series (matching the backtest
    ARIMA arm, where ``fit`` ignores the feature matrix); a future feature-
    consuming B-model would instead score :func:`build_feature_row`.

    Returns ``{symbol: TargetSignal}`` for every symbol that produced a signal.
    """
    if symbols is None:
        from quant.config import settings

        symbols = list(settings.equity_universe)
    if not symbols:
        raise ValueError("symbols must not be empty")

    asof_ts = pd.Timestamp(asof)
    panel = get_pit_panel(symbols, asof_ts, dataset=dataset)

    out: dict[str, TargetSignal] = {}
    for sym in symbols:
        frame = panel.get(sym)
        if frame is None or frame.empty:
            continue
        close = frame["close"]
        if len(close) <= label_horizon:
            continue
        labels = generate_labels(close, label_horizon).series.dropna()
        if len(labels) < min_label_obs:
            continue
        forecast = ARIMABaseline().fit(None, labels.to_numpy()).predict_one_step()  # type: ignore[arg-type]
        out[sym] = TargetSignal(
            symbol=sym,
            asof=asof_ts,
            forecast=float(forecast),
            target_position=derive_target_position(forecast),
        )
    return out


def build_feature_row(
    symbols: Sequence[str],
    asof: pd.Timestamp | str,
    *,
    dataset: str = PRICE_DATASET,
) -> dict[str, pd.DataFrame]:
    """Reader → features seam: the as-of feature matrices for *symbols*.

    Thin wrapper over ``get_pit_panel(asof) → build_features(asof)`` exposing the
    hook a future feature-consuming B-model plugs into (the ARIMA placeholder
    does not use it). Returns ``{symbol: feature DataFrame}`` truncated to bars
    ``timestamp ≤ asof``. Exercised by the C2 E2E notebook to demonstrate the
    full live-inference path is wired, even though ARIMA forecasts from labels.
    """
    from quant.features.engineering import build_features

    asof_ts = pd.Timestamp(asof)
    panel = get_pit_panel(symbols, asof_ts, dataset=dataset)
    present = [s for s in symbols if s in panel]
    if not present:
        return {}
    return build_features(present, panel, asof=asof_ts)


# ─── G1 signal-parity gate ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SignalParityResult:
    """Verdict of the G1 signal-parity gate."""

    n_checked: int
    n_mismatches: int
    passed: bool


def signal_parity_gate_report(
    checks: Sequence[tuple[int, int]],
    *,
    max_mismatches: int = G1_MAX_MISMATCHES,
) -> SignalParityResult:
    """G1: bridge target position == backtest-path target position, 0 mismatches.

    *checks* is a sequence of ``(bridge_target_position,
    backtest_path_target_position)`` pairs over the replay window — typically
    built by pairing :func:`derive_target_position` against
    :func:`backtest_path_target_position` on the same forecasts. A PASS requires
    a non-empty set of checks **and** a material-mismatch count ≤
    *max_mismatches* (the pinned ``G1_MAX_MISMATCHES`` = 0). An empty check set
    cannot pass — there is no parity to assert.
    """
    n = len(checks)
    mismatches = sum(1 for bridge, backtest in checks if bridge != backtest)
    return SignalParityResult(
        n_checked=n,
        n_mismatches=mismatches,
        passed=n > 0 and mismatches <= max_mismatches,
    )


# ─── Order planning: target position → order intent ────────────────────────────


@dataclass(frozen=True)
class OrderIntent:
    """A pure (no-network) description of the order needed to reach a target."""

    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float


def plan_order(
    symbol: str,
    target_position: int,
    current_qty: float,
    *,
    qty: float = PLACEHOLDER_QTY,
) -> OrderIntent | None:
    """Plan the order that moves *current_qty* to ``target_position * qty``. Pure.

    ``target_position`` ∈ {-1, 0, +1} (long/short/flat); the desired holding is
    ``target_position * qty``. The order is the signed delta from *current_qty*:
    a positive delta is a BUY, a negative delta a SELL, and a sub-epsilon delta
    is a no-op (``None``) — so flat (0) closes an existing position and an
    already-on-target symbol places nothing. Sizing beyond this fixed quantity
    is C3.
    """
    desired = target_position * qty
    delta = desired - current_qty
    if abs(delta) < _QTY_EPS:
        return None
    side = "BUY" if delta > 0 else "SELL"
    return OrderIntent(symbol=symbol, side=side, qty=abs(delta))


# ─── Cash-fraction sizing: simulator parity (C2-M2-SIZING-PARITY) ──────────────


def simulator_position_qty(
    cash: float,
    ref_price: float,
    target_position: int,
    *,
    volume: float | None = None,
    slippage_bps: float = SIM_SLIPPAGE_BPS,
    liquidity_cap: float = SIM_LIQUIDITY_CAP,
) -> int:
    """Share magnitude the Phase 1 simulator would open — the G2 sizing rule. Pure.

    Reproduces ``backtest/simulator.py``'s entry sizing exactly:
    ``shares = max(0, min(int(cash / entry_fill), int(volume * liquidity_cap)))``
    where ``entry_fill`` is the slippage-adjusted fill — a long buys at the ask
    (``ref_price * (1 + slip)``) and a short sells at the bid
    (``ref_price * (1 - slip)``), ``slip = slippage_bps / 10_000``. The cap uses
    *cash before commission*, matching the simulator (commission is deducted
    after the share count is fixed and so does not change it).

    *target_position* selects the slippage side; a flat target (0) needs no
    shares and returns 0. *volume* is the fill-bar volume for the liquidity cap;
    pass ``None`` to skip it (a paper order may have no volume read at submit
    time — the cash cap then governs, never the 1-share placeholder). Returns a
    non-negative integer magnitude; the caller applies the sign via the target
    position. Non-positive *cash* or *ref_price* yields 0.
    """
    if target_position == 0 or cash <= 0.0 or ref_price <= 0.0:
        return 0
    slip = slippage_bps / 10_000.0
    if target_position > 0:
        entry_fill = ref_price * (1.0 + slip)  # buying long at the ask
    else:
        entry_fill = ref_price * (1.0 - slip)  # selling short at the bid
    if entry_fill <= 0.0:
        return 0
    max_cap = int(cash / entry_fill)
    if volume is not None:
        max_cap = min(max_cap, int(volume * liquidity_cap))
    return max(0, max_cap)


def sized_target_order(
    symbol: str,
    target_position: int,
    *,
    cash: float,
    ref_price: float,
    volume: float | None = None,
    slippage_bps: float = SIM_SLIPPAGE_BPS,
    liquidity_cap: float = SIM_LIQUIDITY_CAP,
) -> TargetOrder:
    """Build a :class:`TargetOrder` sized by the simulator's cash-fraction rule. Pure.

    Wraps :func:`simulator_position_qty` so the bridge can deploy ~all capital
    like the backtest (the G2 prerequisite) instead of the fixed
    :data:`PLACEHOLDER_QTY`. Sizing logic proper (vol-targeting / risk caps) is
    still C3 — this is only the placeholder-vs-simulator parity G2 needs.
    """
    qty = simulator_position_qty(
        cash,
        ref_price,
        target_position,
        volume=volume,
        slippage_bps=slippage_bps,
        liquidity_cap=liquidity_cap,
    )
    return TargetOrder(symbol=symbol, target_position=target_position, qty=float(qty))


# ─── Position-state persistence (format pinned here; the loop is C2-M3) ─────────


@dataclass(frozen=True)
class PositionState:
    """Holdings snapshot persisted between daily runs (PRD open-Q, G3 prep).

    ``asof`` is an ISO-8601 string; ``holdings`` maps symbol → signed share
    quantity. The C2-M3 ≥5-cycle liveness loop round-trips this so run N+1 opens
    where run N closed. The format is pinned **here** (METHODOLOGY §1); a richer
    store is a C5/console concern, flagged not built.
    """

    asof: str
    holdings: dict[str, float]


def save_position_state(state: PositionState, path: str | Path) -> None:
    """Write *state* to *path* as JSON (creating parent dirs). Pure-ish (disk only)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))


def load_position_state(path: str | Path) -> PositionState | None:
    """Read a :class:`PositionState` from *path*, or ``None`` if absent.

    A missing file means "no prior run" (the first cycle), which is not an
    error. Holdings values are coerced to float so a round-trip is exact.
    """
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    holdings = {str(k): float(v) for k, v in data.get("holdings", {}).items()}
    return PositionState(asof=str(data["asof"]), holdings=holdings)


# ─── Broker boundary: the ExecutionBridge Protocol + impls ──────────────────────


@dataclass(frozen=True)
class AccountSummary:
    """Read-only paper-account fields the bridge reports (mirrors C2-M1)."""

    account_number: str
    status: str
    cash: str
    buying_power: str
    equity: str


@dataclass(frozen=True)
class TargetOrder:
    """A symbol's target position handed to the bridge for execution."""

    symbol: str
    target_position: int  # {-1, 0, +1}
    qty: float = PLACEHOLDER_QTY


class ExecutionBridge(Protocol):
    """Broker-agnostic execution boundary (the contract C3/C4 consume).

    Two impls live behind it: ``AlpacaPaperBridge`` (primary, C2) and
    ``LeanBridge`` (deferred swap). The Protocol is what keeps the §8.3 platform
    fallback a swap, not a rewrite.
    """

    def account_summary(self) -> AccountSummary:
        """Snapshot the paper account (number, status, cash, buying power, equity)."""
        ...

    def current_positions(self) -> dict[str, float]:
        """Map ``symbol → signed share quantity`` for currently held positions."""
        ...

    def place_target(self, order: TargetOrder) -> dict:
        """Place the order needed to reach ``order.target_position``; return a result dict."""
        ...


def _summarize_account(account: object) -> AccountSummary:
    """Project an Alpaca account model onto :class:`AccountSummary`. Pure."""
    return AccountSummary(
        account_number=str(getattr(account, "account_number", "")),
        status=str(getattr(account, "status", "")),
        cash=str(getattr(account, "cash", "")),
        buying_power=str(getattr(account, "buying_power", "")),
        equity=str(getattr(account, "equity", "")),
    )


def build_market_order(intent: OrderIntent) -> object:
    """Build an Alpaca ``MarketOrderRequest`` from an :class:`OrderIntent`. Pure.

    A market ``DAY`` order, matching the C2-M1 hello-world convention: during
    RTH it fills; placed while closed it rests for the next open. Imported lazily
    so the module stays importable without the trading SDK on the path (the pure
    logic + the gate are testable without ``alpaca``).
    """
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    side = OrderSide.BUY if intent.side == "BUY" else OrderSide.SELL
    return MarketOrderRequest(
        symbol=intent.symbol,
        qty=intent.qty,
        side=side,
        time_in_force=TimeInForce.DAY,
    )


class AlpacaPaperBridge:
    """Primary ``ExecutionBridge`` impl — Alpaca paper trading (C2-M1 decision).

    Wraps an Alpaca ``TradingClient`` pinned to the paper endpoint. The client is
    injected (constructed via :meth:`from_settings` in production) so the bridge
    is unit-testable against a fake, exactly like the C2-M1 hello-world.
    """

    def __init__(self, client: object) -> None:
        self._client = client

    @classmethod
    def from_settings(cls) -> "AlpacaPaperBridge":
        """Build a paper bridge from the project's ``.env`` Alpaca keys.

        Reuses the C2-M1 ``build_paper_client`` helper (``paper=True`` — there is
        no live-trading code path in C2; live is gated on the C2-M3 G2 pass).
        """
        from alpaca.trading.client import TradingClient

        from quant.config import settings

        client = TradingClient(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=True
        )
        return cls(client)

    def account_summary(self) -> AccountSummary:
        return _summarize_account(self._client.get_account())  # type: ignore[attr-defined]

    def current_positions(self) -> dict[str, float]:
        """Read open paper positions as ``symbol → signed qty``.

        Alpaca reports short positions with a negative ``qty``; the sign is
        preserved so :func:`plan_order` computes the correct delta.
        """
        positions = self._client.get_all_positions()  # type: ignore[attr-defined]
        return {str(p.symbol): float(p.qty) for p in positions}

    def place_target(self, order: TargetOrder) -> dict:
        """Move the symbol's holding toward ``order.target_position``.

        Reads the current position, plans the signed-delta order
        (:func:`plan_order`), and submits it if one is needed. A symbol already
        at target places nothing (``submitted=False``).
        """
        current = self.current_positions().get(order.symbol, 0.0)
        intent = plan_order(
            order.symbol, order.target_position, current, qty=order.qty
        )
        if intent is None:
            return {
                "symbol": order.symbol,
                "submitted": False,
                "reason": "already at target",
                "target_position": order.target_position,
                "current_qty": current,
            }
        submitted = self._client.submit_order(build_market_order(intent))  # type: ignore[attr-defined]
        return {
            "symbol": order.symbol,
            "submitted": True,
            "side": intent.side,
            "qty": intent.qty,
            "target_position": order.target_position,
            "current_qty": current,
            "order_id": str(getattr(submitted, "id", "")),
            "status": str(getattr(submitted, "status", "")),
        }

    def place_sized_target(
        self,
        symbol: str,
        target_position: int,
        ref_price: float,
        *,
        volume: float | None = None,
    ) -> dict:
        """Place a simulator-sized target: deploy ~all account cash (the G2 rule).

        Reads the account's available cash, sizes the position with
        :func:`simulator_position_qty` (the Phase 1 simulator's cash-fraction
        rule), then routes through :meth:`place_target` so the signed-delta /
        no-op semantics are identical to the fixed-quantity path. This is the
        C2-M2-SIZING-PARITY entry point C2-M3's G2 reconciliation drives — the
        fixed :data:`PLACEHOLDER_QTY` path is preserved for the uninteresting
        placeholder. Sizing proper (vol-targeting / caps) remains C3.
        """
        cash = float(self.account_summary().cash)
        order = sized_target_order(
            symbol, target_position, cash=cash, ref_price=ref_price, volume=volume
        )
        return self.place_target(order)


class LeanBridge:
    """Deferred ``ExecutionBridge`` impl — QuantConnect LEAN (the future swap).

    Not implemented in C2: LEAN-local is paywalled for local data/live use
    (``docs/concepts/lean-setup.md`` §1 + Appendix A), so the ratified §8.3
    fallback (Alpaca paper) is the C2 platform. This stub exists to fix the
    swap point in the Protocol; revisit only if a later milestone needs asset
    classes we lack data for, or hosted live after a B-model clears its gate.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "LeanBridge is a deferred future swap — C2 runs on AlpacaPaperBridge "
            "per the C2-M1 platform decision (docs/concepts/lean-setup.md). "
            "LEAN local data/live use is behind a paid QuantConnect seat."
        )
