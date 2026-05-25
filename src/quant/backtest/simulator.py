"""Vectorised trade simulator.

Turns a signal series into an equity curve and a trade log, modelling:

  - Next-bar execution: signal at bar t close → fill at bar t+1 open.
  - Transaction costs: per-share commission charged on both entry and exit.
  - Slippage / spread: half-spread in basis points — longs buy above the open,
    shorts sell below the open (and vice versa on exit).
  - Liquidity cap: position size capped at `liquidity_cap` × bar volume.

Signals: +1 = long,  0 = flat,  -1 = short.
Only one position at a time (no pyramiding in Phase 1).

All cost parameters are explicit — see COST_ASSUMPTIONS.md for sourcing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def simulate(
    prices: pd.DataFrame,
    signals: pd.Series,
    initial_capital: float = 100_000.0,
    commission_per_share: float = 0.005,
    slippage_bps: float = 5.0,
    liquidity_cap: float = 0.10,
) -> tuple[pd.Series, pd.DataFrame]:
    """Simulate a single-instrument strategy.

    Parameters
    ----------
    prices:               OHLCV DataFrame, DatetimeIndex.
                          Required columns: open, high, low, close, volume.
    signals:              {-1, 0, +1} Series, same index as prices.
                          Signal at bar t fills at bar t+1 open.
    initial_capital:      Starting cash in dollars.
    commission_per_share: Flat fee per share on entry and exit.
    slippage_bps:         Half-spread per side in basis points.
                          Long buy: open × (1 + bps/10000).
                          Long sell: open × (1 − bps/10000). Short mirrored.
    liquidity_cap:        Max fraction of bar volume to trade.

    Returns
    -------
    equity_curve : pd.Series  — portfolio value at each bar.
    trade_log    : pd.DataFrame — columns: date, entry_price, exit_price,
                                  shares, gross_pnl, commission, net_pnl.
    """
    missing_cols = {"open", "volume"} - set(prices.columns)
    if missing_cols:
        raise ValueError(f"prices DataFrame missing required columns: {missing_cols}")
    if prices[["open", "volume"]].isnull().any().any():
        raise ValueError("prices contains NaN values in 'open' or 'volume' — cannot simulate")
    invalid_sigs = set(signals.unique()) - {-1, 0, 1}
    if invalid_sigs:
        raise ValueError(
            f"signals contain values outside {{-1, 0, +1}}: {invalid_sigs}. "
            "Clip or discretise model output before passing to simulate()."
        )

    opens = prices["open"].to_numpy(dtype=float)
    volumes = prices["volume"].to_numpy(dtype=float)
    sig = signals.to_numpy(dtype=int)
    dates = prices.index
    n = len(prices)
    slip = slippage_bps / 10_000.0

    cash = float(initial_capital)
    position = 0          # +shares for long, -shares for short
    entry_price = 0.0

    equity = np.empty(n, dtype=float)
    trade_rows: list[dict] = []

    for t in range(n):
        # Mark equity at bar t before acting on any signal.
        equity[t] = cash + position * opens[t]

        if t >= n - 1:
            break  # no bar t+1 to fill on

        target = int(sig[t])
        current_sign = int(np.sign(position))

        if current_sign == target:
            continue

        fill_bar = t + 1

        # ── Close existing position at fill_bar open ───────────────────────
        if position != 0:
            shares_abs = abs(position)
            exit_open = opens[fill_bar]

            if position > 0:
                exit_fill = exit_open * (1.0 - slip)     # selling long at bid
                exit_comm = shares_abs * commission_per_share
                cash += shares_abs * exit_fill - exit_comm
            else:
                exit_fill = exit_open * (1.0 + slip)     # covering short at ask
                exit_comm = shares_abs * commission_per_share
                cash -= shares_abs * exit_fill + exit_comm

            gross = shares_abs * (exit_fill - entry_price) * float(np.sign(position))
            # commission column = round-trip (entry already deducted from cash on open)
            round_trip_comm = shares_abs * commission_per_share * 2
            trade_rows.append({
                "date": dates[fill_bar],
                "entry_price": entry_price,
                "exit_price": exit_fill,
                "shares": shares_abs,
                "gross_pnl": float(gross),
                "commission": float(round_trip_comm),
                "net_pnl": float(gross - round_trip_comm),
            })
            position = 0
            entry_price = 0.0

        # ── Open new position at fill_bar open ─────────────────────────────
        if target != 0:
            entry_open = opens[fill_bar]
            if target > 0:
                entry_fill = entry_open * (1.0 + slip)   # buying long at ask
                max_cap = int(cash / entry_fill) if entry_fill > 0 else 0
            else:
                entry_fill = entry_open * (1.0 - slip)   # selling short at bid
                max_cap = int(cash / entry_fill) if entry_fill > 0 else 0

            max_liq = int(volumes[fill_bar] * liquidity_cap)
            shares = max(0, min(max_cap, max_liq))

            if shares > 0:
                entry_comm = shares * commission_per_share
                if target > 0:
                    cash -= shares * entry_fill + entry_comm   # pay for shares
                else:
                    cash += shares * entry_fill - entry_comm   # receive short proceeds
                position = shares * target
                entry_price = entry_fill

    # Force-close any position still open at the final bar.
    # equity[n-1] was already marked using the final open; recompute it after
    # the slippage/commission costs of the forced exit so the curve is consistent.
    if position != 0:
        last_bar = n - 1
        shares_abs = abs(position)
        exit_open = opens[last_bar]
        if position > 0:
            exit_fill = exit_open * (1.0 - slip)
            exit_comm = shares_abs * commission_per_share
            cash += shares_abs * exit_fill - exit_comm
        else:
            exit_fill = exit_open * (1.0 + slip)
            exit_comm = shares_abs * commission_per_share
            cash -= shares_abs * exit_fill + exit_comm
        gross = shares_abs * (exit_fill - entry_price) * float(np.sign(position))
        round_trip_comm = shares_abs * commission_per_share * 2
        trade_rows.append({
            "date": dates[last_bar],
            "entry_price": entry_price,
            "exit_price": exit_fill,
            "shares": shares_abs,
            "gross_pnl": float(gross),
            "commission": float(round_trip_comm),
            "net_pnl": float(gross - round_trip_comm),
        })
        equity[last_bar] = cash  # update to reflect exit costs

    equity_series = pd.Series(equity, index=dates, name="equity")

    if trade_rows:
        trade_log = pd.DataFrame(trade_rows)
    else:
        trade_log = pd.DataFrame(
            columns=["date", "entry_price", "exit_price", "shares",
                     "gross_pnl", "commission", "net_pnl"]
        )

    return equity_series, trade_log
