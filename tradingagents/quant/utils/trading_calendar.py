"""交易日历:基于 akshare 拉取历史交易日,本地缓存。"""
from __future__ import annotations

import pickle
from datetime import date, datetime

import pandas as pd

from tradingagents.quant.config import CACHE_DIR

_CALENDAR_FILE = CACHE_DIR / "trading_calendar.pkl"
_calendar_cache: pd.DatetimeIndex | None = None


def _fetch_calendar() -> pd.DatetimeIndex:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    dates = pd.to_datetime(df["trade_date"])
    return pd.DatetimeIndex(dates.sort_values())


def get_calendar() -> pd.DatetimeIndex:
    global _calendar_cache
    if _calendar_cache is not None:
        return _calendar_cache

    if _CALENDAR_FILE.exists():
        with open(_CALENDAR_FILE, "rb") as f:
            _calendar_cache = pickle.load(f)
        return _calendar_cache

    cal = _fetch_calendar()
    with open(_CALENDAR_FILE, "wb") as f:
        pickle.dump(cal, f)
    _calendar_cache = cal
    return cal


def refresh_calendar() -> pd.DatetimeIndex:
    global _calendar_cache
    _calendar_cache = None
    if _CALENDAR_FILE.exists():
        _CALENDAR_FILE.unlink()
    return get_calendar()


def is_trading_day(dt: datetime | date | str) -> bool:
    cal = get_calendar()
    d = pd.Timestamp(dt)
    return d in cal


def trading_days(start: str, end: str) -> list[pd.Timestamp]:
    cal = get_calendar()
    mask = (cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))
    return cal[mask].tolist()


def next_trading_day(dt: datetime | date | str, n: int = 1) -> pd.Timestamp:
    cal = get_calendar()
    d = pd.Timestamp(dt)
    future = cal[cal > d]
    if len(future) < n:
        raise IndexError(f"no {n} future trading days after {dt}")
    return future[n - 1]


def prev_trading_day(dt: datetime | date | str, n: int = 1) -> pd.Timestamp:
    cal = get_calendar()
    d = pd.Timestamp(dt)
    past = cal[cal < d][::-1]
    if len(past) < n:
        raise IndexError(f"no {n} past trading days before {dt}")
    return past[n - 1]


def align_to_trading_day(dt: datetime | date | str) -> pd.Timestamp:
    d = pd.Timestamp(dt)
    cal = get_calendar()
    if d in cal:
        return d
    future = cal[cal >= d]
    if len(future) > 0:
        return future[0]
    return cal[-1]


if __name__ == "__main__":
    cal = get_calendar()
    print(f"Trading calendar loaded: {len(cal)} days")
    print(f"First: {cal[0].date()}, Last: {cal[-1].date()}")
    print(f"Is 2025-07-11 trading day: {is_trading_day('2025-07-11')}")
    print(f"Next trading day after 2025-07-11: {next_trading_day('2025-07-11').date()}")
