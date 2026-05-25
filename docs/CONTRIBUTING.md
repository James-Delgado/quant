# Contributing

This is a solo-operator project, but these notes capture the development
conventions so a future agent or collaborator can pick up cleanly.

## Prerequisites

<!-- AUTO-GENERATED from pyproject.toml -->

| Tool | Version | Purpose |
|------|---------|---------|
| Python | ≥ 3.11 | Runtime |
| uv | latest | Fast package installer (`brew install uv`) |

<!-- END AUTO-GENERATED -->

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env    # fill in the four free API keys (see docs/ENV.md)
```

## Dev dependencies

<!-- AUTO-GENERATED from pyproject.toml [project.optional-dependencies.dev] -->

| Package | Purpose |
|---------|---------|
| `pytest>=8` | Test runner |
| `pytest-cov>=5` | Coverage reporting |
| `ruff>=0.5` | Linter and formatter |
| `mypy>=1.10` | Static type checker |
| `ipykernel` + `jupyterlab` | Interactive exploration |

<!-- END AUTO-GENERATED -->

## Running tests

```bash
pytest                                        # unit suite (~15s, no network)
pytest --integration                          # live-API smoke tests
pytest --cov=src --cov-report=term-missing    # with line-level coverage
```

Tests are in `tests/`. The `lake_root` fixture in `conftest.py` redirects all
lake writes to a temporary directory, so unit tests never touch `data/`.
Integration tests are gated behind `@pytest.mark.integration` and skipped
unless `--integration` is passed.

## Code style

```bash
ruff check src/ tests/   # lint
ruff format src/ tests/  # format
mypy src/                # type check
```

Line length is 100 characters (set in `pyproject.toml [tool.ruff]`).

## Adding a new ingestor

1. Copy `src/quant/ingest/alpaca_bars.py` — it is the canonical template.
2. Follow the four-step shape: determine date range → fetch (with retries) →
   land raw immutably → clean and write processed.
3. Add a pandera schema to `src/quant/ingest/schemas.py`.
4. Register the new ingestor in `src/quant/flows/daily.py`.
5. Add unit tests in `tests/test_ingest_<name>.py` following the existing
   pattern (mock factories in `conftest.py`, `lake_root` fixture for isolation).

## Adding a new FRED series

Add the series ID to `fred_series` in `src/quant/config.py`. No other changes
required — the ingestor loops over the list automatically.

## PR checklist

- [ ] `pytest` passes (0 failures, integration tests skipped)
- [ ] `ruff check` and `ruff format --check` pass
- [ ] New code has corresponding tests
- [ ] `docs/` updated if behaviour or setup changed
