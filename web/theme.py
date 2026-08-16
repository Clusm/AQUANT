"""中央设计令牌:A股"行情牌"风格的统一配色、全局 CSS 与 HTML 小件。

色值收敛为单一事实来源(同时与 .streamlit/config.toml 保持一致),
避免散落硬编码导致漂移。常量与 HTML helper 不依赖 streamlit,可离线单测;
``inject_global_css()`` 是唯一需要 Streamlit runtime 的入口。

信号色与 web/stock_display.signal_style 的输出保持一致(🟢强买/🟡关注/🟠冲突/🔴弃);
分级色(S/A/B/C)是量化选股表的命中徽章用色,与推荐信号语义分开。
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

# ── 基础表面 ──
BG = "#0a0a0a"
SURFACE = "#0f0f0f"
SURFACE_2 = "#161616"
SURFACE_HOVER = "#1e1e1e"
BORDER = "#222"
BORDER_LIGHT = "#2a2a2a"

# ── 文字 ──
TEXT = "#f5f1eb"
MUTED = "#8b8b93"

# ── 品牌 ──
PRIMARY = "#ff5a1f"
PRIMARY_HOVER = "#e04d15"
PRIMARY_SOFT = "#ff8c42"
BRAND_GRADIENT = "linear-gradient(135deg, #ff5a1f 0%, #ff8c42 55%, #ffb347 100%)"

# ── 推荐信号(🟢强买 / 🟡关注 / 🟠冲突 / 🔴弃)──
SIGNAL: dict[str, str] = {
    "strong_buy": "#22c55e",
    "watch": "#eab308",
    "conflict": "#f97316",
    "discard": "#ef4444",
}

# ── 量化策略分级命中徽章(S/A/B/C)──
TIER: dict[str, str] = {
    "S": "#ff5a1f",
    "A": "#f59e0b",
    "B": "#38bdf8",
    "C": "#94a3b8",
}

# ── Tab 图标:统一 24x24 细线矢量图标(与 emoji 解耦,支持 currentColor)──
# 顺序必须与 web/app_main.py 的 st.tabs 顺序一致。
_TAB_ICONS: tuple[str, ...] = (
    # 1 量化选股:柱状图 + 坐标轴
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M4 20V4'/><path d='M4 20h16'/>"
    "<rect x='7.2' y='11.5' width='3' height='7' rx='0.8' fill='#000' stroke='none'/>"
    "<rect x='12.6' y='8' width='3' height='10.5' rx='0.8' fill='#000' stroke='none'/>"
    "<rect x='18' y='13.5' width='3' height='5' rx='0.8' fill='#000' stroke='none'/></svg>",
    # 2 AI 深度分析:芯片/算力节点
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<rect x='6.5' y='6.5' width='11' height='11' rx='2.2'/>"
    "<rect x='10.5' y='10.5' width='3' height='3' rx='0.8' fill='#000' stroke='none'/>"
    "<path d='M12 2.8v3.7M12 17.5v3.7M2.8 12h3.7M17.5 12h3.7'/>"
    "<path d='m5.5 5.5 2.6 2.6M15.9 15.9l2.6 2.6M18.5 5.5l-2.6 2.6M8.1 15.9l-2.6 2.6'/></svg>",
    # 3 买入计划:清单勾选
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<rect x='5' y='4.5' width='14' height='16.5' rx='2.5'/>"
    "<path d='M9 4.5a2.5 2.5 0 0 1 2.5-2.5h1A2.5 2.5 0 0 1 15 4.5V6H9z'/>"
    "<path d='m9.2 13.5 2 2 3.8-4.2'/></svg>",
    # 4 持仓跟踪:趋势折线
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M3.5 17.5 9 12l3.8 3.8 7.7-7.8'/><path d='M15.5 8h5v5'/></svg>",
    # 5 交易记录:账本/收益曲线
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M5 3.5h11.5L20 7v13.5H5z'/>"
    "<path d='M15 3.5V7h5'/>"
    "<path d='m8.5 14 2.5-2.5 2 2 3-3'/>"
    "<path d='M8.5 10.5v3h3'/></svg>",
    # 6 综合推荐:奖章/优胜
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<circle cx='12' cy='9' r='5.2'/>"
    "<path d='m8.8 13.2-1.7 7.3 4.9-2.3 4.9 2.3-1.7-7.3'/></svg>",
    # 7 历史:时钟回拨
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none'"
    " stroke='#000' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M4 12a8 8 0 1 0 2.343-5.657'/><path d='M4 4v4h4'/></svg>",
)


def _tab_icon_css() -> str:
    """Generate nth-of-type rules mapping each tab to its SVG mask icon."""
    rules = [
        ".stTabs:not(.stTabs .stTabs) [data-baseweb='tab-list'] > [data-baseweb='tab']:nth-of-type("
        f"{idx}) {{ --aq-tab-icon: url('data:image/svg+xml,{quote(svg, safe='')}'); }}"
        for idx, svg in enumerate(_TAB_ICONS, 1)
    ]
    return chr(10).join(rules)

_MONO = "font-family:ui-monospace,Consolas,'Courier New',monospace;"


def esc(value: Any) -> str:
    """HTML 转义外部数据(股票名/代码等),防止注入与破坏布局。"""
    return html.escape(str(value))


def mono(value: Any, color: str | None = None, bold: bool = False) -> str:
    """行情牌式等宽数字。"""
    style = _MONO
    if color:
        style += f"color:{color};"
    if bold:
        style += "font-weight:700;"
    return f'<span style="{style}">{esc(value)}</span>'


def chip(value: Any, color: str, title: str = "") -> str:
    """小徽章(分级命中数等):描边 + 同色文字,行情牌计数感。"""
    tip = f' title="{esc(title)}"' if title else ""
    return (
        f'<span{tip} style="display:inline-block;padding:0 0.4rem;margin-right:0.2rem;'
        f"border-radius:0.35rem;font-size:0.75rem;line-height:1.5;font-weight:700;"
        f"{_MONO}color:{color};border:1px solid {color};\">{esc(value)}</span>"
    )


def card_html(
    title: str,
    body: str = "",
    *,
    accent: str | None = None,
    subtitle: str = "",
) -> str:
    """通用内容卡片(标题 + 副标题 + 正文)。正文按纯文本转义。"""
    border = f"border-top:3px solid {accent};" if accent else ""
    sub = (
        f'<div style="font-size:0.82rem;color:{MUTED};margin-top:0.25rem;">'
        f"{esc(subtitle)}</div>"
        if subtitle
        else ""
    )
    body_html = (
        f'<div style="font-size:0.95rem;color:{TEXT};margin-top:0.5rem;'
        f'line-height:1.65;">{esc(body)}</div>'
        if body
        else ""
    )
    return (
        f'<div style="background:{SURFACE_2};border:1px solid {BORDER_LIGHT};'
        f'border-radius:14px;padding:1rem 1.2rem;margin:0.4rem 0;{border}">'
        f'<div style="font-weight:800;color:{TEXT};letter-spacing:0.02em;">'
        f"{esc(title)}</div>{sub}{body_html}</div>"
    )


def hero_html(title: str, subtitle: str = "", meta: str = "") -> str:
    """页面主 Hero:品牌渐变边 + 大标题,用于主区顶部。"""
    sub = (
        f'<div style="font-size:1rem;color:#d8d4ce;margin-top:0.5rem;">'
        f"{esc(subtitle)}</div>"
        if subtitle
        else ""
    )
    meta_html = (
        f'<div style="margin-top:1rem;display:flex;gap:0.4rem;flex-wrap:wrap;'
        f'justify-content:center;">{meta}</div>'
        if meta
        else ""
    )
    return (
        f'<div style="position:relative;overflow:hidden;background:'
        f'linear-gradient(180deg, rgba(255,90,31,0.16), rgba(255,90,31,0.02) 58%),'
        f' {SURFACE};border:1px solid {BORDER_LIGHT};border-radius:18px;'
        f'padding:1.6rem 1.5rem 1.4rem;margin:0.6rem 0 1rem;text-align:center;">'
        f'<div style="position:absolute;left:0;top:0;bottom:0;width:4px;'
        f'background:{BRAND_GRADIENT};border-radius:18px 0 0 18px;"></div>'
        f'<div style="font-size:1.85rem;font-weight:900;letter-spacing:0.01em;'
        f'color:{TEXT};">{esc(title)}</div>{sub}{meta_html}</div>'
    )


def pill(value: Any, color: str | None = None, title: str = "") -> str:
    """Hero 顶部的小信息胶囊。"""
    tip = f' title="{esc(title)}"' if title else ""
    style = (
        f"background:{SURFACE_2};border:1px solid {BORDER_LIGHT};color:{MUTED};"
        if color is None
        else f"background:rgba(0,0,0,0.25);border:1px solid {color};color:{color};"
    )
    return (
        f'<span{tip} style="display:inline-block;padding:0.2rem 0.65rem;'
        f'border-radius:999px;font-size:0.75rem;font-weight:700;{_MONO}{style}">'
        f"{esc(value)}</span>"
    )


def empty_state_html(emoji: str, title: str, body: str = "") -> str:
    """Tab 空状态占位:居中大图标 + 标题 + 说明。

    ``emoji`` 可以是 emoji,也可以是 inline_icon() 返回的 SVG HTML。
    """
    body_html = (
        f'<div style="font-size:0.9rem;color:{MUTED};line-height:1.7;'
        f'margin-top:0.5rem;">{esc(body)}</div>'
        if body
        else ""
    )
    icon_block = (
        f'<div style="font-size:3rem;margin-bottom:0.75rem;color:{PRIMARY};">{emoji}</div>'
        if str(emoji).lstrip().startswith("<")
        else f'<div style="font-size:3rem;margin-bottom:0.75rem;">{esc(emoji)}</div>'
    )
    return (
        f'<div style="text-align:center;padding:2.2rem 1rem;background:'
        f'{SURFACE};border:1px dashed {BORDER_LIGHT};border-radius:16px;'
        f'margin:0.8rem 0;">'
        f"{icon_block}"
        f'<div style="font-size:1.15rem;font-weight:800;color:{TEXT};">'
        f"{esc(title)}</div>{body_html}</div>"
    )


def build_global_css() -> str:
    """生成全局 CSS。所有颜色来自上方令牌,不再散落内联值。"""
    css = """
    html, body, [class*="css"] {
        font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', -apple-system,
            'Segoe UI', sans-serif;
    }
    .stApp {
        background: radial-gradient(1200px 500px at 80% -10%, rgba(255,90,31,0.10),
            transparent 60%), radial-gradient(900px 420px at -10% 0%,
            rgba(255,140,66,0.05), transparent 55%), @BG@;
    }

    /* 保留 sidebar 折叠按钮;只隐藏不需要的 chrome。 */
    #MainMenu, footer,
    div[data-testid="stDecoration"],
    div[data-testid="stStatusWidget"],
    div[data-testid="stToolbarActions"],
    div[data-testid="stAppDeployButton"],
    span[data-testid="stMainMenu"] { display: none !important; }
    header[data-testid="stHeader"] {
        background: transparent !important;
        box-shadow: none !important;
    }
    button[data-testid="stExpandSidebarButton"],
    button[data-testid="stSidebarCollapseButton"],
    button[data-testid="collapsedControl"],
    [data-testid="stSidebarCollapsedControl"] {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;
    }

    .block-container {
        max-width: 1480px;
        padding-top: 1.1rem;
        padding-bottom: 4rem;
    }
    h1, h2, h3 { color: @TEXT@; }
    hr { border-color: @BORDER@; }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, @SURFACE@ 0%, @BG@ 100%);
        border-right: 1px solid @BORDER_LIGHT@;
    }
    section[data-testid="stSidebar"] .stMarkdown,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] .stCaption {
        color: @MUTED@;
    }

    /* Tabs: 深色底 + 橙色激活态 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.25rem;
        border-bottom: 1px solid @BORDER@;
        padding-bottom: 0.2rem;
    }
    .stTabs [data-baseweb="tab-list"] > [data-baseweb="tab"] {
        height: 3rem;
        padding: 0 1.1rem;
        background: transparent;
        border-radius: 10px 10px 0 0;
        color: @MUTED@;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0.5rem !important;
    }
    .stTabs:not(.stTabs .stTabs) [data-baseweb="tab-list"] > [data-baseweb="tab"]::before {
        content: "";
        display: inline-block;
        width: 1.05rem;
        height: 1.05rem;
        flex: 0 0 auto;
        background-color: currentColor;
        -webkit-mask-image: var(--aq-tab-icon, none);
        mask-image: var(--aq-tab-icon, none);
        -webkit-mask-repeat: no-repeat;
        mask-repeat: no-repeat;
        -webkit-mask-position: center;
        mask-position: center;
        -webkit-mask-size: contain;
        mask-size: contain;
        opacity: 0.92;
        transition: transform 0.18s ease, opacity 0.18s ease;
    }
    .stTabs:not(.stTabs .stTabs) [data-baseweb="tab-list"] > [data-baseweb="tab"]:hover::before {
        opacity: 1;
        transform: translateY(-1px);
    }
    .stTabs [aria-selected="true"] {
        background: @SURFACE_2@;
        color: @PRIMARY@ !important;
        border-bottom: 2px solid @PRIMARY@;
    }
    .stTabs [aria-selected="true"] [data-testid="stMarkdownContainer"] p {
        font-weight: 800;
    }

    /* Buttons */
    .stButton > button, button[kind="primary"], button[kind="secondary"] {
        border-radius: 10px;
        transition: all 0.2s ease;
    }
    button[kind="primary"] {
        background: @BRAND_GRADIENT@ !important;
        border: none !important;
        color: #1a0d05 !important;
        font-weight: 800 !important;
        letter-spacing: 0.04em !important;
        box-shadow: 0 6px 18px rgba(255,90,31,0.28) !important;
    }
    button[kind="primary"]:hover {
        filter: brightness(1.05);
        box-shadow: 0 8px 24px rgba(255,90,31,0.38) !important;
        transform: translateY(-1px);
    }
    button[kind="primary"]:disabled {
        background: @SURFACE_2@ !important;
        color: @MUTED@ !important;
        box-shadow: none !important;
    }
    button[kind="secondary"] {
        background: @SURFACE_2@ !important;
        border: 1px solid @BORDER_LIGHT@ !important;
        color: @TEXT@ !important;
    }
    button[kind="secondary"]:hover {
        background: @SURFACE_HOVER@ !important;
        border-color: @PRIMARY@ !important;
        color: @PRIMARY@ !important;
    }

    /* Inputs */
    .stTextInput input, .stNumberInput input, .stDateInput input {
        background: @SURFACE_2@ !important;
        border-color: @BORDER_LIGHT@ !important;
        color: @TEXT@ !important;
        border-radius: 10px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus,
    .stDateInput input:focus {
        border-color: @PRIMARY@ !important;
        box-shadow: 0 0 0 1px @PRIMARY@ !important;
    }
    .stSelectbox [data-baseweb="select"] > div {
        background: @SURFACE_2@ !important;
        border-color: @BORDER_LIGHT@ !important;
        color: @TEXT@ !important;
        border-radius: 10px !important;
    }

    /* Expander / DataFrame / Metric / Alert */
    [data-testid="stExpander"] {
        background: @SURFACE@;
        border: 1px solid @BORDER_LIGHT@ !important;
        border-radius: 12px !important;
        overflow: hidden;
    }
    [data-testid="stExpander"] summary { font-weight: 700; }
    [data-testid="stDataFrame"] {
        border: 1px solid @BORDER@;
        border-radius: 12px;
        overflow: hidden;
    }
    [data-testid="stMetric"] {
        background: @SURFACE_2@;
        border: 1px solid @BORDER_LIGHT@;
        border-radius: 12px;
        padding: 0.7rem 0.9rem;
    }
    [data-testid="stMetric"] label { color: @MUTED@ !important; font-size: 0.8rem !important; }
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: @PRIMARY_SOFT@ !important;
        font-weight: 800 !important;
    }
    .stAlert { border-radius: 12px; }

    .stProgress > div > div > div > div {
        background: @BRAND_GRADIENT@ !important;
    }

    div[data-testid="stDownloadButton"] button {
        background: @SURFACE_2@ !important;
        border: 1px solid @PRIMARY@ !important;
        color: @PRIMARY@ !important;
        border-radius: 10px !important;
    }

    code, pre, .stCode {
        background: @SURFACE_2@;
        border: 1px solid @BORDER@;
        border-radius: 8px;
    }

    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-track { background: @BG@; }
    ::-webkit-scrollbar-thumb { background: @BORDER_LIGHT@; border-radius: 6px; }
    ::-webkit-scrollbar-thumb:hover { background: @PRIMARY_HOVER@; }
    """
    return (
        css.replace("@BG@", BG)
        .replace("@SURFACE@", SURFACE)
        .replace("@SURFACE_2@", SURFACE_2)
        .replace("@SURFACE_HOVER@", SURFACE_HOVER)
        .replace("@BORDER@", BORDER)
        .replace("@BORDER_LIGHT@", BORDER_LIGHT)
        .replace("@TEXT@", TEXT)
        .replace("@MUTED@", MUTED)
        .replace("@PRIMARY@", PRIMARY)
        .replace("@PRIMARY_HOVER@", PRIMARY_HOVER)
        .replace("@PRIMARY_SOFT@", PRIMARY_SOFT)
        .replace("@BRAND_GRADIENT@", BRAND_GRADIENT)
        + chr(10)
        + _tab_icon_css()
    )


def inject_global_css() -> None:
    """把全局 CSS 注入当前 Streamlit 页面(必须在页面内容渲染前调用)。"""
    import streamlit as st

    st.markdown(
        f"<style>{build_global_css()}</style>",
        unsafe_allow_html=True,
    )


# ── 内联矢量图标:与主 Tab 图标同风格的 24x24 细线图标 ──
_ICON_PATHS: dict[str, str] = {
    "chart": "<path d='M4 20V4'/><path d='M4 20h16'/><rect x='7.2' y='11.5' width='3' height='7' rx='0.8' fill='currentColor' stroke='none'/><rect x='12.6' y='8' width='3' height='10.5' rx='0.8' fill='currentColor' stroke='none'/><rect x='18' y='13.5' width='3' height='5' rx='0.8' fill='currentColor' stroke='none'/>",
    "target": "<circle cx='12' cy='12' r='8'/><circle cx='12' cy='12' r='3'/><path d='M12 2v3M12 19v3M2 12h3M19 12h3'/>",
    "briefcase": "<rect x='3.5' y='7' width='17' height='12' rx='2'/><path d='M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2'/><path d='M3.5 12h17'/>",
    "users": "<circle cx='9' cy='8' r='3.5'/><path d='M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6'/><circle cx='17' cy='9' r='2.5'/><path d='M17 14c2.8 0 5 2.2 5 5'/>",
    "scale": "<path d='M12 3v18M7 21h10'/><path d='m5 7 7-3 7 3'/><path d='M5 7l-2.5 7a3 3 0 0 0 5 0L5 7z'/><path d='M19 7l-2.5 7a3 3 0 0 0 5 0L19 7z'/>",
    "shield": "<path d='M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z'/><path d='m9 12 2 2 4-4'/>",
    "check": "<path d='m5 12 4 4L19 6'/>",
    "doc": "<path d='M6 3h9l4 4v14H6z'/><path d='M14 3v5h5'/>",
    "download": "<path d='M12 3v12'/><path d='m7 10 5 5 5-5'/><path d='M4 20h16'/>",
    "trade": "<path d='M5 3.5h11.5L20 7v13.5H5z'/><path d='M15 3.5V7h5'/><path d='m8.5 14 2.5-2.5 2 2 3-3'/><path d='M8.5 10.5v3h3'/>",
    "cpu": "<rect x='6.5' y='6.5' width='11' height='11' rx='2.2'/><rect x='10.5' y='10.5' width='3' height='3' rx='0.8' fill='currentColor' stroke='none'/><path d='M12 2.8v3.7M12 17.5v3.7M2.8 12h3.7M17.5 12h3.7'/><path d='m5.5 5.5 2.6 2.6M15.9 15.9l2.6 2.6M18.5 5.5l-2.6 2.6M8.1 15.9l-2.6 2.6'/>",
}


def inline_icon(
    name: str,
    *,
    color: str | None = None,
    size: int = 18,
    margin_right: str = "0.45rem",
) -> str:
    """返回一个与 Tab 图标同风格的 24x24 细线内联 SVG 图标。"""
    path = _ICON_PATHS.get(name)
    if not path:
        return ""
    style = f"color:{color};" if color else ""
    return (
        f'<span style="display:inline-flex;align-items:center;margin-right:{margin_right};'
        f"vertical-align:-0.18em;{style}\">"
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{path}</svg></span>'
    )


def icon_heading(name: str, title: str, *, color: str | None = None) -> str:
    """带内联 SVG 图标的标题块。"""
    return (
        f'<div style="display:flex;align-items:center;margin:0.9rem 0 0.35rem;'
        f'color:{color or TEXT};">{inline_icon(name, color=color or TEXT)}'
        f'<span style="font-size:1.25rem;font-weight:800;line-height:1.2;">'
        f"{esc(title)}</span></div>"
    )
