"""分块回补 daily_main_board 落后股票(绕开被封的新端点,仅走旧端点)。

新浪封 IP 约 250 请求/批,批间需冷却。复用 data_update.backfill_stale
(共享分块回补调度器):180 只/批 + 批间探测冷却,每批即时 cm.update 落盘,
中断后可重跑续补(仍落后的会自动重进 stale 列表)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import tradingagents.quant.sina_fetcher as sf
from tradingagents.quant.data import cache as cm
from tradingagents.quant.data_update import backfill_stale
from tradingagents.quant.sina_fetcher import KLINE_API_FALLBACK

CACHE = "daily_main_board"
CHUNK = 180
COOLDOWN = 90


def main() -> None:
    # 强制只走旧端点:新端点 quotes.sina.cn 已被 456 封禁(进程级端点策略)
    sf.KLINE_API = KLINE_API_FALLBACK

    df = cm.load(CACHE)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    target = df["trade_date"].max()
    stale = [str(c) for c, lst in df.groupby("stock_code")["trade_date"].max().items()
             if lst < target]
    total = len(stale)
    print(f"target={target.date()} 需回补 {total} 只", flush=True)
    if total == 0:
        print("无落后股票,结束", flush=True)
        return

    _, remaining = backfill_stale(
        df, CACHE,
        window_days=7, chunk_size=CHUNK, cooldown=COOLDOWN,
        max_workers=16,
        probe=sf.probe_sina_available,
        sleep=time.sleep,
        progress_callback=lambda done, tot, stats: print(
            f"  批 {done}/{tot}, 仍落后 {stats['remaining']}, "
            f"失败 {stats['failed']}, 冷却 {COOLDOWN}s", flush=True),
    )
    print(f"完成: {total - len(remaining)}/{total} 只回补到 {target.date()}", flush=True)
    if remaining:
        print(f"仍落后 {len(remaining)} 只(可重跑续补)", flush=True)


if __name__ == "__main__":
    main()
