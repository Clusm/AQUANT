"""龙头股回调到 MA 反弹。

龙头股(wave1 >= 50%)+ 首次回调到 MA20/MA10 ±2% + T 日反弹确认
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class LeaderPullbackBounceStrategy(BaseStrategy):
    """龙头股回调到 MA 反弹。

    龙头股(wave1 >= 50%)+ 首次回调到 MA20/MA10 ±2% + T 日反弹确认
    """
    name = "leader_pullback_bounce"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 wave1_min: float = 0.50,
                 wave1_lookback: int = 30,
                 ma_target: str = "ma20",
                 ma_band: float = 0.02,
                 today_ret_min: float = 0.02,
                 today_vol_min: float = 1.3,
                 body_ratio_min: float = 0.4,
                 require_above_ma: str = "ma20",
                 enable_signal_exit: bool = True,
                 exit_min_holding_days: int = 2,
                 exit_wave1_floor: float = 0.25,
                 wave1_freshness_days: int = 999):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.wave1_min = wave1_min
        self.wave1_lookback = wave1_lookback
        self.ma_target = ma_target
        self.ma_band = ma_band
        self.today_ret_min = today_ret_min
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_above_ma = require_above_ma
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self.exit_wave1_floor = exit_wave1_floor
        self.wave1_freshness_days = wave1_freshness_days
        self._calendar = None  # lazy load in should_exit
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
        print("  [龙头回调反弹] 预计算特征矩阵...", flush=True)
        # V34: 向量化 build_features_vectorized 替代串行循环
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 30].index
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

        N = self.wave1_lookback
        big["close_max_N"] = big.groupby("stock_code")["close"].transform(
            lambda s: s.rolling(N, min_periods=N // 2).max()
        )
        big["close_min_N"] = big.groupby("stock_code")["close"].transform(
            lambda s: s.rolling(N, min_periods=N // 2).min()
        )
        big["wave1_gain"] = (big["close_max_N"] / big["close_min_N"].replace(0, np.nan)) - 1.0

        # P-B-026: 回踩新鲜度 — wave1 高点 (N 日新高) 后 freshness_days 日内回踩有效
        big["is_peak"] = (big["close"] >= big["close_max_N"]).astype(float)
        big["peak_recent"] = big.groupby("stock_code")["is_peak"].transform(
            lambda s: s.rolling(self.wave1_freshness_days, min_periods=1).max())

        big["prev_close"] = big.groupby("stock_code")["close"].shift(1)
        big[f"prev_{self.ma_target}"] = big.groupby("stock_code")[self.ma_target].shift(1)
        big["prev_close_to_ma"] = (
            big["prev_close"] - big[f"prev_{self.ma_target}"]
        ) / big[f"prev_{self.ma_target}"].replace(0, np.nan)
        big["close_above_ma"] = (big["close"] > big[self.ma_target]).astype(float)

        self._feature_cache = big
        print(f"  [龙头回调反弹] 特征矩阵: {big.shape}", flush=True)

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
        print("  [龙头回调反弹] 预计算信号矩阵...", flush=True)

        required = ["close", "open", "high", "low", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "wave1_gain", "prev_close_to_ma", "close_above_ma"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                return

        # eligible mask(向量化 AND,替代 _filter_eligible 的顺序过滤)
        # dropna subset: required 减去 (is_bullish, close_above_ma)
        dropna_cols = ["close", "open", "high", "low", "ma20", "ma60",
                       "today_ret", "volume_ratio_5", "body_ratio",
                       "wave1_gain", "prev_close_to_ma"]
        mask = (
            big[dropna_cols].notna().all(axis=1) &
            (big["wave1_gain"] >= self.wave1_min) &
            (big["prev_close_to_ma"] >= -self.ma_band) &
            (big["prev_close_to_ma"] <= self.ma_band) &
            big["close_above_ma"].eq(1) &
            (big["today_ret"] >= self.today_ret_min) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["body_ratio"] >= self.body_ratio_min) &
            big["is_bullish"].eq(1)
        )
        # P-B-026: 回踩新鲜度门 (wave1 高点后 wave1_freshness_days 日内)
        if self.wave1_freshness_days != 999:
            mask &= big["peak_recent"].eq(1)
        if self.require_above_ma == "ma20":
            mask &= big["close"] > big["ma20"]
        elif self.require_above_ma == "ma60":
            mask &= big["close"] > big["ma60"]

        eligible = big[mask].copy()
        if len(eligible) < 2:
            self._eligible_by_date = {}
            return

        # _eligible_by_date: {date: DataFrame[stock_code, 5 因子原值]}
        # 只存评分需要的列(省内存),zscore 留到 lookup 时算(保证与原 _score 总体一致)
        score_cols = ["stock_code", "wave1_gain", "today_ret",
                      "volume_ratio_5", "body_ratio", "close_to_ma20"]
        score_cols = [c for c in score_cols if c in eligible.columns]
        self._eligible_by_date = {}
        for date, grp in eligible.groupby("trade_date", sort=False):
            self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)

        # _exit_lookup: {(code, date): dict} - should_exit 用,O(1) 查询
        # 只存出场判断需要的字段:close, ma20, wave1_gain
        exit_cols = ["stock_code", "trade_date", "close", "ma20", "wave1_gain"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20", "wave1_gain"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma20": row.ma20,
                "wave1_gain": row.wave1_gain,
            }

        print(f"  [龙头回调反弹] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        print(f"  [龙头回调反弹] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

    def should_exit(self, position, today_row, today):
        """信号级出场:跌破 MA20 支撑 / wave1 涨幅大幅回吐。

        反弹策略的出场逻辑:
        - 跌破 MA20(入场支撑位):反弹失败,认错
        - wave1_gain < exit_wave1_floor(默认 0.25):龙头强势消失,继续持有的逻辑不成立

        引擎 A2: today_row 实际是 yesterday 数据,today 参数是 yesterday 日期,
        用 yesterday 特征判断,避免前视。
        """
        if self._exit_lookup is None or not self.enable_signal_exit:
            return False
        # 持仓时间门槛(避开买入当天的波动)
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
        wave1_gain = feats["wave1_gain"]

        # 1. 跌破 MA20 支撑(入场条件 close > MA20 的反转)
        if pd.notna(close) and pd.notna(ma20) and close < ma20:
            return True
        # 2. wave1 涨幅大幅回吐(龙头强势消失)
        # 入场时 wave1_gain >= 0.50,若当前 < 0.25 说明涨幅回吐过半
        if pd.notna(wave1_gain) and wave1_gain < self.exit_wave1_floor:
            return True
        return False

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """与原 _score 完全等价:5 因子 cross_sectional_zscore 加权。

        原逻辑:_score(feats) 对 feats(已经是 universe ∩ eligible)做 zscore。
        本方法:对 eligible(已经是 universe ∩ eligible)做 zscore,总体一致。

        V34: numpy 向量化实现(避免 5 次 cross_sectional_zscore 函数调用开销)。
        """
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["wave1_gain", "today_ret", "volume_ratio_5",
                   "body_ratio", "close_to_ma20"]
        weights = [0.25, 0.30, 0.20, 0.10, 0.15]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight in zip(factors, weights, strict=True):
            if factor not in eligible.columns:
                continue  # 与原 _score 的 "close_to_ma20" in feats.columns 检查一致
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
                                  reason=f"lpb={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "open", "high", "low", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "wave1_gain", "prev_close_to_ma", "close_above_ma"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=[c for c in required if c not in ("is_bullish", "close_above_ma")])
        if len(df) == 0:
            return df

        df = df[df["wave1_gain"] >= self.wave1_min]
        df = df[(df["prev_close_to_ma"] >= -self.ma_band) &
                (df["prev_close_to_ma"] <= self.ma_band)]
        df = df[df["close_above_ma"] == 1]
        df = df[df["today_ret"] >= self.today_ret_min]
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
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

        wave1 = cross_sectional_zscore(col("wave1_gain", 0.5))
        bounce = cross_sectional_zscore(col("today_ret"))
        volume = cross_sectional_zscore(col("volume_ratio_5", 1.3))
        body = cross_sectional_zscore(col("body_ratio", 0.4))
        if "close_to_ma20" in feats.columns:
            trend = cross_sectional_zscore(col("close_to_ma20", 0.0))
        else:
            trend = pd.Series(0.0, index=feats.index)

        scores = (
            wave1 * 0.25 +
            bounce * 0.30 +
            volume * 0.20 +
            body * 0.10 +
            trend * 0.15
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
