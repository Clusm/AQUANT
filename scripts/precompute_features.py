"""预计算周线/月线 bars 到 parquet,worker 直接 load 跳过 resample。

预计算:
  daily_main_board -> daily_main_board_weekly_bars.parquet  (~50MB)
                    -> daily_main_board_monthly_bars.parquet (~15MB)

worker 启动时:
  - load daily cache(原逻辑,73MB)
  - load weekly_bars / monthly_bars parquet
  - 填进 _WEEKLY_BARS_CACHE / _MONTHLY_BARS_CACHE
  - 策略调 get_weekly_bars / get_monthly_bars 时命中 cache,跳过 resample

收益:
  - get_weekly_bars resample: 20s -> 3s(load 50MB)
  - get_monthly_bars resample: 15s -> 1s(load 15MB)
  - 节省 ~31s per worker,wall time 省 ~31s

日线特征(build_features_vectorized)还是现场算,因为:
  - 日线特征 parquet 太大(903MB,85 列),load 慢 + 内存爆炸
  - 日线特征计算只要 40s,可接受

用法:
    set QUANT_CACHE_DIR=C:\\Users\\Tao\\Desktop\\新建文件夹 (4)\\stock_selector\\outputs\\cache
    py -3 scripts/precompute_features.py
    py -3 scripts/precompute_features.py --cache daily_main_board_liquid
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant.data import cache as cm
from tradingagents.quant.features.pipeline import (
    get_monthly_bars,
    get_weekly_bars,
)


def precompute(daily_cache_name: str) -> int:
    """预计算 weekly_bars / monthly_bars 并存 parquet。返回 0 成功。"""
    weekly_name = f"{daily_cache_name}_weekly_bars"
    monthly_name = f"{daily_cache_name}_monthly_bars"
    print("=" * 80)
    print(f"预计算 bars: {daily_cache_name} -> {weekly_name} + {monthly_name}")
    print(f"缓存目录: {cm.CACHE_DIR}")
    print("=" * 80)

    # Step 1: load daily cache
    print("\n[1/4] 加载 daily cache...")
    if not cm.exists(daily_cache_name):
        print(f"  错误: {daily_cache_name} 不存在")
        return 1
    daily_df = cm.load(daily_cache_name)
    daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.normalize()
    daily_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
    last_date = daily_df["trade_date"].max()
    print(f"  rows={len(daily_df):,}, codes={daily_df['stock_code'].nunique()}, last={last_date.date()}")

    # Step 2: 失效检查
    src_end = str(last_date.date())
    src_rows = len(daily_df)
    need_rebuild = False
    for name in (weekly_name, monthly_name):
        meta = cm.load_meta(name) or {}
        if (meta.get("source_end") != src_end
                or meta.get("source_rows") != src_rows
                or not cm.exists(name)):
            need_rebuild = True
            break
    if not need_rebuild:
        print(f"\n[2/4] bars cache 已是最新(source_end={src_end}),跳过")
        return 0
    print(f"\n[2/4] bars cache 需要更新(源最新 {src_end})")

    # Step 3: 计算周线/月线 bars
    print("\n[3/4] 计算周线/月线 bars...")
    t0 = time.time()
    weekly_bars = get_weekly_bars(daily_df)
    print(f"  weekly done in {time.time()-t0:.1f}s, rows={len(weekly_bars):,}, cols={len(weekly_bars.columns)}")
    t0 = time.time()
    monthly_bars = get_monthly_bars(daily_df)
    print(f"  monthly done in {time.time()-t0:.1f}s, rows={len(monthly_bars):,}, cols={len(monthly_bars.columns)}")

    # Step 4: 保存
    print("\n[4/4] 保存 bars parquet...")
    t0 = time.time()
    cm.save(weekly_name, weekly_bars, meta={
        "source_cache": daily_cache_name,
        "source_end": src_end,
        "source_rows": src_rows,
        "rows": len(weekly_bars),
        "cols": len(weekly_bars.columns),
        "build_time": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    w_size = cm.cache_path(weekly_name).stat().st_size / 1e6
    print(f"  weekly saved in {time.time()-t0:.1f}s, size={w_size:.1f}MB")

    t0 = time.time()
    cm.save(monthly_name, monthly_bars, meta={
        "source_cache": daily_cache_name,
        "source_end": src_end,
        "source_rows": src_rows,
        "rows": len(monthly_bars),
        "cols": len(monthly_bars.columns),
        "build_time": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    m_size = cm.cache_path(monthly_name).stat().st_size / 1e6
    print(f"  monthly saved in {time.time()-t0:.1f}s, size={m_size:.1f}MB")

    print(f"\n{'='*80}")
    print("预计算完成:")
    print(f"  {weekly_name}: {len(weekly_bars):,} rows, {w_size:.1f}MB")
    print(f"  {monthly_name}: {len(monthly_bars):,} rows, {m_size:.1f}MB")
    print(f"  源最新日期: {last_date.date()}")
    print(f"{'='*80}")
    return 0


def main():
    default_cache = DEFAULT_CONFIG.get("quant_daily_cache_name", "daily_main_board")
    parser = argparse.ArgumentParser(description="预计算 weekly/monthly bars 到 parquet")
    parser.add_argument("--cache", default=default_cache,
                        help=f"daily cache 名(默认 {default_cache})")
    args = parser.parse_args()
    return precompute(args.cache)


if __name__ == "__main__":
    sys.exit(main())
