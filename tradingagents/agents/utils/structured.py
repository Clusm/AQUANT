"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.

Provider-level downgrade
------------------------
Some third-party gateways (e.g. opencode.ai Console Go) accept the
``with_structured_output`` bind call but return HTTP 400 on the actual
invoke because they don't translate OpenAI ``tool_choice`` /
``function_calling`` to the upstream provider. Without protection, every
agent pays the failed-call cost (1-2s + double tokens) on every run.

``_UNSUPPORTED_KEYS`` is a process-level cache: once an LLM identity has
failed structured invoke with an "unsupported" error signature, all
subsequent ``bind_structured`` calls for that identity skip the bind
entirely so the agent goes straight to free text. The cache is keyed by
``(model_name, base_url)`` so different providers/models are tracked
independently. The flag never persists across process restarts so a
transient outage doesn't permanently disable structured output.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Process-level cache of (model, base_url) identities that have failed
# structured invoke with an "unsupported" signature. Subsequent bind calls
# for the same identity skip with_structured_output entirely.
_UNSUPPORTED_KEYS: set[tuple[str, str]] = set()
_UNSUPPORTED_LOCK = threading.Lock()

# Error signatures that indicate the provider/gateway doesn't support
# function-calling / tool_choice - retrying structured output is pointless.
# A 400 "Upstream request failed" from opencode.ai Console Go is the
# canonical case; we also match the more explicit variants.
_UNSUPPORTED_ERROR_MARKERS = (
    "upstream request failed",
    "tool_choice",
    "function_calling",
    "function calling",
    "does not support tool",
    "tools are not supported",
    "function is not supported",
    "does not support functions",
    "not support function calling",
)


def _provider_key(llm: Any) -> tuple[str, str]:
    """Identity key for an LLM: (model_name, base_url).

    Different providers expose these under different attribute names; we
    try the common ones and fall back to safe defaults so a weird LLM
    wrapper still gets *some* key (worst case: all instances collapse to
    one bucket, which is fine - they'd fail the same way).
    """
    model = (
        getattr(llm, "model_name", None)
        or getattr(llm, "model", None)
        or "unknown"
    )
    base = (
        getattr(llm, "openai_api_base", None)
        or getattr(llm, "base_url", None)
        or getattr(llm, "azure_endpoint", None)
        or "default"
    )
    return (str(model), str(base))


def _force_free_text_from_config() -> bool:
    """Read the ``force_free_text_llm`` flag from the global config.

    Returns False if the config hasn't been initialised yet (e.g. during
    unit tests that bypass TradingAgentsGraph). The flag lets users
    explicitly disable structured output for providers/gateways that are
    known-broken, avoiding the wasted failed call on the first run.
    """
    try:
        from tradingagents.dataflows.config import get_config
        return bool(get_config().get("force_free_text_llm", False))
    except Exception:
        return False


def _is_unsupported_error(exc: Exception) -> bool:
    """Heuristic: does this exception indicate the provider can't do structured output?

    Matches 400 + any of the unsupported markers. We check the exception
    type name and the string form so it works across langchain versions
    (BadRequestError, APIStatusError, ValueError all surface the message).
    """
    text = f"{type(exc).__name__} {exc}".lower()
    if "400" not in text and "badrequest" not in text and "invalid_request_error" not in text:
        return False
    return any(marker in text for marker in _UNSUPPORTED_ERROR_MARKERS)


def _mark_unsupported(llm: Any, agent_name: str) -> None:
    """Record that this LLM identity can't do structured output."""
    key = _provider_key(llm)
    with _UNSUPPORTED_LOCK:
        if key not in _UNSUPPORTED_KEYS:
            _UNSUPPORTED_KEYS.add(key)
            logger.warning(
                "%s: provider %s does not support structured output via this "
                "gateway; subsequent agents will skip with_structured_output "
                "and use free-text generation directly (set force_free_text_llm "
                "=true in config to silence this warning and skip the probe call)",
                agent_name, key,
            )


def _is_unsupported(llm: Any) -> bool:
    """Has this LLM identity been marked as not supporting structured output?"""
    key = _provider_key(llm)
    with _UNSUPPORTED_LOCK:
        return key in _UNSUPPORTED_KEYS


def reset_structured_output_cache() -> None:
    """Clear the unsupported-provider cache.

    Mainly useful for tests where the same process intentionally exercises
    both the structured and free-text paths on the same LLM identity.
    """
    with _UNSUPPORTED_LOCK:
        _UNSUPPORTED_KEYS.clear()


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Optional[Any]:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    # User-configured hard disable (e.g. known-broken gateway).
    if _force_free_text_from_config():
        logger.info(
            "%s: force_free_text_llm=true, skipping with_structured_output",
            agent_name,
        )
        return None

    # Previously failed probe on this provider - skip the wasted bind.
    if _is_unsupported(llm):
        logger.info(
            "%s: provider previously failed structured output, skipping bind",
            agent_name,
        )
        return None

    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.
    """
    # Even if structured_llm was bound at agent-creation time, a sibling
    # agent (e.g. Research Manager) may have since marked this LLM identity
    # as not supporting structured output. Skip the wasted probe call and
    # go straight to free text.
    if structured_llm is not None and not _is_unsupported(plain_llm):
        try:
            result = structured_llm.invoke(prompt)
            return render(result)
        except Exception as exc:
            # Detect "provider doesn't support structured output" signatures
            # and downgrade this LLM identity for the rest of the process so
            # sibling agents (Trader / Portfolio Manager) don't pay the same
            # failed-probe cost.
            if _is_unsupported_error(exc):
                _mark_unsupported(plain_llm, agent_name)
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return response.content
