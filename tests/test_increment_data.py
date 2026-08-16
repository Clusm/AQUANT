"""Tests for data_update.increment_data — 数据增量更新各分支行为。

特征测试:锁定 freshness gate / inactive 过滤 / stop_check / 指数失败吞掉 /
指数行过滤 等分支。为可测性,increment_data/check_cache_freshness/
get_cache_status 都接受可注入 now(默认 pd.Timestamp.now(),向后兼容)。
全部离线:fetch 经 monkeypatch,缓存落盘 tmp。
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.quant import data_update
from tradingagents.quant.data import cache as cm


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CACHE_DIR", tmp_path)


def _mk_daily(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["stock_code", "trade_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    return df


def _make_bulk(rows):
    """返回 (fake_fetch_bulk, calls)。rows: [{stock_code, trade_date, ...}]。"""
    calls = []
    recs = {r["stock_code"]: r for r in rows}

    def bulk(codes, start, end, **kwargs):
        calls.append((list(codes), start, end, kwargs))
        inc = pd.DataFrame([recs[c] for c in codes if c in recs])
        failed = [c for c in codes if c not in recs]
        return inc, failed
    return bulk, calls


def _make_index(rows):
    calls = []

    def idx(symbol="sh000001", datalen=30):
        calls.append((symbol, datalen))
        return pd.DataFrame(rows)
    return idx, calls


def _no_index():
    return lambda *a, **k: pd.DataFrame()


def _new_row(code, date_str):
    return {"stock_code": code, "trade_date": pd.Timestamp(date_str).normalize(),
            "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2, "volume": 1000}


# ── freshness gate ──

def test_freshness_gate_up_to_date(monkeypatch):
    now = pd.Timestamp("2026-08-07")
    daily = _mk_daily([("A", now)])
    bulk, bulk_calls = _make_bulk([_new_row("A", "2026-08-07")])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert bulk_calls == []
    assert "缓存已最新" in msg


def test_empty_cache_returns_clear_message(monkeypatch):
    # 空缓存(parquet 存在但 0 行)时增量无从谈起,应返回清晰提示而非
    # NaT.strftime 抛 ValueError
    now = pd.Timestamp("2026-08-10")
    bulk, bulk_calls = _make_bulk([])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    monkeypatch.setattr(data_update, "fetch_index_sina", _no_index())

    out_daily, out_idx, msg = data_update.increment_data(
        pd.DataFrame(columns=["stock_code", "trade_date"]), pd.DataFrame(),
        "daily_x", "idx_x", now=now)

    assert bulk_calls == []
    assert "缓存为空" in msg


def test_freshness_gate_weekend_leeway(monkeypatch):
    now = pd.Timestamp("2026-08-08")  # 周六
    assert now.weekday() >= 5
    daily = _mk_daily([("A", "2026-08-07")])  # 落后 1 天,周末 ≤2 宽容
    bulk, bulk_calls = _make_bulk([])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert bulk_calls == []
    assert "缓存已最新" in msg


def test_proceeds_when_behind_weekday(monkeypatch):
    now = pd.Timestamp("2026-08-10")  # 周一
    assert now.weekday() < 5
    daily = _mk_daily([("A", "2026-08-07")])  # 落后 3 天
    bulk, bulk_calls = _make_bulk([_new_row("A", "2026-08-10")])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    idx, idx_calls = _make_index([{"trade_date": pd.Timestamp("2026-08-10").normalize()}])
    monkeypatch.setattr(data_update, "fetch_index_sina", idx)

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert len(bulk_calls) == 1
    assert bulk_calls[0][0] == ["A"]
    assert len(idx_calls) == 1


# ── universe / inactive 过滤 ──

def test_inactive_skip_excluded(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([
        ("A", "2026-08-07"),  # active
        ("B", "2026-06-20"),  # 超过 30 天无成交 → inactive
    ])
    bulk, bulk_calls = _make_bulk([])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    monkeypatch.setattr(data_update, "fetch_index_sina", _no_index())

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert bulk_calls[0][0] == ["A"]
    assert "跳过" in msg


def test_codes_param_restricts_universe(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([("A", "2026-08-07"), ("B", "2026-08-07")])
    bulk, bulk_calls = _make_bulk([])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    monkeypatch.setattr(data_update, "fetch_index_sina", _no_index())

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now, codes=["B"])

    assert bulk_calls[0][0] == ["B"]
    assert "仅 universe" in msg


# ── stop_check / 指数 ──

def test_stop_check_skips_index_update(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([("A", "2026-08-07")])
    bulk, bulk_calls = _make_bulk([_new_row("A", "2026-08-10")])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    idx, idx_calls = _make_index([])
    monkeypatch.setattr(data_update, "fetch_index_sina", idx)

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x",
        now=now, stop_check=lambda: True)

    assert len(bulk_calls) == 1
    assert idx_calls == []
    assert "停止" in msg


def test_index_failure_swallowed(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([("A", "2026-08-07")])
    bulk, _ = _make_bulk([_new_row("A", "2026-08-10")])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)

    def boom(*a, **k):
        raise RuntimeError("idx down")
    monkeypatch.setattr(data_update, "fetch_index_sina", boom)

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert "指数拉取失败" in msg


def test_index_rows_before_last_date_filtered(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([("A", "2026-08-07")])
    bulk, _ = _make_bulk([_new_row("A", "2026-08-10")])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    idx_rows = pd.DataFrame([
        {"trade_date": pd.Timestamp("2026-08-07").normalize(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"trade_date": pd.Timestamp("2026-08-10").normalize(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ])
    monkeypatch.setattr(data_update, "fetch_index_sina", lambda *a, **k: idx_rows)

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert len(out_idx) == 1
    assert out_idx["trade_date"].iloc[0] == pd.Timestamp("2026-08-10").normalize()


# ── 落盘 / 空数据 ──

def test_daily_df_merged_via_cache(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([("A", "2026-08-07")])
    cm.save("daily_x", daily)  # 模拟既有缓存
    bulk, _ = _make_bulk([_new_row("A", "2026-08-10")])
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    monkeypatch.setattr(data_update, "fetch_index_sina", _no_index())

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert len(out_daily) == 2  # 既有行 + 新行
    assert len(cm.load("daily_x")) == 2


def test_no_new_rows_warning(monkeypatch):
    now = pd.Timestamp("2026-08-10")
    daily = _mk_daily([("A", "2026-08-07")])
    bulk, _ = _make_bulk([])  # 无新数据
    monkeypatch.setattr(data_update, "fetch_bulk_incremental_sina", bulk)
    monkeypatch.setattr(data_update, "fetch_index_sina", _no_index())

    out_daily, out_idx, msg = data_update.increment_data(
        daily, pd.DataFrame(), "daily_x", "idx_x", now=now)

    assert "警告" in msg
    assert len(out_daily) == len(daily)


# ── 状态/新鲜度 ──

def test_check_cache_freshness():
    daily = _mk_daily([("A", "2026-08-07")])
    last_date, days_behind = data_update.check_cache_freshness(
        daily, now=pd.Timestamp("2026-08-10"))
    assert last_date == pd.Timestamp("2026-08-07").normalize()
    assert days_behind == 3


def test_get_cache_status_needs_update():
    daily = _mk_daily([("A", "2026-08-07")])
    st = data_update.get_cache_status(daily, now=pd.Timestamp("2026-08-10"))  # 周一
    assert st["days_behind"] == 3
    assert st["n_stocks"] == 1
    assert st["needs_update"] is True


def test_get_cache_status_weekend_grace():
    daily = _mk_daily([("A", "2026-08-07")])
    st = data_update.get_cache_status(daily, now=pd.Timestamp("2026-08-08"))  # 周六,落后 1 天
    assert st["days_behind"] == 1
    assert st["needs_update"] is False
