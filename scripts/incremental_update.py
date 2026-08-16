"""CLI 增量更新 daily 缓存到最新交易日。

薄包装 tradingagents.quant.data_update.increment_data——与 Web 后台
(background_fetcher) / runner 共用同一条生产路径,单一实现,不再各自
实现拉取+合并逻辑。新增行会继承缓存里最后的 outstanding_share。

用法:
    # 共享 stock_selector 的 cache
    set QUANT_CACHE_DIR=C:\\Users\\Tao\\Desktop\\新建文件夹 (4)\\stock_selector\\outputs\\cache
    py -3 scripts/incremental_update.py
    # 默认 cache 名取 quant_daily_cache_name(默认全量主板 daily_main_board);
    # 想更新数据采集层已截断的流动性前 80% 缓存时显式指定:
    py -3 scripts/incremental_update.py --cache daily_main_board_liquid
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

import tradingagents.quant.sina_fetcher as sf
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant import config
from tradingagents.quant.data import cache as cm
from tradingagents.quant.data_update import backfill_stale, increment_data


def main() -> int:
    default_cache = DEFAULT_CONFIG.get("quant_daily_cache_name", "daily_main_board")
    parser = argparse.ArgumentParser(description="增量更新 daily 缓存到最新交易日")
    parser.add_argument("--cache", default=default_cache,
                        help=f"daily 缓存名(默认 {default_cache})")
    parser.add_argument("--index", default="index_000001",
                        help="指数缓存名(默认 index_000001)")
    args = parser.parse_args()

    print("=" * 80)
    print(f"增量更新 {args.cache} 缓存")
    print(f"缓存目录: {config.CACHE_DIR}")
    print("=" * 80)

    if not cm.exists(args.cache):
        print(f"  错误: 缓存 {args.cache} 不存在")
        return 1

    print("\n[1/2] 加载现有缓存...")
    daily_df = cm.load(args.cache)
    daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.normalize()
    last_date = daily_df["trade_date"].max()
    print(f"  现有: {len(daily_df):,} 行, "
          f"{daily_df['stock_code'].nunique():,} 只, 最新 {last_date.date()}")
    idx_df = cm.load(args.index) if cm.exists(args.index) else pd.DataFrame()
    if len(idx_df) > 0:
        idx_df["trade_date"] = pd.to_datetime(idx_df["trade_date"]).dt.normalize()

    print("\n[2/2] 增量更新...")
    daily_df, idx_df, msg = increment_data(
        daily_df, idx_df,
        daily_cache_name=args.cache,
        index_cache_name=args.index,
        max_workers=32,
    )
    print(msg)

    # 补差:increment_data 的保鲜检查按"全局最大交易日"判断,部分更新后
    # (如中途停止、新浪 456 封禁中断)全局 max 已最新,但它会误判"无需更新",
    # 导致落后的股票永远补不上。backfill_stale 定向回补"最后交易日 < 缓存最新日"
    # 的股票,分块 + 探测冷却自限流,中断可续跑。
    printed = False

    def _progress(done, total, stats):
        nonlocal printed
        printed = True
        print(f"\r  补差 {done}/{total}, 仍落后 {stats['remaining']}, "
              f"失败 {stats['failed']}", end="", flush=True)

    daily_df, remaining = backfill_stale(
        daily_df, args.cache,
        window_days=7, chunk_size=180, cooldown=90,
        max_workers=16,
        probe=sf.probe_sina_available,
        sleep=time.sleep,
        progress_callback=_progress,
    )
    if printed:
        print()
        if remaining:
            print(f"  补差后仍落后: {len(remaining)} 只(新浪限流时需稍后重跑本脚本)")

    print(f"\n最新日期: {daily_df['trade_date'].max().date()}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
