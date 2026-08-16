"""中线策略 V28-2:月线 RSI 突破中线(月线 RSI 上穿中性区 + 多重多头确认)。

V28 新方向(基于 V25 w_macd_gc + V21 m_hi_bo 的月线级别经验深化):
- 与 V21-1 monthly_high_breakout(月线新高突破)不同
- 与 V25-2 weekly_macd_golden_cross(周线 MACD 金叉)不同
- 本策略:月线 RSI(14) 上穿 50(从中性区进入强势区)+ 月线多头 + 周线多头 + 日线放量
- 与 V21-1 区别:V21-1 是月线新高,本策略是月线 RSI 突破(动量指标层面确认)
- 与 V25-2 区别:V25-2 是周线 MACD 金叉,本策略是月线 RSI 突破(更纯粹的动量确认)

核心思路(月线 RSI 突破 + 月线多头 + 周线多头 + 日线放量):
- 月线 RSI(14) 上穿 50(从中性区进入强势区)发生在最近 2 个月内
- 月线 RSI 当前值在 50-70 之间(强势但不过热)
- 月线多头:月线 close > 月线 MA3 > 月线 MA6
- 周线多头:周线 close > 周线 MA5
- 日线:close > MA20
- T 日放量阳线 volume_ratio_5 >= 1.5,body_ratio >= 0.5

中线逻辑:月线 RSI 上穿 50 是月线级别动量由弱转强的关键信号,叠加多重多头确认,持仓 10-15d 让趋势延续。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class MonthlyRsiBreakoutStrategy(BaseStrategy):
    """月线 RSI 突破中线策略(月线 RSI 上穿 50 + 月/周线多头 + 日线放量)。"""
    name = "monthly_rsi_breakout"

    def __init__(self, lookback: int = 260, universe_topk: int = 500,
                 rsi_window: int = 14,
                 cross_recent_months: int = 2,
                 rsi_min: float = 50.0,
                 rsi_max: float = 70.0,
                 monthly_ma_short: int = 3,
                 monthly_ma_mid: int = 6,
                 weekly_ma_short: int = 5,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 near_high_ratio: float = 0.95,
                 enable_signal_exit: bool = False,
                 rsi_turn_exit: bool = False,
                 exit_min_holding_days: int = 5):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.rsi_window = rsi_window
        self.cross_recent_months = cross_recent_months
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
        self.monthly_ma_short = monthly_ma_short
        self.monthly_ma_mid = monthly_ma_mid
        self.weekly_ma_short = weekly_ma_short
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.near_high_ratio = near_high_ratio
        self.enable_signal_exit = enable_signal_exit
        self.rsi_turn_exit = rsi_turn_exit
        self.exit_min_holding_days = exit_min_holding_days
        self._calendar = None
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] | None = None
        self._exit_lookup: dict[tuple, dict] | None = None

    def _get_universe(self, daily_df: pd.DataFrame, current_date: pd.Timestamp) -> list[str]:
        month_key = f"{current_date.year}-{current_date.month:02d}"
        if month_key in self._universe_cache:
            return self._universe_cache[month_key]
        codes = filter_universe_topk(daily_df, on_date=current_date, topk=self.universe_topk)
        self._universe_cache[month_key] = codes
        return codes

    def _precompute_features(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        if self._feature_cache is not None:
            return self._feature_cache
        print("  [月线 RSI 突破] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 120].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=30, columns=required_feature_columns(self))
        if len(big) == 0:
            self._feature_cache = pd.DataFrame()
            return self._feature_cache
        big = big.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        big["prev_close_raw"] = big.groupby("stock_code")["close"].shift(1)
        big["today_ret"] = (big["close"] - big["prev_close_raw"]) / big["prev_close_raw"].replace(0, np.nan)

        body = big["close"] - big["open"]
        hl = (big["high"] - big["low"]).replace(0, np.nan)
        big["body_ratio"] = (body / hl).fillna(0)
        big["is_bullish"] = (big["close"] > big["open"]).astype(float)

        # 周线数据:close > 周线 MA5
        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)
        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_ma_short, min_periods=3).mean())
        weekly["weekly_above_ma5"] = (weekly["week_close"] > weekly["wma5"]).astype(float)
        weekly_last = weekly.groupby(["stock_code", "week_key"]).last().reset_index()
        # 关键改动:从 merge(on=week_key) 改成 merge_asof(direction=backward)
        # T 日只看到 week_date <= T 的最近一周(上一完整周),当周 indicator 不会出现在 T 日行
        weekly_to_merge = weekly_last[["stock_code", "week_date", "weekly_above_ma5", "wma5"]].sort_values("week_date")
        big = pd.merge_asof(
            big.sort_values("trade_date"),
            weekly_to_merge,
            left_on="trade_date", right_on="week_date",
            by="stock_code", direction="backward",
        )

        # 月线数据:close=月末close, high=月最高, low=月最低, volume=月总量
        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        monthly = big.groupby(["stock_code", "month_key"]).agg(
            month_close=("close", "last"),
            month_high=("high", "max"),
            month_low=("low", "min"),
            month_volume=("volume", "sum"),
            month_date=("trade_date", "last")
        ).reset_index()
        monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)

        # 月线 RSI(14)
        n = self.rsi_window
        delta = monthly.groupby("stock_code")["month_close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.groupby(monthly["stock_code"]).transform(
            lambda s: s.ewm(alpha=1 / n, adjust=False).mean())
        avg_loss = loss.groupby(monthly["stock_code"]).transform(
            lambda s: s.ewm(alpha=1 / n, adjust=False).mean())
        rs = avg_gain / avg_loss.replace(0, np.nan)
        monthly["mrsi"] = 100 - 100 / (1 + rs)

        # 月线 RSI 上穿 rsi_min(前一月 RSI <= rsi_min,本月 RSI > rsi_min)
        monthly["mrsi_prev"] = monthly.groupby("stock_code")["mrsi"].shift(1)
        monthly["mrsi_cross_up"] = (
            (monthly["mrsi_prev"] <= self.rsi_min) &
            (monthly["mrsi"] > self.rsi_min)
        ).astype(float)

        # 最近 N 月内是否发生过 RSI 上穿
        monthly["mrsi_cross_recent"] = monthly.groupby("stock_code")["mrsi_cross_up"].transform(
            lambda s: s.rolling(self.cross_recent_months, min_periods=1).max()
        )

        # P-D-017: RSI 拐头 — mrsi 连续 2 个月下降 (拐头出场信号)
        monthly["mrsi_decline_2m"] = (
            (monthly["mrsi"] < monthly["mrsi_prev"]) &
            (monthly["mrsi_prev"] < monthly.groupby("stock_code")["mrsi"].shift(2))
        ).astype(float)

        # 月线 MA3, MA6
        monthly["mma3"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_short, min_periods=2).mean())
        monthly["mma6"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_mid, min_periods=3).mean())

        # 月线多头:close > MA3 > MA6
        monthly["monthly_bullish"] = (
            (monthly["month_close"] > monthly["mma3"]) &
            (monthly["mma3"] > monthly["mma6"])
        ).astype(float)

        # 合并月线指标到日线(同一月的所有日共用同一组月线值)
        monthly_last = monthly.groupby(["stock_code", "month_key"]).last().reset_index()
        # 关键改动:从 merge(on=month_key) 改成 merge_asof(direction=backward)
        # T 日只看到 month_date <= T 的最近一月(上一完整月),当月 indicator 不会出现在 T 日行
        monthly_to_merge = monthly_last[["stock_code", "month_date",
                                         "mrsi", "mrsi_cross_recent", "monthly_bullish",
                                         "mrsi_decline_2m",
                                         "mma3", "mma6"]].sort_values("month_date")
        big = pd.merge_asof(
            big.sort_values("trade_date"),
            monthly_to_merge,
            left_on="trade_date", right_on="month_date",
            by="stock_code", direction="backward",
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        print(f"  [月线 RSI 突破] 特征矩阵: {big.shape}", flush=True)

        # V34: 向量化预计算所有日期的 eligible mask + score + 信号
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """V34: 向量化预计算所有日期的 eligible 候选。

        一次性算出:
        1. eligible mask(_filter_eligible 的多重布尔条件,向量化 AND)
        2. _eligible_by_date = {date: DataFrame[stock_code, 5 因子原值]}

        generate_signals 在 lookup 时:
        - O(1) 取当日 eligible
        - O(K) 过滤 universe
        - O(K) 计算 zscore(与原 _score 完全一致,在 universe ∩ eligible 上标准化)
        - O(K log K) 排序取 top_k

        关键:zscore 在 universe 过滤后计算(与原 _score 完全一致),
        不在预计算阶段做(否则改变标准化总体,导致交易差异)。
        """
        print("  [月线 RSI 突破] 预计算信号矩阵...", flush=True)

        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "mrsi", "mrsi_cross_recent", "monthly_bullish",
                    "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # eligible mask(向量化 AND,替代 _filter_eligible 的顺序过滤)
        # dropna subset: required 减去 is_bullish
        dropna_cols = ["close", "ma5", "ma10", "ma20", "ma60",
                       "today_ret", "volume_ratio_5", "body_ratio",
                       "mrsi", "mrsi_cross_recent", "monthly_bullish",
                       "weekly_above_ma5",
                       "ret_60d", "ret_20d"]
        mask = (
            big[dropna_cols].notna().all(axis=1) &
            big["mrsi_cross_recent"].eq(1) &
            (big["mrsi"] >= self.rsi_min) &
            (big["mrsi"] <= self.rsi_max) &
            big["monthly_bullish"].eq(1) &
            big["weekly_above_ma5"].eq(1) &
            (big["close"] > big["ma20"]) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min)
        )
        # 8. 阳线
        if self.require_bullish:
            mask &= big["is_bullish"].eq(1)

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        score_cols = ["stock_code", "mrsi", "ret_60d", "ret_20d",
                      "volume_ratio_5", "today_ret"]
        score_cols = [c for c in score_cols if c in eligible.columns]
        self._eligible_by_date = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        # 突破型出场:月线多头破位(monthly_bullish==0)或周线多头破位(weekly_above_ma5==0)
        exit_cols = ["stock_code", "trade_date", "close", "ma20",
                     "monthly_bullish", "weekly_above_ma5", "mrsi", "mrsi_decline_2m"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma20": row.ma20,
                "monthly_bullish": getattr(row, "monthly_bullish", 1.0),
                "weekly_above_ma5": getattr(row, "weekly_above_ma5", 1.0),
                "mrsi": getattr(row, "mrsi", None),
                "mrsi_decline_2m": getattr(row, "mrsi_decline_2m", 0.0),
            }

        print(f"  [月线 RSI 突破] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [月线 RSI 突破] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """与原 _score 完全等价:5 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 5 次 cross_sectional_zscore 函数调用开销)。
        """
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["mrsi", "ret_60d", "ret_20d", "volume_ratio_5", "today_ret"]
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight in zip(factors, weights, strict=True):
            if factor not in eligible.columns:
                continue
            s = eligible[factor].to_numpy(dtype=np.float64)
            mask = ~np.isnan(s)
            if mask.sum() < 2:
                continue  # zscore = 0 for all
            mean = s[mask].mean()
            std = s[mask].std()
            if std == 0 or np.isnan(std):
                continue  # zscore = 0 for all
            z = np.where(mask, (s - mean) / std, 0.0)
            z = np.clip(z, -3.0, 3.0)
            scores += z * weight

        return pd.Series(scores, index=eligible["stock_code"].to_numpy())

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        # V34: O(1) 查表替代 O(N) 全表过滤
        if self._eligible_by_date is None:
            self._precompute_features(daily_df)

        eligible = self._eligible_by_date.get(current_date)
        if eligible is None or len(eligible) == 0:
            return []

        universe = self._get_universe(daily_df, current_date)
        if not universe:
            return []
        universe_set = set(universe)

        # O(K) 过滤 universe(K = eligible 数,通常 50-300,远小于 N=3000)
        eligible = eligible[eligible["stock_code"].isin(universe_set)]
        if len(eligible) == 0:
            return []

        # O(K) 计算 zscore(在 universe ∩ eligible 上,与原 _score 完全一致)
        scores = self._score_from_eligible(eligible)
        if len(scores) == 0:
            return []

        # O(K log K) 排序取 top_k
        scores = scores.sort_values(ascending=False)

        signals: list[Signal] = []
        for code, score in scores.items():
            if code in portfolio.positions:
                continue
            signals.append(Signal(code=code, score=float(score),
                                  direction="buy", window="morning",
                                  reason=f"m_rsi_bo={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """信号级出场(突破型):月线多头破位 / 周线多头破位。

        入场条件含 monthly_bullish==1 + weekly_above_ma5==1,反转信号:
        - monthly_bullish == 0:月线 close 跌破 mma3 或 mma3 跌破 mma6
        - weekly_above_ma5 == 0:周线 close 跌破 wma5

        引擎 A2: today_row 实际是 yesterday 数据,today 参数是 yesterday 日期,
        用 yesterday 特征判断,避免前视。
        """
        if self._exit_lookup is None or not self.enable_signal_exit:
            return False
        if self._calendar is None:
            from tradingagents.quant.utils.trading_calendar import get_calendar
            self._calendar = get_calendar()
        holding_days = position.holding_days(today, self._calendar)
        if holding_days < self.exit_min_holding_days:
            return False
        feats = self._exit_lookup.get((position.code, today))
        if feats is None:
            return False

        # P-D-017: RSI 拐头出场 — mrsi 连续 2 个月下降或跌破 55 提前离场 (替代月/周破位)
        if self.rsi_turn_exit:
            mrsi = feats.get("mrsi")
            if pd.notna(mrsi) and float(mrsi) < 55.0:
                return True
            if float(feats.get("mrsi_decline_2m", 0.0)) >= 0.5:
                return True
            return False

        if feats["monthly_bullish"] == 0.0:
            return True
        if feats["weekly_above_ma5"] == 0.0:
            return True
        return False

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "mrsi", "mrsi_cross_recent", "monthly_bullish",
                    "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "mrsi", "mrsi_cross_recent", "monthly_bullish",
                               "weekly_above_ma5",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 月线 RSI 上穿 rsi_min(最近 N 月内)
        df = df[df["mrsi_cross_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 月线 RSI 当前值在 rsi_min-rsi_max 之间(强势但不过热)
        df = df[(df["mrsi"] >= self.rsi_min) & (df["mrsi"] <= self.rsi_max)]
        if len(df) == 0:
            return df

        # 3. 月线多头:close > MA3 > MA6
        df = df[df["monthly_bullish"] == 1]
        if len(df) == 0:
            return df

        # 4. 周线多头:close > MA5
        df = df[df["weekly_above_ma5"] == 1]
        if len(df) == 0:
            return df

        # 5. close > MA20
        df = df[df["close"] > df["ma20"]]
        if len(df) == 0:
            return df

        # 6. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 7. 实体
        df = df[df["body_ratio"] >= self.body_ratio_min]
        if len(df) == 0:
            return df

        # 8. 阳线
        if self.require_bullish:
            df = df[df["is_bullish"] == 1]
            if len(df) == 0:
                return df

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        # 月线 RSI(主因子 1:RSI 越接近 rsi_max 越强,但不过热)
        mrsi = cross_sectional_zscore(col("mrsi", 55.0))
        # 60d 涨幅(主因子 2:中期趋势强度)
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 突破强度
        strength = cross_sectional_zscore(col("today_ret", 0.03))

        scores = (
            mrsi * 0.30 +
            ret_60d * 0.25 +
            ret_20d * 0.20 +
            vol * 0.15 +
            strength * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
