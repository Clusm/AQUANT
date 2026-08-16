"""中线策略 V26: 量价趋势确认 (Volume-Price Trend Confirmation)。

核心思路（趋势 + 量能确认，解决纯技术指标假突破问题）:
- 中长线趋势必须被成交量确认：上涨有量，回调缩量
- 量价背离 = 趋势减弱/反转信号
- 量能持续活跃 = 趋势健康

与纯价格趋势策略的区别:
- MaTrendFollow: 只看均线排列 + 量能温和（不关注量价关系）
- 本策略: 用量确认趋势有效性，量价背离时退出

入场条件:
- 中期趋势: close > MA20（中级趋势向上）
- 长期趋势: close > MA60（大趋势向上）
- 量能确认: volume_ratio_20 > 1.0（20日均量放量）
- 量能趋势: 近5日平均量比 > 1.0（持续活跃）
- 价格动量: ret_5d > 0（近期上涨）
- 趋势强度: MA5 > MA10（短期均线多头）
- 不追高: close_to_ma5 在合理范围内

评分因子:
- 趋势强度: close_to_ma20（经标准化）
- 量能确认: volume_ratio_5（当日量比）
- 量价匹配: volume_ratio_5 × ret_5d（量价同向）
- 量能持续性: turnover_zscore_20（20日换手率zscore）

出场条件:
- close < MA20（中期趋势破位）
- 量价背离: price up but volume declining（volume_ratio_5 < 0.7）
- 量能枯竭: turnover_zscore_20 < -1.0（量能持续萎缩）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.base import BaseStrategy


class VolumePriceTrendStrategy(BaseStrategy):
    """量价趋势确认策略（中线版）。"""
    name = "volume_price_trend"

    def __init__(self, lookback: int = 200, universe_topk: int = 500,
                 # 趋势条件
                 require_above_ma: str = "ma60",
                 require_ma5_gt_ma10: bool = True,
                 # 量能条件
                 vol_ratio_20_min: float = 1.0,
                 vol_ratio_5d_avg_min: float = 1.0,
                 # 动量条件
                 ret_5d_min: float = 0.01,
                 ret_5d_max: float = 0.12,
                 # 入场参数
                 close_to_ma5_max: float = 0.05,
                 body_ratio_min: float = 0.0,
                 # 出场参数
                 enable_signal_exit: bool = True,
                 exit_min_holding_days: int = 15,
                 exit_vol_ratio_min: float = 0.7,
                 # 其他
                 today_vol_min: float = 0.8):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.require_above_ma = require_above_ma
        self.require_ma5_gt_ma10 = require_ma5_gt_ma10
        self.vol_ratio_20_min = vol_ratio_20_min
        self.vol_ratio_5d_avg_min = vol_ratio_5d_avg_min
        self.ret_5d_min = ret_5d_min
        self.ret_5d_max = ret_5d_max
        self.close_to_ma5_max = close_to_ma5_max
        self.body_ratio_min = body_ratio_min
        self.enable_signal_exit = enable_signal_exit
        self.exit_min_holding_days = exit_min_holding_days
        self.exit_vol_ratio_min = exit_vol_ratio_min
        self.today_vol_min = today_vol_min
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
        print("  [量价趋势] 预计算特征矩阵...", flush=True)
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        counts = sorted_df.groupby("stock_code", observed=True).size()
        valid_codes = counts[counts >= 60].index
        valid_df = sorted_df[sorted_df["stock_code"].isin(valid_codes)]
        big = build_features_vectorized(valid_df, min_rows=60, columns=required_feature_columns(self))

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

        # 量价特征
        g = big.groupby("stock_code", sort=False)

        # volume_ratio_20: 20日均量比
        big["volume_ratio_20"] = big["volume"] / g["volume"].transform(
            lambda s: s.rolling(20, min_periods=10).mean().shift(1)
        ).replace(0, np.nan)

        # volume_ratio_5d_avg: 近5日平均量比
        big["vol_ratio_5d_avg"] = g["volume_ratio_5"].transform(
            lambda s: s.rolling(5, min_periods=3).mean()
        )

        # 量价匹配: volume_ratio_5 × ret_5d
        big["vol_price_alignment"] = big["volume_ratio_5"] * big["ret_5d"]

        # 量价背离信号
        big["vol_price_divergence"] = (
            (big["ret_5d"] > 0.02) & (big["volume_ratio_5"] < 0.8)
        ).astype(float)

        # 量能趋势: 近5日量比 vs 近20日量比
        big["vol_trend"] = big["volume_ratio_5"] - big["volume_ratio_20"]

        # MA5 > MA10
        big["ma5_gt_ma10"] = (big["ma5"] > big["ma10"]).astype(float)

        # 脱离 MA20 距离（趋势强度）
        big["close_to_ma20_pct"] = (big["close"] - big["ma20"]) / big["ma20"].replace(0, np.nan)

        # 脱离 MA60 距离
        big["close_to_ma60_pct"] = (big["close"] - big["ma60"]) / big["ma60"].replace(0, np.nan)

        self._feature_cache = big
        self._precompute_signals(big)
        return big

    def _precompute_signals(self, big: pd.DataFrame):
        """向量化预计算 eligible 和 exit_lookup。"""
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "ret_5d", "volume_ratio_5", "volume_ratio_20",
                    "vol_ratio_5d_avg", "body_ratio", "is_bullish",
                    "close_to_ma5", "close_to_ma20_pct", "close_to_ma60_pct",
                    "ma5_gt_ma10", "vol_price_alignment", "vol_trend"]
        for c in required:
            if c not in big.columns:
                self._eligible_by_date = {}
                self._exit_lookup = {}
                return

        # 入场条件: 向量化 mask
        mask = (
            big[required].notna().all(axis=1) &
            (big["close_to_ma5"].abs() <= self.close_to_ma5_max) &
            (big["volume_ratio_20"] >= self.vol_ratio_20_min) &
            (big["vol_ratio_5d_avg"] >= self.vol_ratio_5d_avg_min) &
            (big["volume_ratio_5"] >= self.today_vol_min) &
            (big["ret_5d"] >= self.ret_5d_min) &
            (big["ret_5d"] <= self.ret_5d_max) &
            (big["body_ratio"] >= self.body_ratio_min) &
            (big["close"] > big["ma5"])
        )
        if self.require_ma5_gt_ma10:
            mask &= big["ma5_gt_ma10"].eq(1)
        if self.require_above_ma == "ma20":
            mask &= (big["close"] > big["ma20"])
        elif self.require_above_ma == "ma60":
            mask &= (big["close"] > big["ma60"])

        eligible = big[mask].copy()
        if len(eligible) >= 2:
            score_cols = ["stock_code", "close_to_ma20_pct", "volume_ratio_5",
                          "vol_price_alignment", "vol_trend", "close_to_ma5"]
            self._eligible_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
            for date, grp in eligible.groupby("trade_date", sort=False):
                self._eligible_by_date[date] = grp[score_cols].reset_index(drop=True)
            print(f"  [量价趋势] 信号矩阵: {len(self._eligible_by_date)} 个交易日有信号", flush=True)
        else:
            self._eligible_by_date = {}

        # exit_lookup: 出场条件查表
        exit_cols = ["stock_code", "trade_date", "close", "ma20", "ma60",
                     "volume_ratio_5", "vol_ratio_5d_avg"]
        exit_cols = [c for c in exit_cols if c in big.columns]
        exit_df = big[exit_cols].dropna(subset=["close", "ma20"])
        self._exit_lookup: dict[tuple, dict] = {}
        for row in exit_df.itertuples(index=False):
            self._exit_lookup[(row.stock_code, row.trade_date)] = {
                "close": row.close,
                "ma20": row.ma20,
                "ma60": getattr(row, "ma60", None),
                "volume_ratio_5": getattr(row, "volume_ratio_5", 1.0),
                "vol_ratio_5d_avg": getattr(row, "vol_ratio_5d_avg", 1.0),
            }
        print(f"  [量价趋势] 出场查表: {len(self._exit_lookup)} 条 (code,date) 记录", flush=True)

    def _score_from_eligible(self, eligible: pd.DataFrame) -> pd.Series:
        """4 因子 zscore 加权评分。"""
        n = len(eligible)
        if n < 2:
            return pd.Series(dtype=float)

        factors = ["close_to_ma20_pct", "volume_ratio_5",
                   "vol_price_alignment", "vol_trend"]
        weights = [0.35, 0.25, 0.25, 0.15]
        signs = [1.0, 1.0, 1.0, 1.0]

        scores = np.zeros(n, dtype=np.float64)
        for factor, weight, sign in zip(factors, weights, signs, strict=True):
            if factor not in eligible.columns:
                continue
            s = eligible[factor].to_numpy(dtype=np.float64) * sign
            mask = ~np.isnan(s)
            if mask.sum() < 2:
                continue
            mean = s[mask].mean()
            std = s[mask].std()
            if std == 0 or np.isnan(std):
                continue
            z = np.where(mask, (s - mean) / std, 0.0)
            z = np.clip(z, -3.0, 3.0)
            scores += z * weight

        return pd.Series(scores, index=eligible["stock_code"].to_numpy())

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        if self._eligible_by_date is None:
            self._precompute_features(daily_df)

        eligible = self._eligible_by_date.get(current_date)
        if eligible is None or len(eligible) == 0:
            return []

        universe = self._get_universe(daily_df, current_date)
        if not universe:
            return []
        universe_set = set(universe)

        eligible = eligible[eligible["stock_code"].isin(universe_set)]
        if len(eligible) < 5:
            return []

        scores = self._score_from_eligible(eligible)
        if len(scores) == 0:
            return []

        scores = scores.sort_values(ascending=False)

        signals: list[Signal] = []
        for code, score in scores.head(top_k * 2).items():
            if code in portfolio.positions:
                continue
            window = "morning"
            signals.append(Signal(code=code, score=float(score),
                                  direction="buy", window=window,
                                  reason=f"vpt={score:.2f}"))
            if len(signals) >= top_k:
                break
        return signals

    def should_exit(self, position, today_row, today):
        """量价趋势确认出场:
        1. close < MA20（中期趋势破位）
        2. 量价背离: price up but volume declining
        3. 量能枯竭: volume_ratio_5 持续低于阈值
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
        vol_ratio_5 = feats.get("volume_ratio_5", 1.0)
        vol_ratio_5d_avg = feats.get("vol_ratio_5d_avg", 1.0)

        # 1. 中期趋势破位: close < MA20
        if pd.notna(close) and pd.notna(ma20) and close < ma20:
            return True

        # 2. 量价背离: 价格还在 MA20 上方但量能持续萎缩
        if vol_ratio_5d_avg < self.exit_vol_ratio_min:
            return True

        # 3. 量能枯竭: volume_ratio_5 < 0.5
        if vol_ratio_5 < 0.5:
            return True

        return False