"""CLI 增量更新 daily 缓存到最新交易日。

薄包装 tradingagents.quant.data_update.increment_data——与 Web 后台
(background_fetcher) / runner 共用同一条生产路径,单一实现,不再各自
实现拉取+合并逻辑。新增行会继承缓存里最后的 outstanding_share。

用法:
    # 共享 stock_selector 的 cache
    set QUANT_CACHE_DIR=C:\\Users\\Tao\\Desktop\\新建文件夹 (4)\\stock_selector\\outputs\\cache
    py -3 scripts/incremental_update.py
    # 默认 cache 名取 quant_daily_cache_name;全量主板缓存需显式指定:
    py -3 scripts/incremental_update.py --cache daily_main_board
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant import config
from tradingagents.quant.data import cache as cm
from tradingagents.quant.data_update import increment_data


def main() -> int:
    default_cache = DEFAULT_CONFIG.get("quant_daily_cache_name", "daily_main_board_liquid")
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

    print(f"\n[1/2] 加载现有缓存...")
    daily_df = cm.load(args.cache)
    daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.normalize()
    last_date = daily_df["trade_date"].max()
    print(f"  现有: {len(daily_df):,} 行, "
          f"{daily_df['stock_code'].nunique():,} 只, 最新 {last_date.date()}")
    idx_df = cm.load(args.index) if cm.exists(args.index) else pd.DataFrame()
    if len(idx_df) > 0:
        idx_df["trade_date"] = pd.to_datetime(idx_df["trade_date"]).dt.normalize()

    print(f"\n[2/2] 增量更新...")
    daily_df, idx_df, msg = increment_data(
        daily_df, idx_df,
        daily_cache_name=args.cache,
        index_cache_name=args.index,
        max_workers=32,
    )
    print(msg)
    print(f"\n最新日期: {daily_df['trade_date'].max().date()}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
