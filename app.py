"""
Planning Intelligence Tool — Notifica
=====================================
Schaalbare capaciteits- & projectplanning voor installatiebedrijven.

De tool wordt gedreven door configuratie (config.py): per klant staan building blocks
en dashboards aan/uit.

- Klant "Megens (echte data)" draait LIVE op Syntess-data (klant 1142) via de Notifica
  Data API — een reproductie van hun Power BI Projectenplanning + de slimme laag.
- De overige (voorbeeld)profielen draaien op synthetische data en demonstreren de
  config-gedreven building blocks.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent / ".env"))

from config import CLIENTS, BLOCKS, VIEWS, DEFAULT_CLIENT
import theme
import views
import views_megens
from data_gen import generate

st.set_page_config(page_title="Planning Intelligence — Notifica",
                   page_icon="📊", layout="wide", initial_sidebar_state="expanded")
theme.inject_css()


@st.cache_data(show_spinner=False)
def load_bundle(client_key: str, horizon: int, seed: int, n_teams: int, n_mdw: int, n_proj: int):
    prof = CLIENTS[client_key]
    prof = replace(prof, horizon_weeks=horizon, seed=seed, n_teams=n_teams,
                   n_medewerkers=n_mdw, n_projecten=n_proj)
    return generate(prof)


# ── Sidebar: klantkiezer ─────────────────────────────────────────────────────
with st.sidebar:
    logo = theme._logo_b64("logo_white.png")
    if logo:
        st.markdown(f'<img src="data:image/png;base64,{logo}" style="height:30px;margin:4px 0 14px">',
                    unsafe_allow_html=True)
    st.markdown("### Planning Intelligence")
    client_key = st.selectbox("Klantprofiel", list(CLIENTS.keys()),
                              format_func=lambda k: CLIENTS[k].name,
                              index=list(CLIENTS.keys()).index(DEFAULT_CLIENT))
    base = CLIENTS[client_key]
    st.caption(base.tagline)


# ══════════════════════════════════════════════════════════════════════════
# MEGENS — echte data (Notifica Data API)
# ══════════════════════════════════════════════════════════════════════════
if base.data_mode == "megens":
    with st.sidebar:
        st.divider()
        st.markdown("**Databron**")
        st.caption("Live Syntess-data van Megens (klant 1142) via de Notifica Data API. "
                   "Read-only; geen data in de tool opgeslagen.")
        st.divider()
        st.caption("Building blocks (allemaal live gevoed):")
        for blk in BLOCKS.values():
            st.markdown(f"<div style='font-size:12px;color:rgba(255,255,255,.8)'>● {blk.label}</div>",
                        unsafe_allow_html=True)

    theme.topbar("Planning Intelligence Tool",
                 "Klant: <b>Megens</b> &nbsp;·&nbsp; live Syntess-data (1142) via <b>Data API</b>")

    MTABS = [("Management", "management"),
             ("Projectplanning / capaciteit", "capaciteit"),
             ("Begrotingsuren per project", "projecten"),
             ("AI-adviezen", "ai"),
             ("⚙ Bronnen", "bronnen")]
    tabs = st.tabs([t[0] for t in MTABS])
    for tab, (_, key) in zip(tabs, MTABS):
        with tab:
            if key == "bronnen":
                st.subheader("Bronnen — building blocks op echte data")
                st.caption("Dezelfde vastlegging als in Syntess; de tool leest de gemigreerde views "
                           "read-only via de Notifica Data API. Per klant configureerbaar.")
                rows = [
                    ("Benodigde / begrote uren", 'maatwerk."Begrotingsuren" + "Begrotinguren per werkdag"', "vraag per project per week (methode 2)"),
                    ("Beschikbaarheid / capaciteit", 'planning."Geplande en contracturen medewerkers"', "contract- & vrije uren per medewerker/week"),
                    ("Werkelijk bestede uren", 'uren."Geboekte Uren"', "geboekt per project (definitief)"),
                    ("Teams & medewerkers", 'stam."Afdelingen" + "Medewerkers"', "team-mapping + intern/extern"),
                    ("Calculatie", 'projecten."Calculatieregels"', "gecalculeerde uren per project"),
                ]
                html = ('<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
                        '<tr style="color:#8A8DB0;font-size:10.5px;text-transform:uppercase">'
                        '<th style="text-align:left;padding:6px 8px">Building block</th>'
                        '<th style="text-align:left;padding:6px 8px">Bron (db 1142)</th>'
                        '<th style="text-align:left;padding:6px 8px">Levert</th></tr>')
                for nm, src, lev in rows:
                    html += (f'<tr><td style="padding:6px 8px;font-weight:600;color:#16136F">{nm}</td>'
                             f'<td style="padding:6px 8px;font-family:monospace;font-size:11px">{src}</td>'
                             f'<td style="padding:6px 8px;color:#5D6089">{lev}</td></tr>')
                html += "</table>"
                st.markdown(html, unsafe_allow_html=True)
            else:
                views_megens.render(key)

    st.markdown('<div style="text-align:center;color:#B7B9D0;font-size:11px;margin-top:24px">'
                'Notifica · Planning Intelligence — live op Megens Syntess-data via de Data API</div>',
                unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
# SYNTHETISCHE VOORBEELDPROFIELEN — config/building-block demonstratie
# ══════════════════════════════════════════════════════════════════════════
else:
    with st.sidebar:
        st.divider()
        st.markdown("**Building blocks**")
        st.caption("Zet databronnen aan/uit — de dashboards passen zich direct aan.")
        blocks = {k: st.toggle(blk.label, value=base.blocks.get(k, False), key=f"bb_{client_key}_{k}")
                  for k, blk in BLOCKS.items()}
        st.markdown("**Dashboards**")
        vviews = {k: st.toggle(v.label, value=base.views.get(k, False), key=f"vw_{client_key}_{k}")
                  for k, v in VIEWS.items()}
        st.divider()
        with st.expander("Instellingen"):
            horizon = st.slider("Planningshorizon (weken)", 8, 40, base.horizon_weeks)
            target = st.slider("Streefbezetting (%)", 70, 110, int(base.target_utilization * 100)) / 100

    profile = replace(base, blocks=blocks, views=vviews, horizon_weeks=horizon, target_utilization=target)
    bundle = load_bundle(client_key, profile.horizon_weeks, base.seed,
                         base.n_teams, base.n_medewerkers, base.n_projecten)

    n_on = sum(blocks.values())
    theme.topbar("Planning Intelligence Tool",
                 f"Klant: <b>{base.name}</b> &nbsp;·&nbsp; {n_on}/{len(BLOCKS)} building blocks actief "
                 f"&nbsp;·&nbsp; horizon {profile.horizon_weeks} wk &nbsp;·&nbsp; <b>synthetische demo</b>")

    active_views = [k for k in VIEWS if vviews.get(k)]
    RENDER = {"management": views.view_management, "team": views.view_team,
              "project": views.view_project, "ai": views.view_ai, "setup": views.view_setup}
    if not active_views:
        st.info("Geen dashboards actief. Zet er één aan in de zijbalk, of bekijk **Inrichting**.")
        views.view_setup(bundle, profile)
    else:
        tab_keys = active_views + ["setup"]
        tab_labels = [VIEWS[k].label for k in active_views] + ["⚙ Inrichting"]
        for tab, key in zip(st.tabs(tab_labels), tab_keys):
            with tab:
                RENDER[key](bundle, profile)

    st.markdown('<div style="text-align:center;color:#B7B9D0;font-size:11px;margin-top:24px">'
                'Notifica · Planning Intelligence Tool — synthetische demo · niet voor productiebeslissingen</div>',
                unsafe_allow_html=True)
