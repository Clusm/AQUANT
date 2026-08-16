"""验证 07-20 时,4 只消失股票的周线指标在两种 merge 方式下的差异。

对比:
- 修复前(week_key 精确匹配):T 日看到本周(含未来)的周线指标
- 修复后(merge_asof backward):T 日看到上一完整周的周线指标

用来判断:原版策略是否也有 look-ahead bias。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.quant.data import cache as cm
from tradingagents.quant.features.pipeline import (
    get_weekly_bars,
    merge_asof_weekly,
)


def main() -> int:
    daily_df = cm.load("daily_main_board_liquid")
    daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.normalize()
    daily_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)

    test_date = pd.Timestamp("2026-07-20")
    codes = ["000333", "600886", "601728", "600664"]

    print(f"测试日期: {test_date.date()}  测试股票: {codes}")
    print(f"07-20 是周几: {test_date.day_name()}")
    print("=" * 100)

    # 取这 4 只股票的日线数据
    sub = daily_df[daily_df["stock_code"].isin(codes)].copy()
    print(f"4 只股票的日线数据范围: {sub['trade_date'].min().date()} ~ {sub['trade_date'].max().date()}")

    # 看这 4 只股票 07-20 当天的日线
    day = sub[sub["trade_date"] == test_date][["stock_code", "trade_date", "open", "close", "high", "low", "volume"]]
    print("\n07-20 当天日线:")
    print(day.to_string(index=False))

    # 看这 4 只股票 07-13~07-24 的日线(看本周和上周)
    print("\n07-13~07-21 日线(看本周和上周):")
    recent = sub[(sub["trade_date"] >= "2026-07-13") & (sub["trade_date"] <= "2026-07-21")]
    print(recent[["stock_code", "trade_date", "open", "close", "high", "low", "volume"]].to_string(index=False))

    # 用 get_weekly_bars 获取周线
    weekly = get_weekly_bars(sub)
    print("\n周线 bars(4 只股票):")
    print(weekly[["stock_code", "week_key", "week_date", "week_close", "wma5", "wma10",
                  "weekly_above_ma5", "weekly_bullish", "wmacd", "wsignal", "wmacd_gc_recent"]].to_string(index=False))

    # 两种 merge 方式
    print("\n" + "=" * 100)
    print("对比两种 merge 方式在 07-20 的周线指标值:")
    print("=" * 100)

    # 只取 07-20 的日线行
    daily_0720 = sub[sub["trade_date"] == test_date].copy()

    # 方式 A:week_key 精确匹配(修复前,有 look-ahead)
    daily_0720_a = daily_0720.copy()
    daily_0720_a["week_key"] = daily_0720_a["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                                daily_0720_a["trade_date"].dt.isocalendar().year.astype(str)
    merged_a = daily_0720_a.merge(
        weekly[["stock_code", "week_key", "week_date", "week_close", "wma5", "wma10",
                "weekly_above_ma5", "weekly_bullish", "wmacd", "wsignal", "wmacd_gc_recent",
                "wrsi_14"]],
        on=["stock_code", "week_key"], how="left"
    )

    # 方式 B:merge_asof backward(修复后,无 look-ahead)
    daily_0720_b = daily_0720.copy()
    merged_b = merge_asof_weekly(
        daily_0720_b,
        weekly[["stock_code", "week_key", "week_date", "week_close", "wma5", "wma10",
                "weekly_above_ma5", "weekly_bullish", "wmacd", "wsignal", "wmacd_gc_recent",
                "wrsi_14"]]
    )

    print("\n方式 A(week_key 精确匹配,修复前,有 look-ahead):")
    print(merged_a[["stock_code", "trade_date", "week_key", "week_date", "week_close",
                    "wma5", "weekly_bullish", "wmacd_gc_recent", "wrsi_14"]].to_string(index=False))

    print("\n方式 B(merge_asof backward,修复后,无 look-ahead):")
    print(merged_b[["stock_code", "trade_date", "week_date", "week_close",
                    "wma5", "weekly_bullish", "wmacd_gc_recent", "wrsi_14"]].to_string(index=False))

    print("\n" + "=" * 100)
    print("结论:")
    for code in codes:
        a_row = merged_a[merged_a["stock_code"] == code].iloc[0]
        b_row = merged_b[merged_b["stock_code"] == code].iloc[0]
        a_wd = a_row["week_date"].date() if pd.notna(a_row["week_date"]) else "NaT"
        b_wd = b_row["week_date"].date() if pd.notna(b_row["week_date"]) else "NaT"
        a_close = a_row["week_close"] if pd.notna(a_row["week_close"]) else float("nan")
        b_close = b_row["week_close"] if pd.notna(b_row["week_close"]) else float("nan")
        same = "相同" if a_wd == b_wd else "不同"
        print(f"  {code}: A 看到的 week_date={a_wd} week_close={a_close:.2f} | B 看到的 week_date={b_wd} week_close={b_close:.2f} | {same}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
