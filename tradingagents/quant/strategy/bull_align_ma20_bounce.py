"""方向 HHH:多头排列回踩 MA20 反弹。

结合 II(多头排列) + QQ(回踩 MA 反弹) 思路:
- 多头排列(MA5 > MA10 > MA20 > MA60):趋势向上
- T-1 close 在 MA20 ±2%:回踩支撑
- T 日 close > MA20:回到 MA20 上方,反弹确认
- T 日放量阳线:反弹动能确认

入场条件(T 日收盘后判定,T+1 撮合):
1. 多头排列(MA5 > MA10 > MA20 > MA60)
2. T-1 close 在 MA20 ±2% 范围
3. T 日 close > MA20(回到 MA20 上方)
4. T 日 today_ret >= +2%
5. T 日放量:volume_ratio_5 >= 1.3
6. T 日实体阳线:body_ratio >= 0.4
7. close > MA20(趋势 intact)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class BullAlignMa20BounceStrategy(BaseStrategy):
    """多头排列 + 回踩 MA20 反弹。"""
    name = "bull_align_ma20_bounce"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 ma_band: float = 0.02,
                 today_ret_min: float = 0.02,
                 today_vol_min: float = 1.3,
                 body_ratio_min: float = 0.4,
                 require_above_ma: str = "ma20",
                 require_full_align: bool = True,
                 bounce_freshness_days: int = 999,
                 enable_signal_exit: bool = False,
                 exit_min_holding_days: int = 2):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.ma_band = ma_band
        self.today_ret_min = today_ret_min
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_above_ma = require_above_ma
        self.require_full_align = require_full_align
        self.bounce_freshness_days = bounce_freshness_days
        self.enable_signal_exit = enable_signal_exit
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
        import time as _time
        _t0 = _time.time()
        print("  [多头排列MA20反弹] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        _t1 = _time.time()

        # 过滤 < 30 行的股票(与原逻辑一致),然后用向量化函数算全部特征
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 30].index
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

        # 多头排列
        big["bull_align_full"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"]) &
            (big["ma20"] > big["ma60"])
        ).astype(float)
        big["bull_align_3"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"])
        ).astype(float)

        # T-1 close 到 MA20 距离
        big["prev_close"] = big.groupby("stock_code")["close"].shift(1)
        big["prev_ma20"] = big.groupby("stock_code")["ma20"].shift(1)
        big["prev_close_to_ma20"] = (
            big["prev_close"] - big["prev_ma20"]
        ) / big["prev_ma20"].replace(0, np.nan)
        big["close_above_ma20"] = (big["close"] > big["ma20"]).astype(float)

        # P-B-011: 反弹新鲜度 — MA20 上穿后 freshness_days 日内反弹有效
        big["ma20_cross_up"] = (
            (big["close"] > big["ma20"]) &
            (big.groupby("stock_code")["close_above_ma20"].shift(1).eq(0))
        ).astype(float)
        big["ma20_cross_recent"] = big.groupby("stock_code")["ma20_cross_up"].transform(
            lambda s: s.rolling(self.bounce_freshness_days, min_periods=1).max())

        # V34: 预计算评分用复合因子 _align_strength = (ma5 - ma20) / ma20
        # (原 _score 中 align_strength 是动态计算的,这里预先算好以便 _score_from_eligible 直接用)
        big["_align_strength"] = (
            (big["ma5"] - big["ma20"]) / big["ma20"].replace(0, np.nan)
        )

        self._feature_cache = big
        _t3 = _time.time()
        print(f"  [多头排列MA20反弹] 特征矩阵: {big.shape}", flush=True)
        print(f"  [多头排列MA20反弹] 耗时分解: sort={_t1-_t0:.1f}s build_features={_t2-_t1:.1f}s concat+聚合={_t3-_t2:.1f}s", flush=True)

        # V34: 向量化预计算所有日期的 eligible mask + 信号
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """V34: 向量化预计算所有日期的 eligible 候选。

        一次性算出:
        1. eligible mask(7 重布尔条件,向量化 AND)
        2. _eligible_by_date = {date: DataFrame[stock_code, 5 因子原值]}

        generate_signals 在 lookup 时:
        - O(1) 取当日 eligible
        - O(K) 过滤 universe
        - O(K) 计算 zscore(与原 _score 完全一致,在 universe ∩ eligible 上标准化)
        - O(K log K) 排序取 top_k

        关键:zscore 在 universe 过滤后计算(与原 _score 完全一致),
        不在预计算阶段做(否则改变标准化总体,导致交易差异)。
        """
        print("  [多头排列MA20反弹] 预计算信号矩阵...", flush=True)

        # dropna subset 与原 _filter_eligible 一致(排除 is_bullish/close_above_ma20/bull_align_*)
        dropna_cols = ["close", "open", "high", "low", "ma5", "ma10", "ma20", "ma60",
                       "today_ret", "volume_ratio_5", "body_ratio", "prev_close_to_ma20",
                       "bull_align_full", "bull_align_3", "close_above_ma20", "is_bullish",
                       "_align_strength"]
        for c in dropna_cols:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # 1. eligible mask(向量化 AND,替代 _filter_eligible 的 7 重顺序过滤)
        mask = big[dropna_cols].notna().all(axis=1)
        # P-B-011: 反弹新鲜度门 (MA20 上穿后 bounce_freshness_days 日内)
        if self.bounce_freshness_days != 999:
            mask &= big["ma20_cross_recent"].eq(1)
        # 1. 多头排列
        if self.require_full_align:
            mask &= big["bull_align_full"].eq(1)
        else:
            mask &= big["bull_align_3"].eq(1)
        # 2. T-1 close 在 MA20 ±ma_band
        mask &= (big["prev_close_to_ma20"] >= -self.ma_band) & \
                (big["prev_close_to_ma20"] <= self.ma_band)
        # 3. close > MA20
        mask &= big["close_above_ma20"].eq(1)
        # 4. T 日上涨
        mask &= (big["today_ret"] >= self.today_ret_min)
        # 5. 放量
        mask &= (big["volume_ratio_5"] >= self.today_vol_min)
        # 6. 实体
        mask &= (big["body_ratio"] >= self.body_ratio_min)
        # 7. 阳线(原代码无 require_bullish 开关,恒为 True)
        mask &= big["is_bullish"].eq(1)
        # 8. close > MA
        if self.require_above_ma == "ma20":
            mask &= (big["close"] > big["ma20"])
        elif self.require_above_ma == "ma60":
            mask &= (big["close"] > big["ma60"])

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # 2. _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        # 只存评分需要的列(省内存),zscore 留到 lookup 时算(保证与原 _score 总体一致)
        # close_to_ma20 来自 build_features_vectorized(已验证),trend 因子可用
        score_cols = ["stock_code", "today_ret", "volume_ratio_5", "body_ratio",
                      "close_to_ma20", "_align_strength"]
        score_cols = [c for c in score_cols if c in eligible.columns]
        self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        # 反弹型出场:close < MA20(跌破入场支撑)或 bull_align_3 == 0(多头排列破位)
        exit_cols = ["stock_code", "trade_date", "close", "ma20", "bull_align_3"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma20": row.ma20,
                "bull_align_3": getattr(row, "bull_align_3", 1.0),
            }

        print(f"  [多头排列MA20反弹] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [多头排列MA20反弹] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

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
        if len(eligible) < 5:
            return []

        # O(K) 计算 zscore(在 universe ∩ eligible 上,与原 _score 完全一致)
        scores = self._score_from_eligible(eligible)
        if len(scores) == 0:
            return []

        # O(K log K) 排序取 top_k * 2 候选(与原 .head(top_k * 2) 一致)
        scores = scores.sort_values(ascending=False)

        signals: list[Signal] = []
        for code, score in scores.head(top_k * 2).items():
            if code in portfolio.positions:
                continue
            signals.append(Signal(code=code, score=float(score),
                                  direction="buy", window="morning",
                                  reason=f"bamb={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """信号级出场(反弹型):跌破 MA20 支撑 / 多头排列破位。

        反弹策略的入场条件含 close > MA20 + 多头排列,反转信号:
        - close < MA20:跌破入场支撑,反弹失败
        - bull_align_3 == 0:MA5/MA10/MA20 多头排列破位,趋势反转

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
        ma20 = feats["ma20"]
        if pd.notna(close) and pd.notna(ma20) and close < ma20:
            return True
        if feats["bull_align_3"] == 0.0:
            return True
        return False

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """与原 _score 完全等价:5 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 5 次 cross_sectional_zscore 函数调用开销)。
        因子:today_ret / volume_ratio_5 / body_ratio / close_to_ma20 / _align_strength
        (close_to_ma20 若不存在则该因子贡献 0,与原 if-else 逻辑一致)
        无取负因子。
        """
        import numpy as np
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["today_ret", "volume_ratio_5", "body_ratio",
                   "close_to_ma20", "_align_strength"]
        weights = [0.30, 0.20, 0.15, 0.15, 0.20]
        signs = [1.0, 1.0, 1.0, 1.0, 1.0]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight, sign in zip(factors, weights, signs, strict=True):
            if factor not in eligible.columns:
                continue  # 与原 if "close_to_ma20" in feats.columns else 0 一致
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
        required = ["close", "open", "high", "low", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "prev_close_to_ma20", "close_above_ma20",
                    "bull_align_full", "bull_align_3"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=[c for c in required if c not in
                               ("is_bullish", "close_above_ma20", "bull_align_full", "bull_align_3")])
        if len(df) == 0:
            return df

        # 1. 多头排列
        if self.require_full_align:
            df = df[df["bull_align_full"] == 1]
        else:
            df = df[df["bull_align_3"] == 1]
        # 2. T-1 close 在 MA20 ±2%
        df = df[(df["prev_close_to_ma20"] >= -self.ma_band) &
                (df["prev_close_to_ma20"] <= self.ma_band)]
        # 3. T 日 close > MA20
        df = df[df["close_above_ma20"] == 1]
        # 4. T 日上涨
        df = df[df["today_ret"] >= self.today_ret_min]
        # 5. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        # 6. 实体阳线
        df = df[df["body_ratio"] >= self.body_ratio_min]
        df = df[df["is_bullish"] == 1]

        if self.require_above_ma == "ma20":
            df = df[df["close"] > df["ma20"]]
        elif self.require_above_ma == "ma60":
            df = df[df["close"] > df["ma60"]]

        return df

    def _score(self, feats: pd.DataFrame) -> pd.Series:
        if len(feats) < 2:
            return pd.Series(dtype=float)

        def col(name, default=0.0):
            return feats[name].fillna(default) if name in feats.columns else pd.Series(default, index=feats.index)

        bounce = cross_sectional_zscore(col("today_ret"))
        volume = cross_sectional_zscore(col("volume_ratio_5", 1.3))
        body = cross_sectional_zscore(col("body_ratio", 0.4))
        if "close_to_ma20" in feats.columns:
            trend = cross_sectional_zscore(col("close_to_ma20", 0.0))
        else:
            trend = pd.Series(0.0, index=feats.index)
        # 多头排列强度:MA5 - MA20 距离
        align_strength = cross_sectional_zscore((col("ma5", 0) - col("ma20", 0)) / col("ma20", 1.0))

        scores = (
            bounce * 0.30 +
            volume * 0.20 +
            body * 0.15 +
            trend * 0.15 +
            align_strength * 0.20
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
