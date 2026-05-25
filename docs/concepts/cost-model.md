# Cost Assumptions

Documented defaults for the trade simulator (`src/quant/backtest/simulator.py`).
All parameters are keyword arguments and can be overridden at runtime.

## Commission — `commission_per_share = $0.005`

**Source:** Interactive Brokers IBKR Pro tiered schedule, mid-2024.
Fixed rate: $0.005/share, min $1.00/order.

Applied on both entry and exit (round-trip). The `commission` column in the
trade log is the full round-trip amount: `2 × shares × $0.005`.

For retail brokers (Schwab, Fidelity) that have moved to zero-commission equity
trading, set `commission_per_share=0.0` — but note that payment-for-order-flow
is implicitly absorbed into wider spreads, which the slippage parameter captures.

## Slippage — `slippage_bps = 5.0`

**Source:** Academic literature consensus for liquid US equities; see
López de Prado (2018) *Advances in Financial Machine Learning*, ch. 15.

Modelled as a half-spread: 5 bps per side (10 bps round-trip).

| Direction | Entry fill | Exit fill |
|-----------|-----------|-----------|
| Long      | `open × (1 + 0.0005)` | `open × (1 − 0.0005)` |
| Short     | `open × (1 − 0.0005)` | `open × (1 + 0.0005)` |

5 bps is conservative for large-cap, high-volume names (e.g. SPY, AAPL).
For small-/mid-cap names, 10–25 bps is more realistic; increase `slippage_bps`
accordingly.

## Liquidity Cap — `liquidity_cap = 0.10`

**Source:** Industry rule of thumb to avoid market impact.

Position size is capped at 10% of the fill-bar's reported volume. This prevents
the simulation from implying unrealistically large fills in thin markets.

For highly liquid instruments (index ETFs) this constraint rarely binds.
For thinly traded names, tighten to 0.01–0.05.

## Execution Model

Next-bar execution: a signal generated at bar **t** close fills at bar **t+1**
open. This eliminates look-ahead bias from same-bar fills.

## Adjusting for Different Instruments

| Instrument class     | Suggested overrides                              |
|----------------------|--------------------------------------------------|
| Large-cap US equity  | Defaults are appropriate                        |
| Small-/mid-cap       | `slippage_bps=15`, `liquidity_cap=0.02`         |
| Futures (per-lot)    | Convert to per-contract commission; set `slippage_bps` to half the typical tick spread |
| Crypto (spot)        | `commission_per_share=0.0`, `slippage_bps=10`   |
