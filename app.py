"""
Planning Intelligence Tool — Notifica
=====================================
Capaciteits- en projectplanning voor installatiebedrijven.

Architectuur: bron → canoniek contract → één set analyses.

    source_megens.py     ┐
    source_synthetic.py  ┼─► contract.PlanningData ─► an_*.py (5 analyses)
    (nieuwe klant: 1 adapter)

Per klant staat in config.py welke building blocks aan staan en met welke parameters
gerekend wordt. In de **configuratiemodus** (intern — niet met de klant delen) schakel
je dat live: dat is de inrichtingssessie per klant.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent / ".env"))

from config import CLIENTS, BLOCKS, ANALYSES, DEFAULT_CLIENT, ClientProfile
import contract
import seasonality as sn
import theme
import an_hoofd, an_balans, an_teams, an_projecten, an_controle, an_adviezen

st.set_page_config(page_title="Planning Intelligence — Notifica",
                   page_icon="📊", layout="wide", initial_sidebar_state="expanded")
theme.inject_css()

RENDER = {
    "_hoofd": an_hoofd.render,
    "balans": an_balans.render,
    "teams": an_teams.render,
    "projecten": an_projecten.render,
    "controle": an_controle.render,
    "adviezen": an_adviezen.render,
}


# ── Data laden (gecacht op alles wat het resultaat beïnvloedt) ───────────────
@st.cache_data(ttl=1800, show_spinner="Data laden…")
def _laad(client_key: str, seizoen: bool, blokken: tuple, horizon: int,
          vakantie: float, adv: float, ziekte: float, opleiding: float, upd: float):
    prof = CLIENTS[client_key]
    prof = replace(prof, blocks=dict(blokken), horizon_weken=horizon,
                   vakantiedagen=vakantie, adv_dagen=adv,
                   ziekte_pct=ziekte, opleiding_pct=opleiding, uren_per_dag=upd)
    params = sn.SeasonParams(vakantiedagen=vakantie, adv_dagen=adv,
                             ziekte_pct=ziekte / 100.0, opleiding_pct=opleiding / 100.0,
                             uren_per_dag=upd)
    if prof.data_mode == "megens":
        import source_megens
        return source_megens.load(params=params, seizoen=seizoen)
    import source_synthetic
    return source_synthetic.load(prof, params=params, seizoen=seizoen)


# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
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

    st.divider()
    config_mode = st.toggle("Configuratiemodus", value=False,
                            help="Intern: building blocks en rekenparameters instellen. "
                                 "Niet bedoeld om met de klant te delen.")
    if config_mode:
        st.caption("Intern — niet met de klant delen.")

    # Defaults uit het profiel
    blokken = dict(base.blocks)
    analyses_aan = dict(base.analyses)
    horizon = base.horizon_weken
    streef = base.streefbezetting
    vakantie, adv = base.vakantiedagen, base.adv_dagen
    ziekte, opleiding, upd = base.ziekte_pct, base.opleiding_pct, base.uren_per_dag
    eff_pct = base.default_efficiency

    if config_mode:
        st.markdown("**Databronnen**")
        for k, blk in BLOCKS.items():
            if blk.soort != "databron":
                continue
            blokken[k] = st.toggle(blk.label, value=blokken.get(k, False),
                                   key=f"bb_{client_key}_{k}", help=blk.uitleg)

        st.markdown("**Rekenopties**")
        for k, blk in BLOCKS.items():
            if blk.soort != "rekenoptie":
                continue
            blokken[k] = st.toggle(blk.label, value=blokken.get(k, False),
                                   key=f"ro_{client_key}_{k}", help=blk.uitleg)

        if blokken.get("seizoen"):
            with st.expander("Parameters seizoenscorrectie"):
                vakantie = st.number_input("Verlofdagen per jaar", 0.0, 40.0, float(vakantie), 1.0)
                adv = st.number_input("ADV-dagen per jaar", 0.0, 20.0, float(adv), 1.0)
                ziekte = st.number_input("Ziekteverzuim (%)", 0.0, 20.0, float(ziekte), 0.5)
                opleiding = st.number_input("Opleiding (%)", 0.0, 20.0, float(opleiding), 0.5)
                upd = st.number_input("Uren per dag", 4.0, 12.0, float(upd), 0.5)
        if blokken.get("efficiency"):
            eff_pct = st.slider("Efficiency-factor (%)", 50, 100, int(eff_pct))

        st.markdown("**Analyses**")
        for k, an in ANALYSES.items():
            analyses_aan[k] = st.toggle(an.label, value=analyses_aan.get(k, False),
                                        key=f"an_{client_key}_{k}", help=an.uitleg)

        with st.expander("Overig"):
            horizon = st.slider("Planningshorizon (weken)", 8, 52, int(horizon))
            streef = st.slider("Streefbezetting (%)", 60, 110, int(streef))
    else:
        st.divider()
        st.caption("Actieve building blocks")
        for k, blk in BLOCKS.items():
            if blokken.get(k):
                st.markdown(f"<div style='font-size:12px;color:rgba(255,255,255,.82)'>● {blk.label}</div>",
                            unsafe_allow_html=True)

profile = replace(base, blocks=blokken, analyses=analyses_aan, horizon_weken=horizon,
                  streefbezetting=streef, vakantiedagen=vakantie, adv_dagen=adv,
                  ziekte_pct=ziekte, opleiding_pct=opleiding, uren_per_dag=upd,
                  default_efficiency=eff_pct)

opts = {"seizoen": bool(blokken.get("seizoen")), "efficiency": bool(blokken.get("efficiency")),
        "efficiency_pct": int(eff_pct), "horizon": int(horizon), "streef": int(streef)}

# ── Data ophalen ─────────────────────────────────────────────────────────────
try:
    data = _laad(client_key, opts["seizoen"], tuple(sorted(blokken.items())), int(horizon),
                 float(vakantie), float(adv), float(ziekte), float(opleiding), float(upd))
except Exception as e:
    theme.topbar("Planning Intelligence Tool", "databron niet beschikbaar")
    st.error(f"**Kan de data niet laden.**\n\n{e}\n\n"
             "Bij een klantprofiel op echte data: controleer of **NOTIFICA_DATA_KEY** is gezet "
             "in App Beheer → deze draft → Environment, en herstart de app.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════
# HOOFDSCHERM
# ══════════════════════════════════════════════════════════════════════════
sub = f"Klant: <b>{data.meta.klant}</b> &nbsp;·&nbsp; {data.meta.bron_label}"
if opts["seizoen"]:
    sub += " &nbsp;·&nbsp; <b>seizoenscorrectie aan</b>"
theme.topbar("Planning Intelligence Tool", sub)

# Klantweergave: twee tabs — de vraag, en de projecten.
# Configuratiemodus: alle losse analyses + inrichting (intern).
if not config_mode:
    labels = ["Kun je het werk aan?", "Projecten"]
    keys = ["_hoofd", "projecten"]
else:
    actief = [k for k in ANALYSES if analyses_aan.get(k)]
    labels = ["Kun je het werk aan?"] + [ANALYSES[k].label for k in actief] + ["⚙ Inrichting"]
    keys = ["_hoofd"] + list(actief) + ["_inrichting"]

if True:
    for tab, key in zip(st.tabs(labels), keys):
        with tab:
            if key == "_inrichting":
                st.subheader("Inrichting — building blocks")
                st.caption("Intern overzicht van de configuratie voor deze klant. "
                           "Zet blokken en parameters aan/uit in de zijbalk.")
                st.markdown("###### Databronnen")
                for k, blk in BLOCKS.items():
                    aan = blokken.get(k, False)
                    gevuld = data.meta.blokken.get(k)
                    status = ("● actief" if aan else "○ uit")
                    extra = ""
                    if blk.soort == "databron" and aan and gevuld is False:
                        extra = " <span style='color:#B9770E'>— bron levert dit (nog) niet</span>"
                    st.markdown(
                        f'<div class="bb {"on" if aan else "off"}"><div class="h">'
                        f'<span class="nm">{status} {blk.label}</span>'
                        f'<span class="src">{blk.bron}</span></div>'
                        f'<div class="d">{blk.uitleg}{extra}</div></div>', unsafe_allow_html=True)

                if data.meta.bronnen:
                    st.markdown("###### Herkomst van de data")
                    rows = "".join(
                        f'<tr><td style="padding:6px 8px;font-weight:600;color:#16136F">{b}</td>'
                        f'<td style="padding:6px 8px;font-family:monospace;font-size:11px">{s}</td>'
                        f'<td style="padding:6px 8px;color:#5D6089">{l}</td></tr>'
                        for b, s, l in data.meta.bronnen)
                    st.markdown(
                        '<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
                        '<tr style="color:#8A8DB0;font-size:10.5px;text-transform:uppercase">'
                        '<th style="text-align:left;padding:6px 8px">Building block</th>'
                        '<th style="text-align:left;padding:6px 8px">Bron</th>'
                        '<th style="text-align:left;padding:6px 8px">Levert</th></tr>'
                        f'{rows}</table>', unsafe_allow_html=True)

                st.markdown("###### Contract-validatie")
                problemen = contract.valideer(data)
                if problemen:
                    for p in problemen:
                        st.warning(p)
                else:
                    st.success("De bron levert alle canonieke frames volgens contract.")
            else:
                RENDER[key](data, profile, opts)

st.markdown('<div style="text-align:center;color:#B7B9D0;font-size:11px;margin-top:24px">'
            'Notifica · Planning Intelligence</div>', unsafe_allow_html=True)
