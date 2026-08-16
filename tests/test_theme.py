"""web/theme.py 设计令牌 + HTML 小件测试;signal_style 色值回归。

令牌重构的目标是"色值零变化",这里锁住关键色值,防止未来改令牌时信号色漂移。
"""

from web.stock_display import signal_style
from web.theme import (
    BG,
    BRAND_GRADIENT,
    MUTED,
    PRIMARY,
    SIGNAL,
    SURFACE_2,
    TIER,
    build_global_css,
    chip,
    empty_state_html,
    esc,
    hero_html,
    icon_heading,
    inline_icon,
    mono,
    pill,
)


class TestTokens:
    def test_signal_palette_distinct_and_expected(self):
        # 与推荐面板/report 卡渲染一致的信号色(🟢强买/🟡关注/🟠冲突/🔴弃)
        assert SIGNAL["strong_buy"] == "#22c55e"
        assert SIGNAL["watch"] == "#eab308"
        assert SIGNAL["conflict"] == "#f97316"
        assert SIGNAL["discard"] == "#ef4444"

    def test_tier_palette_covers_sabc(self):
        assert set(TIER) == {"S", "A", "B", "C"}
        for color in TIER.values():
            assert color.startswith("#")

    def test_core_tokens_are_hex(self):
        for token in (MUTED, PRIMARY, *SIGNAL.values(), *TIER.values()):
            assert token.startswith("#")


class TestEsc:
    def test_escapes_html(self):
        assert esc('<img src=x onerror=alert(1)>') == "&lt;img src=x onerror=alert(1)&gt;"

    def test_plain_text_passthrough(self):
        assert esc("浦发银行") == "浦发银行"


class TestMono:
    def test_wraps_with_mono_font(self):
        out = mono("600000")
        assert "ui-monospace" in out
        assert "600000" in out

    def test_color_and_bold(self):
        out = mono("69.8%", color=SIGNAL["strong_buy"], bold=True)
        assert SIGNAL["strong_buy"] in out
        assert "font-weight:700" in out

    def test_escapes_content(self):
        out = mono("<b>")
        assert "&lt;b&gt;" in out


class TestChip:
    def test_renders_colored_badge(self):
        out = chip("S2", TIER["S"])
        assert "S2" in out
        assert TIER["S"] in out
        assert "border" in out

    def test_escapes_content(self):
        out = chip("<x>", "#fff")
        assert "&lt;x&gt;" in out


class TestSignalStyleRegression:
    """令牌重构后 signal_style 色值必须与之前完全一致。"""

    def test_4tier_labels(self):
        assert signal_style("🟢 强买") == (SIGNAL["strong_buy"], "强买")
        assert signal_style("🟡 关注") == (SIGNAL["watch"], "关注")
        assert signal_style("🟠 冲突") == (SIGNAL["conflict"], "冲突")
        assert signal_style("🔴 弃") == (SIGNAL["discard"], "弃")

    def test_legacy_english_ratings(self):
        assert signal_style("Buy") == (SIGNAL["strong_buy"], "买入")
        assert signal_style("Sell") == (SIGNAL["discard"], "卖出")
        assert signal_style("Hold") == ("#fbbf24", "持有")


class TestGlobalCss:
    def test_css_contains_tokens(self):
        css = build_global_css()
        assert BG in css
        assert SURFACE_2 in css
        assert PRIMARY in css
        assert BRAND_GRADIENT in css
        # 不应残留未替换占位符
        assert "@" + "BG@" not in css

    def test_css_has_no_external_font_import(self):
        # 国内访问 Google Fonts 不稳定,发布版移除外部字体依赖
        assert "@import" not in build_global_css()


class TestTabIcons:
    def test_six_svg_tab_icons_generated(self):
        css = build_global_css()
        assert css.count(":nth-of-type(") == 7
        for idx in range(1, 8):
            assert f":nth-of-type({idx})" in css
        assert "--aq-tab-icon" in css
        assert "data:image/svg+xml" in css
        assert "mask-image: var(--aq-tab-icon, none)" in css

    def test_tab_icon_data_uris_are_url_encoded(self):
        css = build_global_css()
        assert "%3Csvg" in css
        assert "<svg xmlns" not in css.split("data:image/svg+xml,")[1][:80]


class TestInlineIcons:
    def test_inline_icon_renders_svg(self):
        out = inline_icon("chart", color=PRIMARY, size=20, margin_right="0")
        assert out.startswith("<span")
        assert "<svg" in out
        assert "currentColor" in out

    def test_icon_heading_escapes_title(self):
        out = icon_heading("briefcase", "<b>最终建议</b>")
        assert "&lt;b&gt;" in out
        assert "<svg" in out

    def test_empty_state_accepts_svg_icon_html(self):
        out = empty_state_html(inline_icon("target", size=48, margin_right="0"), "标题")
        assert "<svg" in out
        assert "标题" in out


class TestHtmlHelpers:
    def test_hero_escapes_markup(self):
        out = hero_html("<b>A</b>", "<script>x</script>")
        assert "&lt;b&gt;A&lt;/b&gt;" in out
        assert "&lt;script&gt;" in out

    def test_empty_state_escapes_body(self):
        out = empty_state_html("📊", "<x>", "a<br>b")
        assert "&lt;x&gt;" in out
        assert "&lt;br&gt;" in out

    def test_pill_escapes_value(self):
        assert "&lt;b&gt;" in pill("<b>")
