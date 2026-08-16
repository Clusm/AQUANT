"""事件模板库: 复用 4 个事件策略的触发逻辑, 预计算事件池并磁盘缓存。

事件池 = {trade_date: DataFrame[stock_code, <5 个事件评分因子>]}, 即各生产类
_precompute_signals 产出的 _eligible_by_date。Track A 策略在事件池内做因子精筛。

缓存: CACHE_DIR/event_pools/<event_type>__<params_fp>__<data_fp>.parquet
(parquet 存 date+stock_code+score_cols; date 用 str 存, 读时转 Timestamp)

data_fp 是日线数据的内容指纹(日期范围/行数/股票数/收盘价总和)。缓存路径
同时绑定策略参数与底层日线数据,增量更新或换缓存后自动失效,避免旧事件池
被新数据误命中(前视/陈旧信号)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from tradingagents.quant import config
from tradingagents.quant.strategy.leader_pullback_bounce import (
    LeaderPullbackBounceStrategy,
)
from tradingagents.quant.strategy.monthly_macd_golden_cross import (
    MonthlyMacdGoldenCrossStrategy,
)
from tradingagents.quant.strategy.monthly_weekly_daily_resonance import (
    MonthlyWeeklyDailyResonanceStrategy,
)
from tradingagents.quant.strategy.weekly_macd_golden_cross import (
    WeeklyMacdGoldenCrossStrategy,
)

EVENT_CLASSES: dict[str, type] = {
    "monthly_macd_golden_cross": MonthlyMacdGoldenCrossStrategy,
    "weekly_macd_golden_cross": WeeklyMacdGoldenCrossStrategy,
    "monthly_weekly_daily_resonance": MonthlyWeeklyDailyResonanceStrategy,
    "leader_pullback_bounce": LeaderPullbackBounceStrategy,
}


def _pool_dir(pool_dir: Path | None = None) -> Path:
    """事件池缓存目录(延迟解析,避免 import 时锁死 QUANT_CACHE_DIR)。"""
    root = Path(pool_dir) if pool_dir is not None else Path(config.CACHE_DIR)
    d = root / "event_pools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _params_fp(params: dict) -> str:
    """策略参数指纹(非安全场景,仅用于本地缓存文件命名)。"""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]  # nosec B324 - 本地缓存命名指纹,非安全哈希


def _data_fp(daily_df: pd.DataFrame) -> str:
    """日线数据内容指纹:日期范围 + 行数 + 股票数 + 收盘价总和。

    正常路径的 close.sum() 是轻量且确定的内容摘要;极端缺列时回退到
    pandas 对象哈希(截取前 2000 行避免拖慢 worker 启动)。
    """
    try:
        start = pd.Timestamp(daily_df["trade_date"].min()).normalize()
        end = pd.Timestamp(daily_df["trade_date"].max()).normalize()
        n_stocks = int(daily_df["stock_code"].nunique())
        close_sum = float(daily_df["close"].sum())
        payload = f"{start.date()}|{end.date()}|{len(daily_df)}|{n_stocks}|{close_sum:.6f}"
    except Exception:
        head = daily_df[["trade_date", "stock_code", "close"]].head(2000)
        digest = int(pd.util.hash_pandas_object(head, index=False).sum())
        payload = f"fallback|{len(daily_df)}|{digest}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]  # nosec B324 - 本地缓存命名指纹,非安全哈希


def _load_pool(path: Path) -> dict[pd.Timestamp, pd.DataFrame]:
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    pool: dict[pd.Timestamp, pd.DataFrame] = {}
    for date, grp in df.groupby("trade_date", sort=False):
        pool[pd.Timestamp(date)] = grp.drop(columns=["trade_date"]).reset_index(drop=True)
    return pool


def get_event_pool(event_type: str, daily_df: pd.DataFrame,
                   params: dict | None = None,
                   pool_dir: Path | None = None) -> dict[pd.Timestamp, pd.DataFrame]:
    """返回 {Timestamp: DataFrame[stock_code, score_cols]}。磁盘缓存, 幂等。

    pool_dir 仅用于测试注入;生产环境默认从 QUANT_CACHE_DIR 解析。
    """
    params = params or {}
    if event_type not in EVENT_CLASSES:
        raise KeyError(f"未知事件类型 {event_type}, 可选 {sorted(EVENT_CLASSES)}")

    params_fp = _params_fp(params)
    data_fp = _data_fp(daily_df)
    directory = _pool_dir(pool_dir)
    cache_path = directory / f"{event_type}__{params_fp}__{data_fp}.parquet"
    if cache_path.exists():
        return _load_pool(cache_path)

    cls = EVENT_CLASSES[event_type]
    strat = cls(**params)
    strat._precompute_features(daily_df)  # 触发 _precompute_signals, 填充 _eligible_by_date
    pool = strat._eligible_by_date or {}
    if pool:
        rows = []
        for date, grp in pool.items():
            g = grp.copy()
            g["trade_date"] = pd.Timestamp(date)
            rows.append(g)
        out = pd.concat(rows, ignore_index=True)
        out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
        out.to_parquet(cache_path, index=False)
        meta = {
            "event_type": event_type,
            "params_fp": params_fp,
            "data_fp": data_fp,
            "n_dates": len(pool),
            "n_rows": len(out),
        }
        cache_path.with_suffix(".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return pool
