"""web/components/quant_pick.py 纯函数单元测试:候选表展示 DataFrame + 全选联动。

render_quant_picker 本身绑定 Streamlit,不直接测;抽出的
_prepare_display_df / _select_all_updates 为纯函数,离线可测。
"""

import pandas as pd

from web.components.quant_pick import (
    _prepare_display_df,
    _select_all_updates,
    _tier_chips_html,
)


def _picks_df(rows=None):
    rows = rows or [
        {"stock_code": "600000", "n_strategies": 3, "weighted_score": 15.0,
         "avg_win_rate": 0.6976, "avg_holding_days": 8.33,
         "n_S": 1, "n_A": 2, "n_B": 0, "n_C": 0},
        {"stock_code": "000001", "n_strategies": 1, "weighted_score": 5.0,
         "avg_win_rate": 0.5, "avg_holding_days": 15.0,
         "n_S": 0, "n_A": 0, "n_B": 1, "n_C": 0},
    ]
    return pd.DataFrame(rows)


class TestPrepareDisplayDf:
    def test_rank_inserted_first(self):
        df = _prepare_display_df(_picks_df())
        assert list(df.columns)[0] == "rank"
        assert df["rank"].tolist() == [1, 2]

    def test_name_from_map_with_fallback(self):
        df = _prepare_display_df(_picks_df(), name_map={"600000": "浦发银行"})
        assert df["name"].tolist() == ["浦发银行", "--"]

    def test_no_name_map_uses_dash(self):
        df = _prepare_display_df(_picks_df())
        assert df["name"].tolist() == ["--", "--"]

    def test_win_rate_and_holding_formatted(self):
        df = _prepare_display_df(_picks_df())
        assert df["win_rate_pct"].tolist() == ["69.8%", "50.0%"]
        assert df["holding_d"].tolist() == ["8.3d", "15.0d"]

    def test_empty_df(self):
        df = _prepare_display_df(pd.DataFrame(columns=["stock_code"]))
        assert len(df) == 0
        assert {"rank", "name", "win_rate_pct", "holding_d"} <= set(df.columns)

    def test_missing_metric_columns_default_zero(self):
        df = _prepare_display_df(pd.DataFrame({"stock_code": ["600000"]}))
        assert df["win_rate_pct"].tolist() == ["0.0%"]
        assert df["holding_d"].tolist() == ["0.0d"]

    def test_nan_metric_display_zero_not_nan_percent(self):
        # 老缓存无 win_rate 时 avg_win_rate 可能是 NaN,应显示 0.0% 而非 "nan%"
        df = _prepare_display_df(pd.DataFrame({
            "stock_code": ["600000"],
            "avg_win_rate": [float("nan")],
            "avg_holding_days": [float("nan")],
        }))
        assert df["win_rate_pct"].tolist() == ["0.0%"]
        assert df["holding_d"].tolist() == ["0.0d"]


class TestSelectAllUpdates:
    def test_checked_propagates_to_all_codes(self):
        updates = _select_all_updates(["600000", "000001"], True, "qp")
        assert updates == {"qp_sel_600000": True, "qp_sel_000001": True}

    def test_unchecked_propagates_to_all_codes(self):
        updates = _select_all_updates(["600000", "000001"], False, "qp")
        assert updates == {"qp_sel_600000": False, "qp_sel_000001": False}

    def test_custom_key_prefix(self):
        updates = _select_all_updates(["600000"], True, "history_qp")
        assert updates == {"history_qp_sel_600000": True}

    def test_empty_codes(self):
        assert _select_all_updates([], True, "qp") == {}


def test_tier_chips_merge_mid_and_short():
    row = pd.Series({"n_S": 1, "n_M_S": 2, "n_A": 0, "n_M_A": 1,
                     "n_B": 0, "n_M_B": 0, "n_C": 0, "n_M_C": 0})
    html = _tier_chips_html(row)
    assert "S3" in html
    assert "A1" in html
    assert "短线 1 / 中线 2" in html


def test_tier_chips_placeholder_when_empty():
    row = pd.Series({"n_S": 0, "n_A": 0, "n_B": 0, "n_C": 0})
    assert "·" in _tier_chips_html(row)
