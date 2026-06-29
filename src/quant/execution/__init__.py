"""Execution layer: model forecast → target position → paper-broker order (C2).

The ``lean_bridge`` module is the broker-agnostic ``ExecutionBridge`` boundary
that turns a daily ARIMA forecast into a paper-account order and the G1
signal-parity gate that proves the bridge emits the *same* decision as the
Phase 1 backtest path. See ``docs/concepts/lean-setup.md`` for the platform
decision (Alpaca paper, the ratified §8.3 fallback) and
``.claude/prds/c2-lean-paper.prd.md`` for the contract.

The ``reconciliation`` module is the backtest↔paper reconciliation arithmetic
core (the G2 gate + residual decomposition), shared by the
``scripts/reconcile_paper_backtest.py`` CLI runner and the E3 Live-Monitoring
console (C2-M3-RECON-CORE-LIFT).
"""
from quant.execution import lean_bridge, reconciliation

__all__ = ["lean_bridge", "reconciliation"]
