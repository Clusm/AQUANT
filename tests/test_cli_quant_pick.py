"""cli/quant_pick.py 纯函数单元测试:日期解析、输出路径、JSON 序列化。

quant_pick Typer 命令本身跑 multiprocessing 不测;三个纯 helper 离线可测。
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from cli.quant_pick import (
    _parse_today,
    _resolve_output_path,
    _serialize_result,
)


class TestParseToday:
    def test_none_returns_today_normalized(self):
        ts = _parse_today(None)
        assert isinstance(ts, pd.Timestamp)
        assert ts == pd.Timestamp.now().normalize()

    def test_string_parsed(self):
        assert _parse_today("2026-07-17") == pd.Timestamp("2026-07-17")

    def test_datetime_string_with_time(self):
        assert _parse_today("2026-07-17 15:30:00") == pd.Timestamp("2026-07-17 15:30:00")


class TestResolveOutputPath:
    def test_none_returns_none(self):
        assert _resolve_output_path(None, "json", pd.Timestamp("2026-07-17")) is None

    def test_file_path_passthrough(self):
        p = _resolve_output_path("picks.json", "json", pd.Timestamp("2026-07-17"))
        assert p == Path("picks.json")

    def test_directory_gets_auto_named_file(self, tmp_path):
        p = _resolve_output_path(str(tmp_path), "json", pd.Timestamp("2026-07-17"))
        assert p == tmp_path / "quant_pick_20260717.json"

    def test_trailing_slash_treated_as_directory(self, tmp_path):
        p = _resolve_output_path(f"{tmp_path}/", "markdown", pd.Timestamp("2026-07-17"))
        assert p == tmp_path / "quant_pick_20260717.md"

    @pytest.mark.parametrize("fmt,ext", [
        ("json", "json"), ("csv", "csv"), ("markdown", "md"), ("terminal", "txt"),
    ])
    def test_format_to_extension(self, tmp_path, fmt, ext):
        p = _resolve_output_path(str(tmp_path), fmt, pd.Timestamp("2026-07-17"))
        assert p.name == f"quant_pick_20260717.{ext}"


def _result():
    top = pd.DataFrame([
        {"stock_code": "600000", "n_strategies": 2, "weighted_score": 15.0,
         "avg_win_rate": 0.7, "avg_holding_days": 8.3, "n_S": 1, "n_A": 1,
         "n_B": 0, "n_C": 0},
    ])
    return {
        "today": pd.Timestamp("2026-07-17"),
        "elapsed": 733.647,
        "n_strategies_run": 10,
        "n_strategies_error": 0,
        "top_picks": top,
        "all_records": [
            {"date": pd.Timestamp("2026-07-17"), "strategy": "S_a", "tier": "S",
             "strategy_comp": 10.0, "stock_code": "600000", "score": 1.0,
             "reason": "r", "holding_days": 5, "entry_advice": "a", "win_rate": 0.8},
        ],
        "per_strategy_stats": {"S_a": {"tier": "S", "n_hits": 1, "error": None}},
    }


class TestSerializeResult:
    def test_json_compatible(self):
        payload = _serialize_result(_result())
        # 必须能被 json.dumps 序列化(无 Timestamp/DataFrame)
        roundtrip = json.loads(json.dumps(payload, ensure_ascii=False))
        assert roundtrip["today"] == "2026-07-17"
        assert roundtrip["elapsed"] == 733.65
        assert roundtrip["n_strategies_run"] == 10
        assert roundtrip["top_picks"][0]["stock_code"] == "600000"
        assert roundtrip["all_records"][0]["date"] == "2026-07-17"
        assert roundtrip["per_strategy_stats"]["S_a"]["n_hits"] == 1

    def test_empty_top_picks(self):
        result = {**_result(), "top_picks": pd.DataFrame()}
        payload = _serialize_result(result)
        assert payload["top_picks"] == []

    def test_elapsed_rounded_two_decimals(self):
        payload = _serialize_result(_result())
        assert payload["elapsed"] == 733.65
