"""Execution layer: model forecast → target position → paper-broker order (C2).

The ``lean_bridge`` module is the broker-agnostic ``ExecutionBridge`` boundary
that turns a daily ARIMA forecast into a paper-account order and the G1
signal-parity gate that proves the bridge emits the *same* decision as the
Phase 1 backtest path. See ``docs/concepts/lean-setup.md`` for the platform
decision (Alpaca paper, the ratified §8.3 fallback) and
``.claude/prds/c2-lean-paper.prd.md`` for the contract.
"""
from quant.execution import lean_bridge

__all__ = ["lean_bridge"]
