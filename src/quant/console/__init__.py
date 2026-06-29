"""Console service layer (Project E1).

A pure-Python read layer over the platform's *existing* artifacts — per-arm
backtest checkpoints (``metadata.json`` + ``oos_returns.parquet``),
``data/ledger.yaml``, ``features/catalog.yaml``, the C6 strategy registry
(``execution/strategy_registry.yaml``), and the DuckDB/parquet lake.
Every reader returns a frozen view-model dataclass (see :mod:`quant.console.viewmodels`)
and an idempotent :mod:`quant.console.export` step serialises the view-models to
``src/quant/console/export/*.json`` for the static React frontend (E1-M2+).

Design contract (PRD ``docs/project-e/E1-research-trust-console.prd.md`` §4):

* **No business logic lives outside this layer.** The frontend renders; it does
  not compute. The export script and the future FastAPI service (E2) call the
  *same* readers, so the migration is a data-source swap, not a rewrite.
* **No new datastore.** Readers depend only on ``storage/`` + ``features/`` +
  the checkpoint files already produced by the runners.
* **Readers take injectable sources** (:class:`quant.console.sources.ConsoleSources`)
  so unit tests run on synthetic fixtures with no real (gitignored) data present.
"""
from __future__ import annotations

from quant.console import readers, schemas, sources, viewmodels

__all__ = ["readers", "schemas", "sources", "viewmodels"]
