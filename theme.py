"""
theme.py — Notifica-huisstijl voor Streamlit: navy kleuren, CSS, KPI-cards,
statuslabels en kleine SVG-iconen. Eén plek voor alle opmaak.
"""
from __future__ import annotations
import base64
from pathlib import Path
import streamlit as st

from config import NAVY, NAVY2, NAVY_LIGHT, GOLD, GREEN, AMBER, RED, GREY

ASSETS = Path(__file__).parent / "assets"

# Statuskleuren voor signalering (dataviz — geen merk-navy nodig hier).
STATUS = {
    "goed":    {"color": GREEN, "bg": "#ECFDF5", "label": "Op koers"},
    "let_op":  {"color": AMBER, "bg": "#FFFBEB", "label": "Aandacht"},
    "risico":  {"color": RED,   "bg": "#FEF2F2", "label": "Risico"},
    "neutraal":{"color": NAVY2, "bg": "#EEF0FB", "label": "Neutraal"},
}

MND = {1: "jan", 2: "feb", 3: "mrt", 4: "apr", 5: "mei", 6: "jun",
       7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec"}


def _logo_b64(name: str) -> str:
    p = ASSETS / name
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode()


def inject_css() -> None:
    st.markdown(f"""<style>
    /* Sidebar — navy gradient */
    [data-testid="stSidebar"] {{background: linear-gradient(180deg,{NAVY} 0%,#1E1B8C 55%,{NAVY2} 100%);}}
    [data-testid="stSidebar"] * {{color:#fff!important;}}
    [data-testid="stSidebar"] label {{color:rgba(255,255,255,.72)!important;font-size:11px;
        text-transform:uppercase;letter-spacing:.4px;font-weight:600;}}
    [data-testid="stSidebar"] hr {{border-color:rgba(255,255,255,.14);}}
    /* Selectbox in de navy sidebar: witte box met DONKERE tekst (anders wit-op-wit) */
    [data-testid="stSidebar"] [data-baseweb="select"] > div {{background:#fff!important;
        border-color:rgba(255,255,255,.35)!important;}}
    [data-testid="stSidebar"] [data-baseweb="select"] * {{color:{NAVY}!important;
        -webkit-text-fill-color:{NAVY}!important;}}
    [data-testid="stSidebar"] [data-baseweb="select"] svg {{fill:{NAVY}!important;}}
    [data-testid="stSidebar"] input {{color:{NAVY}!important;-webkit-text-fill-color:{NAVY}!important;}}
    [data-baseweb="popover"] * {{color:{NAVY}!important;-webkit-text-fill-color:{NAVY}!important;}}
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{color:rgba(255,255,255,.72)!important;}}

    /* Algemeen */
    .block-container {{padding-top:2.2rem;max-width:100%;}}
    h1,h2,h3,h4 {{color:{NAVY}!important;font-family:'Segoe UI',sans-serif;}}
    h1 {{font-size:22px!important;}}
    h2 {{font-size:17px!important;margin-top:.4rem;}}
    h3 {{font-size:14px!important;}}

    /* Topbar */
    .pi-topbar {{display:flex;align-items:center;gap:14px;padding:10px 18px;
        background:linear-gradient(135deg,{NAVY} 0%,{NAVY2} 100%);border-radius:12px;
        margin:4px 0 16px;position:relative;z-index:1;}}
    .pi-topbar img {{height:26px;}}
    .pi-topbar .t {{color:#fff;font-size:15px;font-weight:700;letter-spacing:.2px;}}
    .pi-topbar .s {{color:rgba(255,255,255,.72);font-size:12px;margin-left:auto;text-align:right;}}
    .pi-topbar .s b {{color:{GOLD};}}

    /* KPI cards */
    .kpi-row {{display:flex;gap:12px;flex-wrap:wrap;margin:2px 0 14px;}}
    .kpi {{flex:1;min-width:150px;background:#fff;border:1px solid #E7E9F5;border-radius:12px;
        padding:14px 16px;box-shadow:0 1px 2px rgba(22,19,111,.04);}}
    .kpi .lbl {{font-size:10.5px;color:#7A7DA6;text-transform:uppercase;letter-spacing:.5px;font-weight:700;}}
    .kpi .val {{font-size:26px;font-weight:800;color:{NAVY};line-height:1.15;margin-top:3px;}}
    .kpi .sub {{font-size:11.5px;color:#8A8DB0;margin-top:2px;}}
    .kpi.accent {{border-left:4px solid {GOLD};}}
    .kpi.risk {{border-left:4px solid {RED};}} .kpi.risk .val {{color:{RED};}}
    .kpi.ok {{border-left:4px solid {GREEN};}}
    .kpi.warn {{border-left:4px solid {AMBER};}}

    /* Status-pill */
    .pill {{display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700;}}

    /* Building-block card */
    .bb {{background:#fff;border:1px solid #E7E9F5;border-radius:12px;padding:14px 16px;margin-bottom:10px;}}
    .bb.on {{border-left:4px solid {GREEN};}}
    .bb.off {{border-left:4px solid {GREY};opacity:.62;}}
    .bb .h {{display:flex;align-items:center;gap:10px;}}
    .bb .h .nm {{font-weight:700;color:{NAVY};font-size:14px;}}
    .bb .src {{font-size:11px;color:#8A8DB0;margin-left:auto;background:#F1F2FB;padding:2px 9px;border-radius:6px;}}
    .bb .d {{font-size:12px;color:#5D6089;margin:6px 0 6px;}}
    .bb .f {{font-size:11px;color:#8A8DB0;}}

    .note {{background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:12px 14px;
        font-size:13px;color:#92600A;}}
    .advice {{background:#fff;border:1px solid #E7E9F5;border-left:4px solid {NAVY2};border-radius:10px;
        padding:12px 15px;margin-bottom:10px;}}
    .advice.risk {{border-left-color:{RED};background:#FEF6F6;}}
    .advice.warn {{border-left-color:{AMBER};background:#FFFCF3;}}
    .advice .a-h {{font-weight:700;color:{NAVY};font-size:13.5px;margin-bottom:3px;}}
    .advice .a-b {{font-size:12.5px;color:#4A4D74;line-height:1.5;}}
    .advice .a-t {{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;}}
    </style>""", unsafe_allow_html=True)


def topbar(title: str, subtitle: str) -> None:
    logo = _logo_b64("logo_white.png")
    img = f'<img src="data:image/png;base64,{logo}"/>' if logo else ""
    st.markdown(
        f'<div class="pi-topbar">{img}<span class="t">{title}</span>'
        f'<span class="s">{subtitle}</span></div>', unsafe_allow_html=True)


def kpi_cards(cards: list[dict]) -> None:
    html = '<div class="kpi-row">'
    for c in cards:
        cls = c.get("cls", "")
        sub = f'<div class="sub">{c["sub"]}</div>' if c.get("sub") else ""
        html += (f'<div class="kpi {cls}"><div class="lbl">{c["lbl"]}</div>'
                 f'<div class="val">{c["val"]}</div>{sub}</div>')
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def pill(status: str) -> str:
    s = STATUS.get(status, STATUS["neutraal"])
    return (f'<span class="pill" style="background:{s["bg"]};color:{s["color"]};">'
            f'{s["label"]}</span>')


def advice_card(kind: str, title: str, body: str) -> None:
    tagcol = {"risico": RED, "let_op": AMBER, "goed": GREEN, "neutraal": NAVY2}[kind]
    cls = {"risico": "risk", "let_op": "warn", "goed": "", "neutraal": ""}[kind]
    tag = {"risico": "Actie nodig", "let_op": "Aandacht", "goed": "Kans",
           "neutraal": "Signaal"}[kind]
    st.markdown(
        f'<div class="advice {cls}"><div class="a-t" style="color:{tagcol}">{tag}</div>'
        f'<div class="a-h">{title}</div><div class="a-b">{body}</div></div>',
        unsafe_allow_html=True)
