"""Unit tests for the C2-M1 Alpaca paper hello-world (``scripts/c2_hello_world.py``).

The hello-world's pure builders (``build_paper_client`` / ``build_hello_order`` /
``summarize_account``) are asserted directly; the network adapter
(``run_hello_world``) is exercised against a fake client so the order-path logic
— submit, status reporting, best-effort cleanup, dry-run — is covered without
touching the live paper API (METHODOLOGY §15). The single *live* order is the
``__main__`` evidence captured in ``docs/concepts/lean-setup.md``, not a CI test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

# Load the script as a module without making ``scripts/`` a package — the same
# importlib pattern as tests/test_monitor_freshness.py + tests/test_phase4a_runner.py.
_HELLO_PATH = Path(__file__).resolve().parent.parent / "scripts" / "c2_hello_world.py"
_spec = importlib.util.spec_from_file_location("c2_hello_world", _HELLO_PATH)
assert _spec is not None and _spec.loader is not None
hw = importlib.util.module_from_spec(_spec)
sys.modules["c2_hello_world"] = hw
_spec.loader.exec_module(hw)


# ─── Fakes ──────────────────────────────────────────────────────────────────────


class _FakeAccount:
    account_number = "PA_TEST_0001"
    status = "AccountStatus.ACTIVE"
    cash = "1000000"
    buying_power = "4000000"
    equity = "1000000"


class _FakeClock:
    def __init__(self, is_open: bool) -> None:
        self.is_open = is_open


class _FakeOrder:
    def __init__(self, status: str = "accepted") -> None:
        self.id = "ord_abc123"
        self.symbol = "SPY"
        self.qty = "1"
        self.side = "OrderSide.BUY"
        self.status = status


class _FakeClient:
    """Records submitted orders and cancellations; never hits the network."""

    def __init__(self, *, is_open: bool = False, order_status: str = "accepted") -> None:
        self._is_open = is_open
        self._order_status = order_status
        self.submitted: list[MarketOrderRequest] = []
        self.cancelled: list[str] = []
        self.cancel_raises = False

    def get_account(self) -> _FakeAccount:
        return _FakeAccount()

    def get_clock(self) -> _FakeClock:
        return _FakeClock(self._is_open)

    def submit_order(self, order: MarketOrderRequest) -> _FakeOrder:
        self.submitted.append(order)
        return _FakeOrder(status=self._order_status)

    def cancel_order_by_id(self, order_id: str) -> None:
        if self.cancel_raises:
            raise RuntimeError("order already filled")
        self.cancelled.append(order_id)


# ─── Pure builders ────────────────────────────────────────────────────────────


def test_build_hello_order_is_a_market_day_buy():
    order = hw.build_hello_order()
    assert isinstance(order, MarketOrderRequest)
    assert order.symbol == "SPY"
    assert order.qty == 1.0
    assert order.side == OrderSide.BUY
    assert order.time_in_force == TimeInForce.DAY


def test_build_hello_order_respects_overrides():
    order = hw.build_hello_order(symbol="AAPL", qty=3)
    assert order.symbol == "AAPL"
    assert order.qty == 3.0
    assert order.side == OrderSide.BUY  # always a buy — placeholder, not a strategy


def test_summarize_account_projects_fields():
    summary = hw.summarize_account(_FakeAccount())
    assert summary.account_number == "PA_TEST_0001"
    assert summary.status == "AccountStatus.ACTIVE"
    assert summary.cash == "1000000"
    assert summary.equity == "1000000"
    assert summary.buying_power == "4000000"


def test_build_paper_client_pins_paper_endpoint(monkeypatch):
    captured = {}

    def fake_ctor(api_key, secret_key, paper):
        captured.update(api_key=api_key, secret_key=secret_key, paper=paper)
        return object()

    monkeypatch.setattr(hw, "TradingClient", fake_ctor)
    hw.build_paper_client("KEY", "SECRET")
    assert captured == {"api_key": "KEY", "secret_key": "SECRET", "paper": True}


# ─── run_hello_world orchestration ───────────────────────────────────────────


def test_run_hello_world_submits_and_cleans_up_open_order():
    client = _FakeClient(is_open=False, order_status="accepted")
    result = hw.run_hello_world(client, cleanup=True)

    assert len(client.submitted) == 1
    assert result["order"]["submitted"] is True
    assert result["order"]["id"] == "ord_abc123"
    assert result["order"]["status"] == "accepted"
    # accepted is a cancelable status → cleanup cancels the resting order
    assert client.cancelled == ["ord_abc123"]
    assert result["cleanup"]["cancelled"] is True
    assert result["market_open"] is False
    assert result["account"]["account_number"] == "PA_TEST_0001"


def test_run_hello_world_skips_cleanup_when_filled():
    # A fill moves the order out of the cancelable set → no cancel attempt.
    client = _FakeClient(is_open=True, order_status="filled")
    result = hw.run_hello_world(client, cleanup=True)

    assert len(client.submitted) == 1
    assert client.cancelled == []
    assert result["cleanup"]["cancelled"] is False


def test_run_hello_world_no_cleanup_leaves_order_resting():
    client = _FakeClient(order_status="accepted")
    result = hw.run_hello_world(client, cleanup=False)

    assert len(client.submitted) == 1
    assert client.cancelled == []
    assert result["cleanup"]["cancelled"] is False


def test_run_hello_world_cleanup_failure_is_reported_not_raised():
    client = _FakeClient(order_status="accepted")
    client.cancel_raises = True
    result = hw.run_hello_world(client, cleanup=True)

    assert result["order"]["submitted"] is True
    assert result["cleanup"]["cancelled"] is False
    assert result["cleanup"]["reason"] == "RuntimeError"


def test_run_hello_world_dry_run_places_no_order():
    client = _FakeClient()
    result = hw.run_hello_world(client, dry_run=True)

    assert client.submitted == []
    assert result["order"] == {"submitted": False, "reason": "dry-run"}


def test_run_hello_world_tolerates_clock_failure():
    client = _FakeClient(order_status="accepted")

    def boom():
        raise RuntimeError("clock unavailable")

    client.get_clock = boom  # type: ignore[assignment]
    result = hw.run_hello_world(client, cleanup=False)
    # clock is informational — its failure must not block the order
    assert result["market_open"] is None
    assert result["order"]["submitted"] is True


def test_format_renders_submitted_order():
    client = _FakeClient(order_status="accepted")
    text = hw._format(hw.run_hello_world(client, cleanup=False))
    assert "alpaca-paper" in text
    assert "PA_TEST_0001" in text
    assert "order id        : ord_abc123" in text


def test_format_renders_dry_run():
    text = hw._format(hw.run_hello_world(_FakeClient(), dry_run=True))
    assert "not submitted" in text


# ─── main() entrypoint (argv parsing, no network) ────────────────────────────


def test_main_dry_run_parses_args_and_places_no_order(monkeypatch, capsys):
    client = _FakeClient()
    monkeypatch.setattr(hw, "build_paper_client", lambda api_key, secret_key: client)
    rc = hw.main(["--dry-run"])
    assert rc == 0
    assert client.submitted == []
    assert "not submitted" in capsys.readouterr().out


def test_main_default_run_submits_via_fake_client(monkeypatch, capsys):
    client = _FakeClient(order_status="accepted")
    monkeypatch.setattr(hw, "build_paper_client", lambda api_key, secret_key: client)
    rc = hw.main(["--symbol", "AAPL", "--qty", "2", "--no-cleanup"])
    assert rc == 0
    assert len(client.submitted) == 1
    assert client.submitted[0].symbol == "AAPL"
    assert client.submitted[0].qty == 2.0
    assert client.cancelled == []  # --no-cleanup
    assert "order id" in capsys.readouterr().out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
