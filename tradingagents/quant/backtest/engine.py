"""回测引擎(简化版):仅保留 Signal 类。

完整 BacktestEngine 不迁移(量化层只做实时选股,不做回测)。
"""
from __future__ import annotations


class Signal:
    """策略生成的信号。"""
    def __init__(self, code: str, score: float, reason: str = ""):
        self.code = code
        self.score = score
        self.reason = reason
