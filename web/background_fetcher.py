"""Web app 启动时后台自动增量更新主板全量数据。

设计:
- daemon 线程,Streamlit 启动时触发一次
- daily 缓存(quant_daily_cache_name,默认 daily_main_board 全量主板)已存在
  -> increment_data 增量更新最新交易日(快,几秒到几分钟)
- daily 缓存不存在 -> 跳过,提示用户先手动跑 download_all 构建基础数据
  (全量构建 15-20 分钟,不适合放在启动自动流程里)
- 全局 singleton,防止重复启动
- 状态记录供 UI 查询

并发保护:
- _fetch_lock 串行化 background_fetcher 与 web/runner.py 的 _run_quant
- _run_quant 调 increment_data 前 acquire _fetch_lock,避免与后台增量冲突
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime

import pandas as pd

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant.data import cache as cm

# 与 pick()/quant_picker_node 保持一致:读配置 quant_daily_cache_name
# (默认 daily_main_board 全量主板)。后台自动增量覆盖全部主板股票,
# 流动性/价格筛选只在选股层执行,不再依赖"数据采集层已截断"的 liquid 缓存。
CACHE_NAME = DEFAULT_CONFIG.get("quant_daily_cache_name", "daily_main_board")
INDEX_CACHE_NAME = "index_000001"

_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()  # 串行化 background_fetcher 与 _run_quant 的数据写入
_STARTED = False
_STATE: dict = {
    "status": "idle",       # idle / running / done / error / skip
    "stage": "",            # 增量更新 / 跳过
    "message": "",
    "started_at": None,
    "finished_at": None,
    "progress": 0,          # 0-100
}


def acquire_fetch_lock() -> threading.Lock:
    """让外部模块(web/runner.py)串行访问 increment_data。

    用法:
        from web.background_fetcher import acquire_fetch_lock
        with acquire_fetch_lock():
            increment_data(...)
    """
    return _FETCH_LOCK


def get_status() -> dict:
    """返回后台拉取状态(供 UI 查询)。"""
    with _LOCK:
        return dict(_STATE)


def start_background_fetcher() -> None:
    """启动后台拉取线程(进程内只触发一次)。"""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True

    t = threading.Thread(target=_run, daemon=True, name="bg-fetcher")
    t.start()


def _set_state(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def _run() -> None:
    """后台线程主逻辑:只做增量更新,不做全量构建。"""
    _set_state(
        status="running",
        stage="增量更新",
        message="检查缓存状态...",
        started_at=datetime.now().isoformat(),
        finished_at=None,
        progress=0,
    )

    try:
        if not cm.exists(CACHE_NAME):
            _set_state(
                status="skip",
                stage="跳过",
                message=(
                    f"未找到 {CACHE_NAME}.parquet,跳过自动增量。"
                    "请先手动跑 download_all 构建基础数据:"
                    f" python -c \"from tradingagents.quant.data.fetcher import download_all; "
                    f"download_all('2022-01-01', '{datetime.now():%Y-%m-%d}', "
                    f"percentile=1.0, cache_name='{CACHE_NAME}', max_workers=32)\""
                ),
                finished_at=datetime.now().isoformat(),
                progress=0,
            )
            return

        with _FETCH_LOCK:
            _run_incremental()

    except Exception as e:
        traceback.print_exc()
        _set_state(
            status="error",
            stage="",
            message=f"{type(e).__name__}: {e}",
            finished_at=datetime.now().isoformat(),
        )


def _run_incremental() -> None:
    """对已存在的 daily 缓存 parquet 做增量更新。"""
    from tradingagents.quant.data_update import (
        check_cache_freshness,
        increment_data,
    )

    daily_df = cm.load(CACHE_NAME)
    last_date, days_behind = check_cache_freshness(daily_df)

    is_weekend = pd.Timestamp.now().weekday() >= 5
    if days_behind <= 0 or (is_weekend and days_behind <= 2):
        _set_state(
            status="done",
            stage="增量更新",
            message=f"缓存已最新({last_date.date()}),无需更新",
            finished_at=datetime.now().isoformat(),
            progress=100,
        )
        return

    _set_state(
        stage="增量更新",
        message=f"增量拉取 {last_date.date()} -> 今天 ({days_behind} 天)",
        progress=0,
    )

    idx_df = cm.load(INDEX_CACHE_NAME) if cm.exists(INDEX_CACHE_NAME) else pd.DataFrame()

    def _progress(completed: int, total: int, stats: dict) -> None:
        pct = int(completed * 100 / total) if total > 0 else 0
        _set_state(
            progress=pct,
            message=(
                f"{completed}/{total} ({pct}%) - "
                f"成功 {stats.get('succeeded', 0)}, 失败 {stats.get('failed', 0)}"
            ),
        )

    daily_df, idx_df, msg = increment_data(
        daily_df, idx_df,
        daily_cache_name=CACHE_NAME,
        index_cache_name=INDEX_CACHE_NAME,
        max_workers=32,
        progress_callback=_progress,
    )

    _set_state(
        status="done",
        stage="增量更新",
        message=msg,
        finished_at=datetime.now().isoformat(),
        progress=100,
    )
