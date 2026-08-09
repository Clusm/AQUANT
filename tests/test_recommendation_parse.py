"""parse_label bucket classification for the 综合推荐 tab."""

from web.components.recommendation import parse_label


def _decision(label_line: str) -> str:
    return (
        "# 综合推荐: 300750 (2026-08-07)\n\n"
        f"**标签**: {label_line}\n\n"
        "## 综合研判\n"
        "正文可含 '关注' / '强买' / '冲突' 等干扰词,不应影响分类。\n"
    )


def test_full_markdown_label_extraction():
    assert parse_label(_decision("🟠 冲突")) == "conflict"
    assert parse_label(_decision("🔴 弃")) == "discard"
    assert parse_label(_decision("🟢 强买")) == "strong_buy"
    assert parse_label(_decision("🟡 关注")) == "watch"


def test_markdown_not_confused_by_keywords_in_body():
    # 回归:真实标签 🟠 冲突 / 🔴 弃,但 LLM 预览含 "关注",不得被误判为 watch
    conflict_md = (
        "# 综合推荐: 300750 (2026-08-07)\n\n"
        "**标签**: 🟠 冲突\n"
        "## LLM 信号\nRating: Sell\n建议谨慎关注回调风险...\n"
        "## 综合研判\n量化与 LLM 信号冲突,需用户判断。\n"
    )
    assert parse_label(conflict_md) == "conflict"

    discard_md = (
        "# 综合推荐: 600519 (2026-08-07)\n\n"
        "**标签**: 🔴 弃\n"
        "## LLM 信号\nRating: Hold\n短期无催化,可关注后续走势...\n"
        "## 综合研判\n无买入信号,建议放弃。\n"
    )
    assert parse_label(discard_md) == "discard"


def test_clean_labels():
    assert parse_label("🟢 强买") == "strong_buy"
    assert parse_label("🟡关注") == "watch"
    assert parse_label("🟠 冲突") == "conflict"
    assert parse_label("🔴 弃") == "discard"


def test_bucket_ids_pass_through():
    assert parse_label("strong_buy") == "strong_buy"
    assert parse_label("watch") == "watch"
    assert parse_label("conflict") == "conflict"
    assert parse_label("discard") == "discard"


def test_legacy_english_ratings():
    assert parse_label("Buy") == "strong_buy"
    assert parse_label("Overweight") == "strong_buy"
    assert parse_label("Hold") == "watch"
    assert parse_label("Sell") == "discard"
    assert parse_label("Underweight") == "discard"


def test_unrecognized_falls_back_to_discard():
    assert parse_label("") == "discard"
    assert parse_label(None) == "discard"
    assert parse_label("无法识别的文本") == "discard"
