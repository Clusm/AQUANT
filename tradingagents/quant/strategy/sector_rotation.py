"""中线策略 V2-3:板块轮动中线版。

核心思路(行业多头 + 龙头股):
- 用 sw_industry_map 给每只股票打申万一级行业标签
- 计算每个行业指数(行业内 universe 个股等权日涨幅累计)
- 行业多头排列:行业指数 MA5 > MA10 > MA20
- 行业近 5d/10d 涨幅排名 top N(默认 top 5)
- 在 top 行业内选个股:多头排列 + 放量 + 接近 20d 新高
- top_k 内同行业最多 max_per_industry 只(避免集中)

与 sector_main_line.py 区别:
- sector_main_line 用市场广度近似"主线存在性"(强势股>=30 + 涨停>=5)
- 本策略用真实行业数据计算行业指数,选 top 行业内龙头股

中线逻辑:中线趋势的本质是板块轮动。选对行业 + 龙头股,持仓 10-15d 让板块行情展开。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.backtest.engine import Signal
from tradingagents.quant.data import cache as cache_mod
from tradingagents.quant.data.universe import filter_universe_topk
from tradingagents.quant.features.pipeline import build_features_vectorized, cross_sectional_zscore
from tradingagents.quant.strategy.base import BaseStrategy


class SectorRotationStrategy(BaseStrategy):
    """板块轮动中线策略。"""
    name = "sector_rotation"

    def __init__(self, lookback: int = 120, universe_topk: int = 500,
                 industry_ma_short: int = 5,
                 industry_ma_mid: int = 10,
                 industry_ma_long: int = 20,
                 industry_top_n: int = 5,
                 industry_ret_window: int = 5,
                 max_per_industry: int = 2,
                 today_ret_min: float = 0.0,
                 today_ret_max: float = 0.06,
                 today_vol_min: float = 1.5,
                 body_ratio_min: float = 0.5,
                 require_above_ma: str = "ma20",
                 require_full_align: bool = False,
                 near_high_ratio: float = 0.95):
        self.lookback = lookback
        self.universe_topk = universe_topk
        self.industry_ma_short = industry_ma_short
        self.industry_ma_mid = industry_ma_mid
        self.industry_ma_long = industry_ma_long
        self.industry_top_n = industry_top_n
        self.industry_ret_window = industry_ret_window
        self.max_per_industry = max_per_industry
        self.today_ret_min = today_ret_min
        self.today_ret_max = today_ret_max
        self.today_vol_min = today_vol_min
        self.body_ratio_min = body_ratio_min
        self.require_above_ma = require_above_ma
        self.require_full_align = require_full_align
        self.near_high_ratio = near_high_ratio
        self._universe_cache: dict[str, list[str]] = {}
        self._feature_cache: pd.DataFrame | None = None
        self._industry_map: pd.DataFrame | None = None
        self._industry_index_cache: pd.DataFrame | None = None

    def _get_universe(self, daily_df: pd.DataFrame, current_date: pd.Timestamp) -> list[str]:
        date_key = pd.Timestamp(current_date).strftime("%Y-%m-%d")
        if date_key in self._universe_cache:
            return self._universe_cache[date_key]
        codes = filter_universe_topk(daily_df, on_date=current_date, topk=None)
        self._universe_cache[date_key] = codes
        return codes

    def _load_industry_map(self) -> pd.DataFrame:
        if self._industry_map is not None:
            return self._industry_map
        df = cache_mod.load("sw_industry_map")
        df = df.rename(columns={"sw_l1_name": "industry"})
        df["stock_code"] = df["stock_code"].astype(str)
        # 提取 6 位代码(去除可能的 .SZ/.SH 后缀)
        df["stock_code"] = df["stock_code"].str.slice(0, 6)
        df = df[["stock_code", "industry"]].dropna()
        self._industry_map = df
        return df

    def _precompute_features(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        if self._feature_cache is not None:
            return self._feature_cache
        print("  [板块轮动] 预计算特征矩阵...", flush=True)
        sorted_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        big = build_features_vectorized(sorted_df, min_rows=30)
        if len(big) == 0:
            self._feature_cache = pd.DataFrame()
            return self._feature_cache
        big["prev_close_raw"] = big.groupby("stock_code")["close"].shift(1)
        big["today_ret"] = (big["close"] - big["prev_close_raw"]) / big["prev_close_raw"].replace(0, np.nan)

        body = big["close"] - big["open"]
        hl = (big["high"] - big["low"]).replace(0, np.nan)
        big["body_ratio"] = (body / hl).fillna(0)
        big["is_bullish"] = (big["close"] > big["open"]).astype(float)

        # 多头排列
        big["full_align"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"]) &
            (big["ma20"] > big["ma60"])
        ).astype(float)
        big["align_3ma"] = (
            (big["ma5"] > big["ma10"]) &
            (big["ma10"] > big["ma20"])
        ).astype(float)

        # 接近 20d 新高
        big["high_20"] = big.groupby("stock_code")["high"].transform(
            lambda s: s.rolling(20, min_periods=10).max().shift(1)
        )
        big["near_high_20"] = (big["close"] >= big["high_20"] * self.near_high_ratio).astype(float)

        # 合并行业标签
        ind_map = self._load_industry_map()
        big = big.merge(ind_map, on="stock_code", how="left")
        big["industry"] = big["industry"].fillna("未知")

        self._feature_cache = big
        print(f"  [板块轮动] 特征矩阵: {big.shape}", flush=True)

        # 预计算行业指数
        self._precompute_industry_index(big)
        return big

    def _precompute_industry_index(self, big: pd.DataFrame) -> None:
        """计算每个行业的行业指数(等权日涨幅累计 + MA)。"""
        if self._industry_index_cache is not None:
            return
        print("  [板块轮动] 预计算行业指数...", flush=True)
        # 行业内个股等权平均日涨幅作为行业日收益
        ind_daily = big.groupby(["industry", "trade_date"])["today_ret"].mean().reset_index()
        ind_daily = ind_daily.sort_values(["industry", "trade_date"]).reset_index(drop=True)
        # 行业累计净值(从 1 开始)
        ind_daily["ind_ret"] = ind_daily.groupby("industry")["today_ret"].transform(
            lambda s: s.fillna(0)
        )
        ind_daily["ind_nav"] = ind_daily.groupby("industry")["ind_ret"].transform(
            lambda s: (1 + s).cumprod()
        )
        # 行业 MA
        for window, name in [(self.industry_ma_short, "ind_ma_s"),
                             (self.industry_ma_mid, "ind_ma_m"),
                             (self.industry_ma_long, "ind_ma_l")]:
            ind_daily[name] = ind_daily.groupby("industry")["ind_nav"].transform(
                lambda s: s.rolling(window, min_periods=window // 2).mean()
            )
        # 行业近 N 日涨幅
        ind_daily["ind_ret_recent"] = ind_daily.groupby("industry")["ind_nav"].pct_change(
            self.industry_ret_window
        )
        # 行业多头排列
        ind_daily["ind_bullish"] = (
            (ind_daily["ind_ma_s"] > ind_daily["ind_ma_m"]) &
            (ind_daily["ind_ma_m"] > ind_daily["ind_ma_l"])
        ).astype(int)
        self._industry_index_cache = ind_daily
        print(f"  [板块轮动] 行业数: {ind_daily['industry'].nunique()}, 行业-日行数: {len(ind_daily)}")

    def generate_signals(self, daily_df: pd.DataFrame, current_date: pd.Timestamp,
                         portfolio, top_k: int = 2) -> list[Signal]:
        universe = self._get_universe(daily_df, current_date)
        if not universe:
            return []

        feats_all = self._precompute_features(daily_df)
        if len(feats_all) == 0 or self._industry_index_cache is None:
            return []

        # 1. 选 top 行业(当日行业多头排列 + 近 N 日涨幅 top N)
        ind_today = self._industry_index_cache[
            self._industry_index_cache["trade_date"] == current_date
        ].copy()
        if len(ind_today) == 0:
            return []
        ind_today = ind_today[ind_today["ind_bullish"] == 1]
        if len(ind_today) == 0:
            return []
        ind_today = ind_today.sort_values("ind_ret_recent", ascending=False)
        top_industries = set(ind_today.head(self.industry_top_n * 2)["industry"].tolist())
        if not top_industries:
            return []

        # 2. 取 T 日 universe 内的个股
        feats = feats_all[(feats_all["trade_date"] == current_date) &
                          (feats_all["stock_code"].isin(universe))].copy()
        if len(feats) < 5:
            return []

        # 3. 限定 top 行业内
        feats = feats[feats["industry"].isin(top_industries)]
        if len(feats) == 0:
            return []

        # 4. 个股过滤
        feats = self._filter_eligible(feats)
        if len(feats) == 0:
            return []

        # 5. 打分
        scores = self._score(feats)
        if len(scores) == 0:
            return []

        # 6. 选 top_k,控制同行业数量
        eligible = scores.sort_values(ascending=False)
        signals: list[Signal] = []
        industry_count: dict[str, int] = {}
        for code, score in eligible.items():
            if code in portfolio.positions:
                continue
            ind = feats[feats["stock_code"] == code]["industry"].iloc[0]
            if industry_count.get(ind, 0) >= self.max_per_industry:
                continue
            industry_count[ind] = industry_count.get(ind, 0) + 1
            signals.append(Signal(code=code, score=float(score),
                                  reason=f"sec_rot={score:.2f}({ind})"))
            if len(signals) >= top_k:
                break
        return signals

    def _filter_eligible(self, feats: pd.DataFrame) -> pd.DataFrame:
        df = feats.copy()
        required = ["close", "ma5", "ma10", "ma20", "ma60",
                    "today_ret", "volume_ratio_5", "body_ratio", "is_bullish",
                    "full_align", "align_3ma", "near_high_20", "industry"]
        for c in required:
            if c not in df.columns:
                return pd.DataFrame()
        df = df.dropna(subset=[c for c in required if c not in
                               ("is_bullish", "full_align", "align_3ma", "near_high_20",
                                "industry")])
        if len(df) == 0:
            return df

        # 1. 多头排列
        if self.require_full_align:
            df = df[df["full_align"] == 1]
        else:
            df = df[df["align_3ma"] == 1]
        if len(df) == 0:
            return df

        # 2. 当日涨幅区间
        df = df[(df["today_ret"] >= self.today_ret_min) &
                (df["today_ret"] <= self.today_ret_max)]
        if len(df) == 0:
            return df

        # 3. 放量
        df = df[df["volume_ratio_5"] >= self.today_vol_min]
        if len(df) == 0:
            return df

        # 4. 实体阳线
        df = df[(df["body_ratio"] >= self.body_ratio_min) & (df["is_bullish"] == 1)]
        if len(df) == 0:
            return df

        # 5. 接近 20d 新高
        df = df[df["near_high_20"] == 1]
        if len(df) == 0:
            return df

        # 6. close > MA
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

        # 趋势强度
        if "close_to_ma20" in feats.columns:
            trend = cross_sectional_zscore(col("close_to_ma20", 0.0))
        else:
            trend = pd.Series(0.0, index=feats.index)
        # 量能
        vol = cross_sectional_zscore(col("volume_ratio_5", 1.5))
        # 实体
        body = cross_sectional_zscore(col("body_ratio", 0.5))
        # 涨幅
        chg = cross_sectional_zscore(col("today_ret", 0.0))

        scores = (
            trend * 0.35 +
            vol * 0.25 +
            body * 0.20 +
            chg * 0.20
        )
        return pd.Series(scores.values, index=feats["stock_code"].values)
