import logging
import re

logger = logging.getLogger(__name__)

# Tickers can contain letters, digits, dot, dash, underscore, and caret
# (for index symbols like ^GSPC). Anything else is rejected so the value
# never escapes a containing directory when interpolated into a path.
_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9._\-\^]+$")
_HAS_CHINESE_RE = re.compile(r"[一-鿿]")


def safe_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    If the value contains Chinese characters (common when LLMs return stock
    names instead of codes), automatically resolve it to a 6-digit A-stock
    code via ``resolve_ticker`` before validation.

    Returns ``value`` unchanged when it matches the allowed pattern; raises
    ``ValueError`` otherwise.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")

    if _HAS_CHINESE_RE.search(value):
        from tradingagents.dataflows.a_stock import resolve_ticker
        resolved = resolve_ticker(value)
        logger.info("Auto-resolved Chinese ticker %r -> %s", value, resolved)
        value = resolved

    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    if not _TICKER_PATH_RE.fullmatch(value):
        raise ValueError(
            f"ticker contains characters not allowed in a filesystem path: {value!r}"
        )
    if set(value) == {"."}:
        raise ValueError(f"ticker cannot consist solely of dots: {value!r}")
    return value
