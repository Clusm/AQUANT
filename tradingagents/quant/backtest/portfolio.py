"""持仓与现金管理(简化版):仅保留 Portfolio 容器和 Position/Trade dataclass。

T+1 结算/手续费/估值等方法不迁移(量化层只生成信号,不模拟交易)。
策略通过 `code in portfolio.positions` 检查是否已持仓。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingagents.quant.config import INITIAL_CAPITAL, MAX_POSITIONS


@dataclass
class Position:
    code: str
    shares: int
    entry_price: float
    entry_date: pd.Timestamp
    entry_window: str
    cost: float


@dataclass
class Trade:
    code: str
    direction: str
    shares: int
    price: float
    date: pd.Timestamp
    cost: float = 0.0


class Portfolio:
    def __init__(self, capital: float = INITIAL_CAPITAL,
                 max_positions: int = MAX_POSITIONS,
                 calendar: pd.DatetimeIndex | None = None):
        self.cash = capital
        self.initial_capital = capital
        self.max_positions = max_positions
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.calendar = calendar if calendar is not None else pd.DatetimeIndex([])
        self.equity_curve: list[dict] = []
        self.last_known_close: dict[str, float] = {}

    @property
    def n_positions(self) -> int:
        return len(self.positions)

    @property
    def free_slots(self) -> int:
        return self.max_positions - self.n_positions
