"""Tests for data_update.backfill_stale — 共享分块回补调度器。

设计动机:回补"落后股票"的职责此前散落在两个脚本(一处单突发 456 风险)。
backfill_stale 把它内聚到数据更新层,用依赖注入(fetch_batch/probe/sleep/
stop_check)替代 mock,测试完全离线运行。

注意:backfill_stale 的 target 从 daily_df 的 trade_date.max() 推断,因此测试
数据必须构造"多数股票已到最新日 + 少数落后"以定义 stale(与真实缓存一致)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.quant import data_update, sina_fetcher
from tradingagents.quant.data import cache as cm

TARGET = "2026-08-07"


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """所有测试的 cm.update 落盘到 tmp,不污染真实缓存目录。"""
    monkeypatch.setattr(cm, "CACHE_DIR", tmp_path)


def _mk_daily(rows) -> pd.DataFrame:
    """rows: list of (code, date_str)。构造 stock_code/trade_date 两列。"""
    df = pd.DataFrame(rows, columns=["stock_code", "trade_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    return df


def _mk_inc(codes, date_str=TARGET) -> pd.DataFrame:
    rows = [{
        "stock_code": c,
        "trade_date": pd.Timestamp(date_str).normalize(),
        "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2, "volume": 1000,
    } for c in codes]
    return pd.DataFrame(rows)


def _make_fetch():
    """返回 (fake_fetch_batch, calls)。fake 为请求的每个 code 返回一行 target 数据。"""
    calls = []

    def fetch(codes, start, end, **kwargs):
        calls.append((list(codes), start, end, kwargs))
        return _mk_inc(codes), []
    return fetch, calls


def _noop_sleep(sec):
    pass


def test_stale_detection_and_catch_up():
    daily = _mk_daily([
        ("A", "2026-08-05"),  # 落后 2 天
        ("B", "2026-08-06"),  # 落后 1 天
        ("C", TARGET),        # 最新,定义 target
    ])
    fetch, calls = _make_fetch()
    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", chunk_size=10, fetch_batch=fetch, sleep=_noop_sleep)

    assert remaining == []
    # 只回补落后的 A/B(默认窗口 7 天 → start=target-6)
    assert [c[0] for c in calls] == [["A", "B"]]
    assert [c[1] for c in calls] == ["2026-08-01"]
    assert [c[2] for c in calls] == [TARGET]
    last = merged.groupby("stock_code")["trade_date"].max()
    assert last["A"] == pd.Timestamp(TARGET).normalize()
    assert last["B"] == pd.Timestamp(TARGET).normalize()


def test_empty_stale_short_circuit():
    daily = _mk_daily([("A", TARGET), ("B", TARGET)])
    fetch, calls = _make_fetch()
    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", fetch_batch=fetch, sleep=_noop_sleep)
    assert calls == []
    assert remaining == []
    assert len(merged) == len(daily)


def test_chunking_boundaries():
    codes = [f"{600000 + i}" for i in range(350)]
    daily = _mk_daily([(c, "2026-08-06") for c in codes] + [("999999", TARGET)])
    fetch, calls = _make_fetch()
    sleeps = []
    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", chunk_size=100, cooldown=1.0,
        fetch_batch=fetch, sleep=sleeps.append)

    assert remaining == []
    assert [len(c[0]) for c in calls] == [100, 100, 100, 50]
    # 4 批之间 3 次冷却
    assert sleeps == [1.0, 1.0, 1.0]
    fetched = [c for call in calls for c in call[0]]
    assert sorted(fetched) == sorted(codes)


def test_cooldown_on_probe_false():
    daily = _mk_daily([("A", "2026-08-06"), ("C", TARGET)])
    fetch, calls = _make_fetch()
    sleeps = []
    probe_calls = []
    answers = [False, True]

    def probe():
        probe_calls.append(1)
        return answers.pop(0)

    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", cooldown=5.0,
        fetch_batch=fetch, probe=probe, sleep=sleeps.append)

    assert remaining == []
    # probe 先 False(冷却)后 True,才发起拉取
    assert probe_calls == [1, 1]
    assert len(sleeps) >= 1 and all(s == 5.0 for s in sleeps)
    assert len(calls) == 1


def test_per_chunk_persist_via_real_cache():
    codes = [f"{600000 + i}" for i in range(220)]
    daily = _mk_daily([(c, "2026-08-06") for c in codes] + [("999999", TARGET)])
    cm.save("test_persist", daily)  # 模拟既有历史缓存
    fetch, _ = _make_fetch()
    merged, remaining = data_update.backfill_stale(
        daily, "test_persist", chunk_size=100, fetch_batch=fetch, sleep=_noop_sleep)

    assert remaining == []
    # 每块即时落盘 + 与既有历史 merge:220 只 × (落后日+补回日) + 1 只 target
    on_disk = cm.load("test_persist")
    assert len(on_disk) == len(codes) * 2 + 1
    assert on_disk["stock_code"].nunique() == len(codes) + 1
    assert len(merged) == len(on_disk)
    assert on_disk[on_disk["trade_date"] == pd.Timestamp(TARGET)].shape[0] == len(codes) + 1


def test_stop_check_mid_backfill():
    codes = [f"{600000 + i}" for i in range(220)]
    daily = _mk_daily([(c, "2026-08-06") for c in codes] + [("999999", TARGET)])
    calls = []
    stop = [False]

    def fetch(codes, start, end, **kwargs):
        calls.append((list(codes), start, end, kwargs))
        stop[0] = True  # 首批拉取后请求停止
        return _mk_inc(codes), []

    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", chunk_size=100,
        fetch_batch=fetch, sleep=_noop_sleep, stop_check=lambda: stop[0])

    assert len(calls) == 1
    assert len(remaining) == 120  # 220 - 100,未拉取的仍落后


def test_passes_shares_close_maps():
    daily = pd.DataFrame({
        "stock_code": ["A", "B", "C"],
        "trade_date": pd.to_datetime(["2026-08-05", "2026-08-05", TARGET]).normalize(),
        "outstanding_share": [100.0, 200.0, 300.0],
        "close": [10.0, 20.0, 30.0],
    })
    fetch, calls = _make_fetch()
    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", fetch_batch=fetch, sleep=_noop_sleep)

    assert remaining == []
    kw = calls[0][3]
    assert kw["last_shares_map"] == {"A": 100.0, "B": 200.0, "C": 300.0}
    assert kw["last_close_map"] == {"A": 10.0, "B": 20.0, "C": 30.0}


@pytest.mark.parametrize("window_days,expected_start", [
    (7, "2026-08-01"),  # target - 6
    (3, "2026-08-05"),  # target - 2
])
def test_window_bounds(window_days, expected_start):
    daily = _mk_daily([("A", "2026-08-06"), ("C", TARGET)])
    fetch, calls = _make_fetch()
    data_update.backfill_stale(
        daily, "test_cache", window_days=window_days,
        fetch_batch=fetch, sleep=_noop_sleep)
    assert calls[0][1] == expected_start
    assert calls[0][2] == TARGET


def test_progress_callback_reports_remaining():
    codes = [f"{600000 + i}" for i in range(250)]
    daily = _mk_daily([(c, "2026-08-06") for c in codes] + [("999999", TARGET)])
    fetch, _ = _make_fetch()
    events = []
    data_update.backfill_stale(
        daily, "test_cache", chunk_size=100,
        fetch_batch=fetch, sleep=_noop_sleep,
        progress_callback=lambda done, total, stats: events.append((done, total, stats)))

    assert events[0][1] == 250  # total == 初始 stale 数
    assert [e[0] for e in events] == [100, 200, 250]  # done = 已完成数
    assert [e[2]["remaining"] for e in events] == [150, 50, 0]  # 单调递减
    assert all(e[2]["failed"] == 0 for e in events)


def test_default_fetch_batch_delegates(monkeypatch):
    daily = _mk_daily([("A", "2026-08-06"), ("C", TARGET)])
    real_calls = []

    def fake_bulk(codes, start, end, **kwargs):
        real_calls.append((list(codes), start, end))
        return _mk_inc(codes), []

    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", fake_bulk)
    merged, remaining = data_update.backfill_stale(
        daily, "test_cache", sleep=_noop_sleep)
    assert remaining == []
    assert real_calls == [(["A"], "2026-08-01", TARGET)]


def test_probe_sina_available_200(monkeypatch):
    class R:
        status_code = 200
    monkeypatch.setattr(sina_fetcher.requests, "get", lambda *a, **k: R())
    assert sina_fetcher.probe_sina_available() is True


def test_probe_sina_available_non200_or_error(monkeypatch):
    class R:
        status_code = 456
    monkeypatch.setattr(sina_fetcher.requests, "get", lambda *a, **k: R())
    assert sina_fetcher.probe_sina_available() is False

    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(sina_fetcher.requests, "get", boom)
    assert sina_fetcher.probe_sina_available() is False
