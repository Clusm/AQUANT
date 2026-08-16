"""Web UI 用户配置持久化(仅限本机,保存在 ~/.tradingagents/web_config.json)。

当前持久化:模型数据源、API Key、OpenCode 网关地址。
不写入 .env,避免污染项目级密钥文件。
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

_FILE = Path.home() / ".tradingagents" / "web_config.json"
_LOCK = threading.Lock()

_DEFAULTS: dict[str, Any] = {
    "llm_gateway": "deepseek_official",
    "llm_api_key": "",
    "llm_base_url": "",
}


def _load() -> dict[str, Any]:
    if not _FILE.exists():
        return {}
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_user_config() -> dict[str, Any]:
    """Return persisted config merged over defaults."""
    with _LOCK:
        merged = dict(_DEFAULTS)
        merged.update(_load())
        return merged


def save_user_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Atomically merge and persist a small patch."""
    with _LOCK:
        data = _load()
        data.update({k: v for k, v in patch.items() if k in _DEFAULTS and v is not None})
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_name(f".web_config.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(_FILE)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        try:
            _FILE.chmod(0o600)
        except OSError:
            pass
        return dict(_DEFAULTS, **data)
