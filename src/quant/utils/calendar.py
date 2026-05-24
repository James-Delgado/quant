"""Trading-calendar helpers — so the pipeline knows which days to expect data.

Used to detect gaps (a missing trading day means an ingest silently failed)
and, later, to align features to actual sessions.
"""
from __future__ import annotations

import datetime as dt


def trading_days(start: dt.date, end: dt.date, exchange: str = "NYSE") -> list[dt.date]:
    """All market sessions in [start, end] for the given exchange calendar."""
    import pandas_market_calendars as mcal

    schedule = mcal.get_calendar(exchange).schedule(start_date=start, end_date=end)
    return [ts.date() for ts in schedule.index]


def is_trading_day(day: dt.date, exchange: str = "NYSE") -> bool:
    return day in set(trading_days(day, day, exchange))


def last_trading_day(reference: dt.date | None = None, exchange: str = "NYSE") -> dt.date:
    """Most recent session on or before `reference` (defaults to today)."""
    reference = reference or dt.date.today()
    window = trading_days(reference - dt.timedelta(days=10), reference, exchange)
    return window[-1]
