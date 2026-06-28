"""C2-M1 execution-platform hello-world — boot the paper engine, place one order.

This is the runnable half of the C2-M1 deliverable; the prose half is
``docs/concepts/lean-setup.md`` (the install runbook + platform decision record).
Together they are the **platform contract** the C2-M2 bridge commits to
(METHODOLOGY §4 — contract before consumer).

Platform decision (recorded in full in ``lean-setup.md``)
---------------------------------------------------------
The ratified plan (ROADMAP §8.3) is *LEAN local first; fall back to the Alpaca
paper adapter if LEAN install friction exceeds 2 days*. LEAN-local was attempted
— Docker (native ``linux/aarch64``) and the LEAN CLI (``pip``/``pipx``) install
cleanly — but ``lean init`` is gated behind a **paid QuantConnect account** (the
"Quant Researcher" seat) for local data/live use. That paywall *is* install
friction within the meaning of §8.3, so this milestone takes the **Alpaca paper**
fallback: pure-Python, zero-Docker, and the credentials already live in ``.env``
(the same keys the project ingests bars with — they resolve to an Alpaca *paper*
account, ``PA…``). The C2-M2 ``ExecutionBridge`` Protocol keeps LEAN a future swap,
not a rewrite.

What this proves (and what it does not)
---------------------------------------
This is the **platform smoke test**: it authenticates against the paper broker,
reads account state, and submits **one** order — proving the
``credentials → client → order → broker ack`` path is live end-to-end. It is
*not* the production bridge: there is no model, no ``build_features(asof)`` wiring,
no signal-parity gate (that is C2-M2's ``ExecutionBridge`` + G1), and no
reconciliation (C2-M3 / G2). The ≥5-cycle liveness loop (G3) is a C2-M3 runbook;
M1 only demonstrates a single clean boot-and-order.

Design — pure builders vs. network adapters
--------------------------------------------
``build_paper_client`` / ``build_hello_order`` / ``summarize_account`` are pure
(no network) and unit-tested in ``tests/test_c2_hello_world.py`` against mocks.
``submit_hello_order`` / ``run_hello_world`` are the thin network adapters that
touch the live paper API; they are exercised by the ``__main__`` run whose output
is captured as the M1 evidence in ``lean-setup.md``.

Run
---
    .venv/bin/python scripts/c2_hello_world.py            # places 1 share SPY (paper)
    .venv/bin/python scripts/c2_hello_world.py --dry-run  # account summary only, no order
    .venv/bin/python scripts/c2_hello_world.py --no-cleanup  # leave the order resting
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

# ─── Pinned hello-world order (METHODOLOGY §1 — a fixed, trivial placeholder) ────
# SPY is in the project universe (config.equity_universe) and liquid; 1 share is
# immaterial against the $1M paper account. This order carries no strategy
# meaning — its only job is to drive the order path. The C2-M2 placeholder maps
# an ARIMA sign to a target position; that mapping is pinned there, not here.
HELLO_SYMBOL = "SPY"
HELLO_QTY = 1.0

# Order statuses Alpaca considers open / still cancelable. A market order placed
# while the market is closed rests in one of these until the next open; a fill
# moves it out of the set and cleanup is skipped (nothing to cancel).
_CANCELABLE_STATUSES = frozenset(
    {"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding"}
)


@dataclass(frozen=True)
class AccountSummary:
    """The read-only paper-account fields the hello-world reports."""

    account_number: str
    status: str
    cash: str
    buying_power: str
    equity: str


def build_paper_client(api_key: str, secret_key: str) -> TradingClient:
    """Construct an Alpaca **paper** trading client.

    ``paper=True`` pins the ``paper-api.alpaca.markets`` endpoint — there is no
    live-trading code path in C2 (paper only, ROADMAP §7; live is gated on the
    C2-M3 reconciliation pass per Phase 4 Sub-track B).
    """
    return TradingClient(api_key, secret_key, paper=True)


def summarize_account(account: object) -> AccountSummary:
    """Project the Alpaca account model onto the fields we report. Pure."""
    return AccountSummary(
        account_number=str(getattr(account, "account_number", "")),
        status=str(getattr(account, "status", "")),
        cash=str(getattr(account, "cash", "")),
        buying_power=str(getattr(account, "buying_power", "")),
        equity=str(getattr(account, "equity", "")),
    )


def build_hello_order(symbol: str = HELLO_SYMBOL, qty: float = HELLO_QTY) -> MarketOrderRequest:
    """Build the single hello-world order request. Pure — no submission.

    A market ``DAY`` buy: during RTH it fills; placed while closed, Alpaca rests
    it for the next open (status ``accepted``), which still proves placement.
    """
    return MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )


def submit_hello_order(client: TradingClient, order: MarketOrderRequest) -> object:
    """Submit the order to the paper broker. Thin network adapter."""
    return client.submit_order(order)


def run_hello_world(
    client: TradingClient,
    *,
    symbol: str = HELLO_SYMBOL,
    qty: float = HELLO_QTY,
    dry_run: bool = False,
    cleanup: bool = True,
) -> dict:
    """Boot, summarize the account, and (unless ``dry_run``) place one order.

    Returns a JSON-serializable result dict captured as the M1 evidence. On
    ``cleanup`` the order is cancelled best-effort if still open, so repeated
    runs leave the sandbox as they found it.
    """
    account = client.get_account()
    summary = summarize_account(account)

    try:
        market_open = bool(client.get_clock().is_open)
    except Exception:  # clock is informational only — never block the order on it
        market_open = None

    result: dict = {
        "platform": "alpaca-paper",
        "account": asdict(summary),
        "market_open": market_open,
        "order": None,
        "cleanup": None,
    }
    if dry_run:
        result["order"] = {"submitted": False, "reason": "dry-run"}
        return result

    order = submit_hello_order(client, build_hello_order(symbol, qty))
    order_id = str(getattr(order, "id", ""))
    submitted_status = str(getattr(order, "status", ""))
    result["order"] = {
        "submitted": True,
        "id": order_id,
        "symbol": str(getattr(order, "symbol", symbol)),
        "qty": str(getattr(order, "qty", qty)),
        "side": str(getattr(order, "side", "")),
        "status": submitted_status,
    }

    if cleanup and submitted_status.split(".")[-1].lower() in _CANCELABLE_STATUSES:
        try:
            client.cancel_order_by_id(order_id)
            result["cleanup"] = {"cancelled": True, "order_id": order_id}
        except Exception as exc:  # already filled / not cancelable — report, don't raise
            result["cleanup"] = {"cancelled": False, "reason": type(exc).__name__}
    else:
        result["cleanup"] = {"cancelled": False, "reason": "not cancelable or --no-cleanup"}

    return result


def _format(result: dict) -> str:
    """Human-readable rendering of the result dict for the console / the doc."""
    a = result["account"]
    lines = [
        f"platform        : {result['platform']}",
        f"paper account   : {a['account_number']}  ({a['status']})",
        f"cash / equity   : {a['cash']} / {a['equity']}  (buying_power {a['buying_power']})",
        f"market open     : {result['market_open']}",
    ]
    o = result["order"]
    if o and o.get("submitted"):
        lines += [
            f"order submitted : {o['side']} {o['qty']} {o['symbol']}",
            f"order id        : {o['id']}",
            f"order status    : {o['status']}",
            f"cleanup         : {result['cleanup']}",
        ]
    else:
        lines.append(f"order           : not submitted ({o.get('reason') if o else 'n/a'})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C2-M1 Alpaca paper hello-world.")
    parser.add_argument("--symbol", default=HELLO_SYMBOL, help="ticker to order (default SPY)")
    parser.add_argument("--qty", type=float, default=HELLO_QTY, help="share quantity (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="account summary only; no order")
    parser.add_argument("--no-cleanup", action="store_true", help="leave the order resting")
    args = parser.parse_args(argv)

    # Import settings lazily so the module stays import-side-effect-free (the test
    # suite loads it via importlib without requiring live credentials).
    from quant.config import settings

    client = build_paper_client(settings.alpaca_api_key, settings.alpaca_secret_key)
    result = run_hello_world(
        client,
        symbol=args.symbol,
        qty=args.qty,
        dry_run=args.dry_run,
        cleanup=not args.no_cleanup,
    )
    print(_format(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
