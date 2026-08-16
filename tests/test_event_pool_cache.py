"""event_templates 事件池缓存的数据指纹测试。

回归点:事件池此前只按策略参数命名缓存文件,底层日线增量更新后仍会命中
旧事件池,导致 factor_ranked_event 使用陈旧信号。现在缓存 key 同时包含
params_fp 与 data_fp(日期范围/行数/股票数/close 总和)。
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.quant.strategy import event_templates
from tradingagents.quant.strategy.event_templates import get_event_pool


class _FakeEventStrategy:
    instances: int = 0

    def __init__(self, **params):
        self.params = params
        _FakeEventStrategy.instances += 1
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}

    def _precompute_features(self, daily_df: pd.DataFrame) -> None:
        last_date = pd.Timestamp(daily_df["trade_date"].max()).normalize()
        self._eligible_by_date = {
            last_date: pd.DataFrame(
                [{"stock_code": "600000", "score": 1.5}],
            )
        }


def _mk_daily(days: int = 3, close: float = 10.0) -> pd.DataFrame:
    rows = []
    for i in range(days):
        rows.append({
            "stock_code": "600000",
            "trade_date": pd.Timestamp("2026-08-01") + pd.Timedelta(days=i),
            "close": close + i,
        })
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _fake_event_class(monkeypatch):
    _FakeEventStrategy.instances = 0
    monkeypatch.setitem(
        event_templates.EVENT_CLASSES,
        "fake_event",
        _FakeEventStrategy,
    )
    yield


def test_data_fp_changes_with_new_rows_and_close():
    base = _mk_daily(days=3, close=10.0)
    fp = event_templates._data_fp(base)

    longer = _mk_daily(days=4, close=10.0)
    assert event_templates._data_fp(longer) != fp

    changed_close = _mk_daily(days=3, close=11.0)
    assert event_templates._data_fp(changed_close) != fp


def test_cache_key_binds_underlying_data(tmp_path):
    base = _mk_daily(days=3)
    pool1 = get_event_pool("fake_event", base, pool_dir=tmp_path)
    assert _FakeEventStrategy.instances == 1
    assert len(pool1) == 1

    # 完全相同的 DataFrame 命中磁盘缓存,不再实例化策略
    base2 = _mk_daily(days=3)
    assert event_templates._data_fp(base2) == event_templates._data_fp(base)
    pool2 = get_event_pool("fake_event", base2, pool_dir=tmp_path)
    assert _FakeEventStrategy.instances == 1
    assert len(pool2) == 1


def test_changed_data_invalidates_cache(tmp_path):
    get_event_pool("fake_event", _mk_daily(days=3), pool_dir=tmp_path)
    assert _FakeEventStrategy.instances == 1

    # 新增一个交易日:data_fp 变化,必须重算而不是命中旧缓存
    pool = get_event_pool("fake_event", _mk_daily(days=4), pool_dir=tmp_path)
    assert _FakeEventStrategy.instances == 2
    assert len(pool) == 1


def test_unknown_event_raises(tmp_path):
    with pytest.raises(KeyError):
        get_event_pool("does_not_exist", _mk_daily(), pool_dir=tmp_path)
