"""top18 规则策略的按需特征列声明。

每个类只声明 build_features_vectorized 中真正被读取的列;
依赖列(如 close_to_ma20 -> ma20)由 pipeline.resolve_feature_columns 自动补全。
未声明的策略继续走 columns=None 的全量特征路径,保证自定义策略兼容。
"""

from __future__ import annotations

STRATEGY_REQUIRED_FEATURES: dict[str, list[str]] = {
    "LongConsolidationBreakoutV2Strategy": [
        "ma5", "ma10", "ma20", "ma60", "volume_ratio_5",
    ],
    "WeeklyAdxDmiBreakoutStrategy": [
        "ma5", "ma20", "volume_ratio_5",
    ],
    "BullAlignMa20BounceStrategy": [
        "ma5", "ma10", "ma20", "ma60", "close_to_ma20", "volume_ratio_5",
    ],
    "MonthlyRsiBreakoutStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "ContinuousStrongCloseStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "MonthlyBreakoutStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "MonthlyMacdGoldenCrossStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "LowVolBreakoutStrategy": [
        "ma5", "ma20", "ma60", "volume_ratio_5",
    ],
    "MonthlyWeeklyDailyResonanceStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "WeeklyMacdGoldenCrossStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "WeeklyBreakoutPullbackStrategy": [
        "ma5", "ma10", "ma20", "ma60", "ret_20d", "volume_ratio_5",
    ],
    "VolumePriceTrendStrategy": [
        "ma5", "ma10", "ma20", "ma60", "close_to_ma5", "close_to_ma20",
        "ret_5d", "turnover_zscore_20", "volume_ratio_5",
    ],
    "LeaderPullbackBounceStrategy": [
        "ma20", "ma60", "close_to_ma20", "volume_ratio_5",
    ],
}


def required_feature_columns(strategy) -> list[str] | None:
    """返回策略所需的按需特征列;未知策略返回 None(全量特征)。"""
    return STRATEGY_REQUIRED_FEATURES.get(type(strategy).__name__)
