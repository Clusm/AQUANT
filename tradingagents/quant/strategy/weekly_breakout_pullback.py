"""V32-1 波段策略:周线突破回踩(周线突破近 5 日内 + 回踩 MA10 + 月/周线多头 + 日线放量)。

V32 新方向(延伸 V31-3 quarterly_breakout_pullback 新周期 comp 11.71 到周线级别):
- V31-3 q_bo_pb_loose 在新周期 comp 11.71/+179%,验证"突破+回踩"模式极强
- 本策略:周线突破近 5 日内发生 + 当日回踩 MA10 + 月/周线多头 + 日线放量
- 与 V31-3 区别:周期从季度改为周线(更短周期,信号更频繁)

核心思路(周线突破 + 回踩 MA10 + 多头):
- 周线突破(本周 close >= 过去 N 周最高 * 0.98)近 5 日内发生
- 当日回踩 MA10:close 距 MA10 偏离在 [pullback_min, pullback_max](默认 [-1%, +3%])
- MA10 上行:MA10 今日 > MA10 5 日前
- 月线多头:月线 close > 月线 MA3
- 周线多头:周线 close > 周线 MA5
- 日线放量阳线

波段逻辑:周线突破+回踩比追涨风险低,持仓 10d。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class WeeklyBreakoutPullbackStrategy(BaseStrategy):
    """周线突破回踩策略(周线突破近 5 日 + 回踩 MA10 + 月/周线多头 + 日线放量)。"""
    name = "weekly_breakout_pullback"

    def __init__(self, lookback: int = 200, universe_topk: int = 500,
                 weekly_lookback: int = 13,
                 breakout_threshold: float = 0.98,
                 breakout_recent_days: int = 5,
                 pullback_min: float = -0.01,
                 pullback_max: float = 0.03,
                 ma10_uptrend_days: int = 5,
                 monthly_ma_short: int = 3,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_bullish: bool = True,
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 2,
                 exit_stop_loss_pct: float = 0.05,
                 exit_ma_breach_days: int = 3):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.weekly_lookback = weekly_lookback
        self.breakout_threshold = breakout_threshold
        self.breakout_recent_days = breakout_recent_days
        self.pullback_min = pullback_min
        self.pullback_max = pullback_max
        self.ma10_uptrend_days = ma10_uptrend_days
        self.monthly_ma_short = monthly_ma_short
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_bullish = require_bullish
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self.exit_stop_loss_pct = exit_stop_loss_pct
        self.exit_ma_breach_days = exit_ma_breach_days
        self._calendar = None
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] | None = None

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
        import time as _time
        _t0 = _time.time()
        print("  [周线突破回踩] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        # 过滤 < 120 行的股票(与原逻辑一致),然后用向量化函数算全部特征
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 120].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=30, columns=required_feature_columns(self))

        _t2 = _time.time()
        print(f"    build_features_vectorized: {_t2-_t1:.1f}s ({len(big)} 行)", flush=True)
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

        # 周线数据:close=周末close, high=周内最高
        big["week_key"] = big["trade_date"].dt.isocalendar().week.astype(str) + "_" + \
                          big["trade_date"].dt.isocalendar().year.astype(str)
        weekly = big.groupby(["stock_code", "week_key"]).agg(
            week_close=("close", "last"),
            week_high=("high", "max"),
            week_date=("trade_date", "last")
        ).reset_index()
        weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)

        # 过去 N 周最高价(不含本周,shift(1) 后取 rolling max)
        weekly["week_high_N"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(self.weekly_lookback, min_periods=2).max().shift(1)
        )
        # 本周 close 突破过去 N 周最高 * threshold
        weekly["weekly_breakout"] = (
            weekly["week_close"] >= weekly["week_high_N"] * self.breakout_threshold
        ).astype(float)
        # 突破强度
        weekly["weekly_breakout_strength"] = (
            weekly["week_close"] / weekly["week_high_N"].replace(0, np.nan) - 1.0
        )

        # 周线 MA5
        weekly["wma5"] = weekly.groupby("stock_code")["week_close"].transform(
            lambda s: s.rolling(5, min_periods=3).mean())
        weekly["weekly_above_ma5"] = (weekly["week_close"] > weekly["wma5"]).astype(float)

        # 合并周线指标到日线
        weekly_last = weekly.groupby(["stock_code", "week_key"]).last().reset_index()
        weekly_to_merge = weekly_last[["stock_code", "week_date",
                         "weekly_breakout", "weekly_breakout_strength",
                         "week_high_N", "week_close",
                         "weekly_above_ma5", "wma5"]].sort_values("week_date")
        big = pd.merge_asof(
            big.sort_values("trade_date"),
            weekly_to_merge,
            left_on="trade_date", right_on="week_date",
            by="stock_code", direction="backward",
        )

        # 周线突破近 N 日内发生(日线级别 rolling)
        big["weekly_breakout_recent"] = big.groupby("stock_code")["weekly_breakout"].transform(
            lambda s: s.rolling(self.breakout_recent_days, min_periods=1).max()
        )

        # 当日回踩 MA10:close 距 MA10 偏离
        big["pullback_to_ma10"] = (big["close"] - big["ma10"]) / big["ma10"].replace(0, np.nan)
        big["pullback_valid"] = (
            (big["pullback_to_ma10"] >= self.pullback_min) &
            (big["pullback_to_ma10"] <= self.pullback_max)
        ).astype(float)

        # MA10 上行
        big["ma10_prev_N"] = big.groupby("stock_code")["ma10"].shift(self.ma10_uptrend_days)
        big["ma10_uptrend"] = (big["ma10"] > big["ma10_prev_N"]).astype(float)

        # 月线数据
        big["month_key"] = big["trade_date"].dt.year.astype(str) + "_" + \
                           big["trade_date"].dt.month.astype(str).str.zfill(2)
        monthly = big.groupby(["stock_code", "month_key"]).agg(
            month_close=("close", "last"),
            month_date=("trade_date", "last")
        ).reset_index()
        monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)
        monthly["mma3"] = monthly.groupby("stock_code")["month_close"].transform(
            lambda s: s.rolling(self.monthly_ma_short, min_periods=2).mean())
        monthly["monthly_above_ma3"] = (monthly["month_close"] > monthly["mma3"]).astype(float)
        monthly_last = monthly.groupby(["stock_code", "month_key"]).last().reset_index()
        monthly_to_merge = monthly_last[["stock_code", "month_date", "monthly_above_ma3", "mma3"]].sort_values("month_date")
        big = pd.merge_asof(
            big.sort_values("trade_date"),
            monthly_to_merge,
            left_on="trade_date", right_on="month_date",
            by="stock_code", direction="backward",
        )

        # 60d 涨幅
        big["ret_60d"] = big.groupby("stock_code")["close"].pct_change(60)

        self._feature_cache = big
        _t3 = _time.time()
        print(f"  [周线突破回踩] 特征矩阵: {big.shape}", flush=True)
        print(f"  [周线突破回踩] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s concat+聚合={_t3-_t2:.1f}s", flush=True)

        # V34: 向量化预计算所有日期的 eligible mask + 信号
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """V34: 向量化预计算所有日期的 eligible 候选。

        一次性算出:
        1. eligible mask(8 重布尔条件,向量化 AND)
        2. _eligible_by_date = {date: DataFrame[stock_code, 5 因子原值]}

        generate_signals 在 lookup 时:
        - O(1) 取当日 eligible
        - O(K) 过滤 universe
        - O(K) 计算 zscore(与原 _score 完全一致,在 universe ∩ eligible 上标准化)
        - O(K log K) 排序取 top_k

        关键:zscore 在 universe 过滤后计算(与原 _score 完全一致),
        不在预计算阶段做(否则改变标准化总体,导致交易差异)。
        """
        print("  [周线突破回踩] 预计算信号矩阵...", flush=True)

        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "weekly_breakout", "weekly_breakout_strength", "week_high_N",
                    "weekly_breakout_recent", "pullback_to_ma10", "pullback_valid", "ma10_uptrend",
                    "monthly_above_ma3", "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # 1. eligible mask(向量化 AND,替代 _filter_eligible 的 8 重顺序过滤)
        mask = (
            big[required].notna().all(axis=1) &
            big["weekly_breakout_recent"].eq(1) &
            big["pullback_valid"].eq(1) &
            big["ma10_uptrend"].eq(1) &
            big["monthly_above_ma3"].eq(1) &
            big["weekly_above_ma5"].eq(1) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min)
        )
        if self.require_bullish:
            mask &= big["is_bullish"].eq(1)

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # 2. _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        # 只存评分需要的列(省内存),zscore 留到 lookup 时算(保证与原 _score 总体一致)
        score_cols = ["stock_code", "weekly_breakout_strength",
                      "pullback_to_ma10", "ret_60d", "ret_20d", "volume_ratio_5"]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # _exit_lookup: {(code, date): dict} - should_exit 用
        # V3 单组件出场:保护性止损 + MA5 破位(保留 max_holding,不取消)
        exit_cols = ["stock_code", "trade_date", "close", "ma5", "ma20",
                     "volume_ratio_5", "is_bullish",
                     "monthly_above_ma3", "weekly_above_ma5"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma5"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma5": row.ma5,
                "ma20": getattr(row, "ma20", None),
                "volume_ratio_5": getattr(row, "volume_ratio_5", None),
                "is_bullish": getattr(row, "is_bullish", 1.0),
                "monthly_above_ma3": getattr(row, "monthly_above_ma3", 1.0),
                "weekly_above_ma5": getattr(row, "weekly_above_ma5", 1.0),
            }

        print(f"  [周线突破回踩] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [周线突破回踩] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
                                  reason=f"w_bo_pb={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """V3 单组件出场:保护性止损 5% + MA5 破位(持仓 >= exit_ma_breach_days)。

        保留 max_holding 作为主出场,本方法仅作异常保护。
        不启用 V3 的涨幅回吐/放量阴线(已证明导致过度交易)。

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

        close = feats["close"]
        entry_price = position.entry_price

        # 1. 保护性止损:close < entry * (1 - stop_loss_pct)
        if pd.notna(close) and entry_price > 0:
            if close < entry_price * (1 - self.exit_stop_loss_pct):
                return True

        # 2. MA5 破位:close < MA5(持仓 >= exit_ma_breach_days 后检查)
        if holding_days >= self.exit_ma_breach_days:
            ma5 = feats.get("ma5")
            if pd.notna(ma5) and pd.notna(close) and close < ma5:
                return True

        return False

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """与原 _score 完全等价:5 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 5 次 cross_sectional_zscore 函数调用开销)。
        pullback_to_ma10 取负(回踩越深越加分)。
        """
        import numpy as np
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["weekly_breakout_strength", "pullback_to_ma10", "ret_60d",
                   "ret_20d", "volume_ratio_5"]
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]
        signs = [1.0, -1.0, 1.0, 1.0, 1.0]  # pullback_to_ma10 取负

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight, sign in zip(factors, weights, signs, strict=True):
            if factor not in eligible.columns:
                continue
            s = eligible[factor].to_numpy(dtype=np.float64) * sign
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

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "weekly_breakout", "weekly_breakout_strength", "week_high_N",
                    "weekly_breakout_recent", "pullback_to_ma10", "pullback_valid", "ma10_uptrend",
                    "monthly_above_ma3", "weekly_above_ma5",
                    "ret_60d", "ret_20d"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=["close", "ma5", "ma10", "ma20", "ma60",
                               "today_ret", "volume_ratio_5", "body_ratio",
                               "weekly_breakout", "weekly_breakout_strength", "week_high_N",
                               "weekly_breakout_recent", "pullback_to_ma10", "pullback_valid", "ma10_uptrend",
                               "monthly_above_ma3", "weekly_above_ma5",
                               "ret_60d", "ret_20d"])
        if len(df) == 0:
            return df

        # 1. 周线突破近 N 日内
        df = df[df["weekly_breakout_recent"] == 1]
        if len(df) == 0:
            return df

        # 2. 回踩 MA10
        df = df[df["pullback_valid"] == 1]
        if len(df) == 0:
            return df

        # 3. MA10 上行
        df = df[df["ma10_uptrend"] == 1]
        if len(df) == 0:
            return df

        # 4. 月线多头
        df = df[df["monthly_above_ma3"] == 1]
        if len(df) == 0:
            return df

        # 5. 周线多头
        df = df[df["weekly_above_ma5"] == 1]
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

        # 周线突破强度(主因子 1)
        strength_w = cross_sectional_zscore(col("weekly_breakout_strength", 0.02))
        # 回踩深度(主因子 2:回踩越深越加分)
        pullback_depth = cross_sectional_zscore(-col("pullback_to_ma10", 0.01))
        # 60d 涨幅
        ret_60d = cross_sectional_zscore(col("ret_60d", 0.10))
        # 20d 涨幅
        ret_20d = cross_sectional_zscore(col("ret_20d", 0.10))
        # T 日放量
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))

        scores = (
            strength_w * 0.30 +
            pullback_depth * 0.25 +
            ret_60d * 0.20 +
            ret_20d * 0.15 +
            vol * 0.10
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
