"""直接用 requests 调 sina K-line API,绕过 akshare/py_mini_racer。

用于 exe 内增量更新(akshare 的 py_mini_racer.dll 在 exe 里崩溃)。

sina K-line API 返回: day, open, high, low, close, volume
缺失字段处理:
- outstanding_share: 从已有缓存继承最后已知值(流通股本不常变)
- amount: 近似 = volume * (open+high+low+close)/4
- turnover_ratio: = volume / outstanding_share
- pre_close: = close.shift(1)
- change_pct: = close.pct_change() * 100

性能优化(2026-07): 用模块级 requests.Session(HTTP keep-alive)替代每次新建连接,
单股延迟从 ~200ms 降到 ~30-50ms(3-6x 提速)。配合 ThreadPoolExecutor 32 workers,
3000 股增量更新 < 1 分钟。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pandas as pd
import requests


KLINE_API = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_a/CN_MarketDataService.getKLineData"

# 模块级 Session:HTTP keep-alive 复用 TCP+TLS 连接,显著降低单股延迟
_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    """懒初始化模块级 requests.Session,带 UA header。"""
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        })
        _SESSION = s
    return _SESSION


def symbol_with_prefix(code: str) -> str:
    """600xxx -> sh600xxx, 00xxxx -> sz00xxxx。"""
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def adaptive_datalen(days: int) -> int:
    """按日期跨度自适应 datalen:交易日 ≈ 日历日 * 0.75,加 30 天缓冲,最少 30,最多 1000。"""
    return max(30, min(1000, int(days * 0.75) + 30))


def _fetch_kline_raw(symbol: str, datalen: int) -> pd.DataFrame:
    """请求 sina K-line API 并解析,返回 trade_date/open/high/low/close/volume(无 stock_code)。"""
    params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(datalen)}
    try:
        r = _get_session().get(KLINE_API, params=params, timeout=15)
        if r.status_code != 200:
            return pd.DataFrame()
        text = r.text
        start = text.index("(") + 1
        end = text.rindex(")")
        data = json.loads(text[start:end])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df = df.rename(columns={"day": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["trade_date", "open", "high", "low", "close", "volume"]]
    except Exception:
        return pd.DataFrame()


def fetch_kline_sina(code: str, datalen: int = 30) -> pd.DataFrame:
    """从 sina K-line API 拉取最近 datalen 天的数据(不复权)。

    Returns DataFrame with: trade_date, open, high, low, close, volume, stock_code
    """
    df = _fetch_kline_raw(symbol_with_prefix(code), datalen)
    if len(df) > 0:
        df["stock_code"] = code
    return df


def fetch_incremental_sina(code: str, start_date: str, end_date: str,
                            last_outstanding_share: float | None = None,
                            last_close: float | None = None) -> pd.DataFrame:
    """拉取单只股票的增量数据(start_date ~ end_date)。

    Args:
        last_outstanding_share: 从缓存继承的流通股本(用于计算 turnover_ratio)
        last_close: 从缓存继承的上一收盘价。增量窗口从缓存最后交易日+1 开始,
            窗口内 shift(1) 会让首行 pre_close/change_pct 恒为 NaN;传入该值
            保证边界日技术指标完整(否则 NaN 会被 cm.update 永久落盘)。
    """
    # 按日期跨度自适应 datalen:days_behind + 30 天缓冲,最少 30,最多 1000
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    calendar_days = (end_ts - start_ts).days
    datalen = adaptive_datalen(calendar_days)

    df = fetch_kline_sina(code, datalen=datalen)
    if len(df) == 0:
        return pd.DataFrame()

    df = df[(df["trade_date"] >= start_ts) & (df["trade_date"] <= end_ts)]
    if len(df) == 0:
        return pd.DataFrame()

    # 近似计算缺失字段
    df["amount"] = df["volume"] * (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    if last_outstanding_share is not None and last_outstanding_share > 0:
        df["outstanding_share"] = last_outstanding_share
        df["turnover_ratio"] = df["volume"] / last_outstanding_share
    else:
        df["outstanding_share"] = 0.0
        df["turnover_ratio"] = 0.0

    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pre_close"] = df["close"].shift(1)
    if last_close is not None and len(df) > 0:
        df.loc[0, "pre_close"] = last_close
    prev = df["pre_close"].replace(0, float("nan"))
    df["change_pct"] = (df["close"] - prev) / prev * 100
    return df


def fetch_bulk_incremental_sina(codes: list[str], start_date: str, end_date: str,
                                 last_shares_map: dict[str, float] | None = None,
                                 last_close_map: dict[str, float] | None = None,
                                 max_workers: int = 32,
                                 progress_callback: Callable[[int, int, dict], None] | None = None,
                                 stop_check: Callable[[], bool] | None = None
                                 ) -> tuple[pd.DataFrame, list[str]]:
    """批量增量拉取。

    Args:
        last_shares_map: {stock_code: outstanding_share} 从缓存继承
        last_close_map: {stock_code: 最后收盘价} 从缓存继承,用于修正增量
            首行 pre_close/change_pct(否则窗口内 shift 致边界日 NaN)。
        max_workers: 默认 32(原 8)。配合 Session keep-alive,3000 股 < 1 分钟。
        progress_callback: fn(completed, total, stats),stats 含 succeeded/failed/latest_code
        stop_check: 每完成一个 future 轮询一次;返回 True 时取消未启动的
            拉取并尽快返回已累积结果(调用方可将部分数据落盘,下次续跑)。
    """
    if last_shares_map is None:
        last_shares_map = {}
    if last_close_map is None:
        last_close_map = {}

    results: list[pd.DataFrame] = []
    failed: list[str] = []
    total = len(codes)

    def _one(code: str) -> pd.DataFrame:
        return fetch_incremental_sina(
            code, start_date, end_date,
            last_outstanding_share=last_shares_map.get(code),
            last_close=last_close_map.get(code))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_one, c): c for c in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            if stop_check is not None and stop_check():
                for f in futures:
                    f.cancel()
                break
            code = futures[fut]
            try:
                df = fut.result()
                if len(df) > 0:
                    results.append(df)
                else:
                    failed.append(code)
            except Exception:
                failed.append(code)
            if progress_callback is not None:
                progress_callback(i, total, {
                    "succeeded": len(results),
                    "failed": len(failed),
                    "latest_code": code,
                })

    if results:
        big = pd.concat(results, ignore_index=True)
        big["trade_date"] = pd.to_datetime(big["trade_date"]).dt.normalize()
        return big, failed
    return pd.DataFrame(), failed


def fetch_index_sina(index_code: str = "sh000001", datalen: int = 30) -> pd.DataFrame:
    """拉取指数 K 线数据(默认上证指数)。薄包装 _fetch_kline_raw,列名同 K 线但不含 stock_code。"""
    return _fetch_kline_raw(index_code, datalen)


if __name__ == "__main__":
    # 测试
    print("=== 测试 600000 ===")
    df = fetch_kline_sina("600000", datalen=5)
    print(df)
    print()
    print("=== 测试增量 ===")
    df2 = fetch_incremental_sina("600000", "2026-07-10", "2026-07-14", last_outstanding_share=293.5e8)
    print(df2)
