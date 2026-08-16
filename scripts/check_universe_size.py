"""统计 universe 过滤各阶段后的股票数。

验证价格过滤(默认 3-70 元)+ 涨跌停过滤 + 流动性分位配置后的实际股票池大小。

用法:
    python scripts/check_universe_size.py
    python scripts/check_universe_size.py --cache daily_main_board
    python scripts/check_universe_size.py --date 2026-07-18
    python scripts/check_universe_size.py --price-min 3 --price-max 120 --percentile 0.8
"""
from __future__ import annotations

import argparse

import pandas as pd

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant.data import cache as cm
from tradingagents.quant.data.universe import (
    _get_calendar,
    _get_first_dates_per_stock,
    get_list_dates,
    get_st_codes_on_date,
    is_at_price_limit,
    is_main_board,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="统计 universe 过滤各阶段后的股票数")
    parser.add_argument("--cache", default="daily_main_board",
                        help="日线 cache 名(默认 daily_main_board)")
    parser.add_argument("--date", default=None,
                        help="模拟日期 YYYY-MM-DD(默认 cache 最新日期)")
    parser.add_argument("--price-min", type=float, default=None,
                        help="最低股价(默认读 default_config)")
    parser.add_argument("--price-max", type=float, default=None,
                        help="最高股价(默认读 default_config)")
    parser.add_argument("--percentile", type=float, default=None,
                        help="流动性保留分位 0-1(默认读 default_config)")
    args = parser.parse_args()

    price_min = args.price_min if args.price_min is not None else DEFAULT_CONFIG.get("quant_price_min")
    price_max = args.price_max if args.price_max is not None else DEFAULT_CONFIG.get("quant_price_max")
    percentile = args.percentile if args.percentile is not None else DEFAULT_CONFIG.get("quant_liquidity_percentile", 0.8)

    print(f"加载 cache: {args.cache}")
    daily_df = cm.load(args.cache)
    on_date = pd.Timestamp(args.date) if args.date else daily_df["trade_date"].max()
    on_date = pd.Timestamp(on_date).normalize()
    print(f"模拟日期: {on_date.date()}")
    print(f"cache 总行数: {len(daily_df):,}")
    print(f"配置: price_min={price_min}, price_max={price_max}, percentile={percentile}")
    print("-" * 60)

    all_codes = daily_df["stock_code"].unique()
    print(f"[阶段1] cache 内股票数:        {len(all_codes):>6,}")

    main_board_codes = [c for c in all_codes if is_main_board(c)]
    print(f"[阶段2] 主板过滤后(60/00):     {len(main_board_codes):>6,}")

    try:
        st_codes = get_st_codes_on_date(on_date)
        non_st = [c for c in main_board_codes if c not in st_codes]
        print(f"[阶段3] ST 过滤后:             {len(non_st):>6,}  (剔除 {len(main_board_codes) - len(non_st):,} 只 ST)")
    except RuntimeError as e:
        print(f"[阶段3] ST 过滤跳过: {e}")
        non_st = list(main_board_codes)

    liquidity_window = 20
    min_listing_days = 60
    window_start = on_date - pd.Timedelta(liquidity_window * 2, unit="D")
    df_window = daily_df[(daily_df["trade_date"] <= on_date) &
                         (daily_df["trade_date"] >= window_start)]

    list_dates = get_list_dates()
    calendar = _get_calendar()
    first_dates_per_stock = _get_first_dates_per_stock(daily_df)
    on_date_idx = calendar.searchsorted(on_date, side="right")

    eligible: list[tuple[str, float, float, float | None, float | None]] = []
    for code in non_st:
        grp = df_window[df_window["stock_code"] == code]
        if len(grp) == 0 or len(grp) < liquidity_window:
            continue
        recent = grp.tail(liquidity_window)
        last_date = grp["trade_date"].iloc[-1]
        if (on_date - last_date).days > 5:
            continue

        first_date = first_dates_per_stock.get(code)
        if first_date is None:
            list_date = list_dates.get(code)
            if list_date is None or pd.isna(list_date):
                continue
            first_date = pd.Timestamp(list_date).normalize()
        first_idx = calendar.searchsorted(first_date, side="left")
        if (on_date_idx - first_idx) < min_listing_days:
            continue

        last_row = recent.iloc[-1]
        pre_close = last_row.get("pre_close") if "pre_close" in recent.columns else None
        pre_close = None if pd.isna(pre_close) else float(pre_close)
        change_pct = last_row.get("change_pct") if "change_pct" in recent.columns else None
        change_pct = None if pd.isna(change_pct) else float(change_pct)
        eligible.append((
            code,
            float(recent["amount"].mean()),
            float(recent["close"].iloc[-1]),
            pre_close,
            change_pct,
        ))

    print(f"[阶段4] 数据完整性(20日窗口/上市60天/未停牌): {len(eligible):>6,}  (剔除 {len(non_st) - len(eligible):,} 只)")

    after_price = [(c, amt, p, pc, chg) for c, amt, p, pc, chg in eligible
                   if (price_min is None or p >= price_min) and
                      (price_max is None or p <= price_max)]
    filtered_out = len(eligible) - len(after_price)
    print(f"[阶段5] 价格过滤后({price_min}-{price_max}元): {len(after_price):>6,}  (剔除 {filtered_out:,} 只)")

    after_limit = [
        (c, amt, p, pc, chg) for c, amt, p, pc, chg in after_price
        if not is_at_price_limit(c, close=p, pre_close=pc, change_pct=chg)
    ]
    print(f"[阶段6] 涨跌停过滤后:            {len(after_limit):>6,}  (剔除 {len(after_price) - len(after_limit):,} 只)")

    after_limit.sort(key=lambda x: x[1], reverse=True)
    keep_n = max(1, int(len(after_limit) * percentile))
    final = after_limit[:keep_n]
    print(f"[阶段7] 流动性前 {percentile*100:.0f}%:        {len(final):>6,}  (剔除 {len(after_limit) - len(final):,} 只)")
    print("-" * 60)

    if final:
        prices = [p for _, _, p, _, _ in final]
        amounts = [amt for _, amt, _, _, _ in final]
        med_p = sorted(prices)[len(prices) // 2]
        med_a = sorted(amounts)[len(amounts) // 2]
        print(f"最终池股价: min={min(prices):.2f} / med={med_p:.2f} / max={max(prices):.2f}")
        print(f"最终池20日均成交额: min={min(amounts)/1e8:.2f}亿 / med={med_a/1e8:.2f}亿 / max={max(amounts)/1e8:.2f}亿")


if __name__ == "__main__":
    main()
