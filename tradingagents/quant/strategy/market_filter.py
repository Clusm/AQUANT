"""大盘择时:上证指数均线过滤,空头市场不生成买入信号。

规则:
- bull_strong: close > MA15 > MA35(满仓可买)
- bull_weak:   close > MA35 但不满足 strong(可买,谨慎)
- bear:        close <= MA35(不买,仅管理现有持仓)

用 MA35 作多空分界,避免 MA20 频繁切换。MA15/MA35 双均线确认趋势。
参数经过 2024-07 ~ 2025-07 回测调优:MA(15,35) 较 MA(20,60) comp 11.61 -> 12.09,回撤 -8% -> -6.08%。

长趋势过滤(long_ma=90, surge_lookback=5, surge_pct=0.03):
- 大盘 close 在 MA90 之上才入场,避开慢熊中的反弹假信号
- 反弹补丁:近 5 日大盘涨 3%+ 时即使 close < MA90 也允许入场,捕捉刚启动行情
- 全期 comp 7.74 -> 8.43,回撤 -25.05% -> -22.33%,胜率 47% -> 50%,主回测 12.09 -> 12.29
"""
from __future__ import annotations

import pandas as pd

from tradingagents.quant.features.indicators import ma


class MarketFilter:
    def __init__(self, index_df: pd.DataFrame, fast: int = 15, slow: int = 35,
                 long_ma: int | None = 90, surge_lookback: int = 5, surge_pct: float = 0.03):
        df = index_df.sort_values("trade_date").reset_index(drop=True).copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
        df["ma_fast"] = ma(df["close"], fast)
        df["ma_slow"] = ma(df["close"], slow)
        if long_ma is not None:
            df["ma_long"] = ma(df["close"], long_ma)
        df = df.dropna(subset=["ma_slow"]).reset_index(drop=True)
        self._table = df.set_index("trade_date")
        self.fast = fast
        self.slow = slow
        self.long_ma = long_ma
        self.surge_lookback = surge_lookback
        self.surge_pct = surge_pct
        self._long_cache: dict[pd.Timestamp, bool] = {}

    def _long_trend_ok(self, date: pd.Timestamp) -> bool:
        """大盘长期趋势过滤:close 在 MA90 之上 OR 近 5 日涨 3%+ 反弹确认。"""
        if self.long_ma is None:
            return True
        date = pd.Timestamp(date).normalize()
        if date in self._long_cache:
            return self._long_cache[date]
        hist = self._table[self._table.index <= date]
        if len(hist) == 0:
            self._long_cache[date] = True
            return True
        row = hist.iloc[-1]
        if pd.isna(row.get("ma_long", float("nan"))):
            self._long_cache[date] = True
            return True
        if row["close"] > row["ma_long"]:
            self._long_cache[date] = True
            return True
        # 反弹补丁:近 surge_lookback 日大盘涨幅超过 surge_pct
        if self.surge_lookback > 0 and len(hist) >= self.surge_lookback + 1:
            ref = hist.iloc[-self.surge_lookback - 1]["close"]
            ret = (row["close"] - ref) / ref
            if ret >= self.surge_pct:
                self._long_cache[date] = True
                return True
        self._long_cache[date] = False
        return False

    def status(self, date: pd.Timestamp) -> str:
        """返回 date 当日的大盘状态(用 date 当日或之前最近的指数数据)。"""
        date = pd.Timestamp(date).normalize()
        hist = self._table[self._table.index <= date]
        if len(hist) == 0:
            return "unknown"
        row = hist.iloc[-1]
        if row["close"] > row["ma_fast"] > row["ma_slow"]:
            return "bull_strong"
        if row["close"] > row["ma_slow"]:
            return "bull_weak"
        return "bear"

    def is_bullish(self, date: pd.Timestamp) -> bool:
        """多头市场可买入(bull_strong 或 bull_weak),且需通过长趋势过滤。"""
        if not self._long_trend_ok(date):
            return False
        return self.status(date) in ("bull_strong", "bull_weak")

    def is_strong_bull(self, date: pd.Timestamp) -> bool:
        return self.status(date) == "bull_strong"

    def coverage(self, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float]:
        """统计 [start, end] 区间内各状态占比,用于诊断。"""
        sub = self._table[(self._table.index >= start) & (self._table.index <= end)]
        if len(sub) == 0:
            return {}
        statuses = sub.apply(lambda r: "bull_strong" if r["close"] > r["ma_fast"] > r["ma_slow"]
                             else ("bull_weak" if r["close"] > r["ma_slow"] else "bear"), axis=1)
        counts = statuses.value_counts()
        return {k: f"{v / len(sub):.1%}" for k, v in counts.items()}
