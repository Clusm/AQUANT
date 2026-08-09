"""量化层全局配置。

从 stock_pick_live/config.py 裁剪:删 BACKTEST_*/ML_*/RULE_WEIGHTS(回测和 ML 不迁移),
保留 UNIVERSE_*/LIMIT_*/MAX_HOLDING_DAYS/TOP_K/CACHE_DIR(实时选股需要)。

支持环境变量 QUANT_CACHE_DIR 重定向缓存目录(便于共享 stock_pick_live 的缓存数据)。

CACHE_DIR / OUTPUT_DIR 使用 lazy init(PEP 562 模块级 __getattr__),首次访问时才
mkdir,避免 import 时副作用(无写权限时整个模块 import 失败)。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

# Lazy-init cache directory state. Modules that import CACHE_DIR still work
# transparently via module-level __getattr__ below.
_CACHE_DIR: Path | None = None
_OUTPUT_DIR: Path | None = None


def _ensure_dirs() -> None:
    global _CACHE_DIR, _OUTPUT_DIR
    if _CACHE_DIR is not None:
        return
    cache = Path(os.environ.get("QUANT_CACHE_DIR", PROJECT_ROOT / "outputs" / "cache"))
    cache.mkdir(parents=True, exist_ok=True)
    out = cache.parent
    out.mkdir(parents=True, exist_ok=True)
    _CACHE_DIR = cache
    _OUTPUT_DIR = out


def __getattr__(name: str):
    """PEP 562: lazy module attributes. CACHE_DIR / OUTPUT_DIR trigger mkdir on first access."""
    if name == "CACHE_DIR":
        _ensure_dirs()
        return _CACHE_DIR
    if name == "OUTPUT_DIR":
        _ensure_dirs()
        return _OUTPUT_DIR
    raise AttributeError(f"module 'tradingagents.quant.config' has no attribute {name!r}")


INITIAL_CAPITAL = 20000.0
MAX_POSITIONS = 2
PER_POSITION_CAPITAL = INITIAL_CAPITAL / MAX_POSITIONS

MAX_HOLDING_DAYS = 5
STOP_LOSS_PCT = -0.05
TAKE_PROFIT_PCT = 0.08

LIMIT_UP_PCT = 0.097
LIMIT_DOWN_PCT = -0.097

UNIVERSE_LIQUIDITY_TURNOVER = 50_000_000
UNIVERSE_MIN_LISTING_DAYS = 60
UNIVERSE_REBUILD_MONTHS = 1

TOP_K = 2

BENCHMARK_INDEX = "000001"
