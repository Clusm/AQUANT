"""web/user_config.py 持久化测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from web import user_config


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(user_config, "_FILE", tmp_path / "web_config.json")
    yield tmp_path


def test_defaults_when_no_file(isolated):
    cfg = user_config.load_user_config()
    assert cfg["llm_gateway"] == "deepseek_official"
    assert cfg["llm_api_key"] == ""


def test_roundtrip(isolated):
    user_config.save_user_config({"llm_api_key": "sk-test", "llm_gateway": "opencode"})
    cfg = user_config.load_user_config()
    assert cfg["llm_api_key"] == "sk-test"
    assert cfg["llm_gateway"] == "opencode"


def test_unknown_keys_are_ignored(isolated):
    user_config.save_user_config({"llm_api_key": "sk-test", "evil": "x"})
    assert "evil" not in user_config.load_user_config()
