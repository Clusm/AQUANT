"""Tests for cache.update — merge key 推断 + dedup 行为。

特征测试:锁定 on=None 自动推断(优先 [trade_date,stock_code],回退
[stock_code],无公共 key 报错)与 dedup keep="last" 语义。走真实 cm 与
tmp_path 缓存,不 mock。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from tradingagents.quant.data import cache as cm

D = "2026-08-07"


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CACHE_DIR", tmp_path)


def _df(codes, dates, **cols) -> pd.DataFrame:
    df = pd.DataFrame({
        "stock_code": list(codes),
        "trade_date": pd.to_datetime(list(dates)).normalize(),
    })
    for k, v in cols.items():
        df[k] = v
    return df


def test_update_missing_cache_saves():
    df = _df(["A"], [D])
    out = cm.update("new_cache", df)
    assert len(out) == 1
    assert cm.exists("new_cache")
    assert len(cm.load("new_cache")) == 1


def test_auto_detect_prefers_trade_date_stock_code():
    cm.save("c1", _df(["A"], [D], close=[1.0]))
    out = cm.update("c1", _df(["A", "A"], [D, "2026-08-08"], close=[2.0, 3.0]))
    # on 推断为 [trade_date, stock_code]:(A,08-07) 新旧两行 dedup 保留 last=2.0
    assert len(out) == 2
    r = out[out["trade_date"] == pd.Timestamp(D).normalize()]
    assert r["close"].iloc[0] == 2.0


def test_auto_detect_falls_back_stock_code():
    cm.save("c2", _df(["A"], [D], other=[1.0]))
    df2 = _df(["A"], [D], other=[9.0]).drop(columns=["trade_date"])
    out = cm.update("c2", df2)
    # 无 trade_date → 回退 [stock_code],同 code 新旧行 dedup 保留新行
    assert len(out) == 1
    assert out["other"].iloc[0] == 9.0


def test_auto_detect_raises_no_key():
    cm.save("c3", pd.DataFrame({"foo": [1]}))
    with pytest.raises(ValueError, match="on="):
        cm.update("c3", pd.DataFrame({"bar": [2]}))


def test_dedup_keep_last():
    cm.save("c4", _df(["A"], [D], close=[1.0]))
    out = cm.update("c4", _df(["A"], [D], close=[5.0]), on=["stock_code", "trade_date"])
    assert len(out) == 1
    assert out["close"].iloc[0] == 5.0


def test_explicit_on_respected():
    cm.save("c5", _df(["A", "B"], [D, D]))
    df2 = _df(["A", "B"], [D, D], v=[1, 1])
    # 显式 on=["trade_date"] 覆盖推断:两股同日 → 只保留 1 行
    out = cm.update("c5", df2, on=["trade_date"])
    assert len(out) == 1


def test_repeated_update_is_idempotent_no_duplicate_rows():
    cm.save("idem", _df(["A"], [D], close=[1.0]))
    new = _df(["A"], [D], close=[2.0])
    for _ in range(3):
        cm.update("idem", new, on=["stock_code", "trade_date"])
    out = cm.load("idem")
    assert len(out) == 1
    assert out["close"].iloc[0] == 2.0


def test_concurrent_updates_do_not_lose_rows():
    """并发 read-old/merge/write 也必须串行:最终应包含所有唯一 key。"""
    cm.save("conc", pd.DataFrame(columns=["stock_code", "trade_date"]))
    dates = [f"2026-08-{day:02d}" for day in range(1, 9)]

    def write(offset: int):
        codes = [f"{600000 + offset * 100 + i}" for i in range(8)]
        cm.update("conc", _df(codes, dates, close=[float(i) for i in range(8)]))

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(write, range(4)))

    out = cm.load("conc")
    assert len(out) == 32
    assert out.duplicated(subset=["stock_code", "trade_date"]).sum() == 0


def test_atomic_write_leaves_no_tmp_files():
    df = _df(["A"], [D], close=[1.0])
    cm.save("atomic", df)
    cm.update("atomic", df, on=["stock_code", "trade_date"])
    leftovers = [p for p in cm.CACHE_DIR.glob("*.parquet") if ".tmp" in p.name]
    assert leftovers == []


def test_update_with_empty_new_df_short_circuits():
    """空增量不再 concat,避免 pandas FutureWarning 且结果保持原样。"""
    old = _df(["A"], [D], close=[1.0])
    cm.save("empty_new", old)
    out = cm.update("empty_new", pd.DataFrame(), on=["stock_code", "trade_date"])
    assert out.equals(old)
    assert cm.load("empty_new").equals(old)
