"""
views_megens.py — De 4 dashboards op ECHTE Megens-data (klant 1142) via de Data API.

Reproduceert de kern van Megens' Power BI Projectenplanning + de slimme laag (ERCO's wensen):
  1. Management (slim)        — nog-in-te-plannen vraag vs. capaciteit over de tijd
  2. Projectplanning / capaciteit — capaciteit per afdeling/medewerker, intern/extern
  3. Begrotingsuren per project   — begroting / geboekt / nog te plannen / overschrijding (hun hoofdpagina)
  4. AI-adviezen               — signalen uit de echte cijfers

Alle getallen komen 1-op-1 uit de geverifieerde queries in megens_source.py.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import megens_source as ms
from config import NAVY, NAVY2, NAVY_LIGHT, GOLD, GREEN, AMBER, RED, GREY
from theme import kpi_cards, pill, advice_card, STATUS

HORIZON_WEKEN = 26
PLOT = dict(font=dict(family="Segoe UI, sans-serif", size=12, color="#3A3D63"),
            paper_bgcolor="white", plot_bgcolor="white",
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", y=1.14, x=0))


def fmt(n) -> str:
    try:
        return f"{float(n):,.0f}".replace(",", ".")
    except Exception:
        return "—"


@st.cache_data(ttl=3600, show_spinner="Megens-data laden via de Data API…")
def _load():
    return ms.load_all()


@st.cache_data(ttl=3600, show_spinner=False)
def _booked_week(project_key: int) -> pd.DataFrame:
    return ms.fetch_booked_per_week_project(ms.get_client(), project_key)


def _horizon(cap, dem):
    starts = []
    if len(cap): starts.append(cap["week_start"].min())
    if len(dem): starts.append(dem["week_start"].min())
    start = min(starts) if starts else pd.Timestamp("2026-08-03")
    weeks = pd.date_range(start=start, periods=HORIZON_WEKEN, freq="7D")
    return weeks


# ══════════════════════════════════════════════════════════════════════════
def view_management(data: ms.MegensData):
    st.subheader("Management overzicht")
    st.caption("Nog in te plannen werk (resterende werkvoorbereiding, methode 2) afgezet tegen de "
               "beschikbare capaciteit. Zo zie je of het openstaande werk in de bemensing past.")
    dem, cap, ov = data.demand_week, data.capacity_week, data.projecten
    weeks = _horizon(cap, dem)
    wset = set(weeks)

    dem_w = dem.groupby("week_start")["vraag_uren"].sum().reindex(weeks, fill_value=0.0)
    cap_w = cap.groupby("week_start")["capaciteit_uren"].sum().reindex(weeks, fill_value=0.0)
    vraag_h = float(dem_w.sum()); cap_h = float(cap_w.sum())
    bez = (vraag_h / cap_h * 100) if cap_h else float("nan")

    n_over = int((ov["overschrijding"] > 0).sum())
    kpi_cards([
        {"lbl": "Nog te plannen (totaal)", "val": fmt(ov["nog_te_plannen"].sum()),
         "sub": "resterende begrote uren", "cls": "accent"},
        {"lbl": f"Capaciteit ({HORIZON_WEKEN} wk)", "val": fmt(cap_h), "sub": "bruto contracturen"},
        {"lbl": "Nog in te plannen (horizon)", "val": fmt(vraag_h), "sub": f"{HORIZON_WEKEN} weken vooruit"},
        {"lbl": "Beslag op capaciteit", "val": f"{bez:.0f}%" if bez == bez else "—",
         "sub": "openstaand werk / capaciteit", "cls": "warn" if bez > 90 else "ok"},
        {"lbl": "Projecten met overschrijding", "val": f"{n_over}",
         "sub": f"van {len(ov)} actief", "cls": "risk" if n_over else "ok"},
    ])

    st.markdown("###### Nog in te plannen werk vs. beschikbare capaciteit per week")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=list(weeks), y=cap_w.values, name="Capaciteit (bruto)",
                             mode="lines", line=dict(color=NAVY2, width=2),
                             fill="tozeroy", fillcolor="rgba(54,54,162,0.10)"))
    fig.add_trace(go.Bar(x=list(weeks), y=dem_w.values, name="Nog in te plannen",
                         marker_color=GOLD, opacity=0.9))
    fig.update_layout(**PLOT, height=330, barmode="overlay")
    fig.update_yaxes(title="uren", gridcolor="#EEF0F7")
    st.plotly_chart(fig, width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("###### Nog te plannen per afdeling (top 10)")
        by_afd = (dem.groupby("afdeling")["vraag_uren"].sum().sort_values(ascending=True).tail(10))
        fig2 = go.Figure(go.Bar(x=by_afd.values, y=by_afd.index, orientation="h",
                                marker_color=NAVY2, text=[fmt(v) for v in by_afd.values],
                                textposition="auto"))
        fig2.update_layout(**PLOT, height=320); fig2.update_xaxes(title="uren", gridcolor="#EEF0F7")
        st.plotly_chart(fig2, width="stretch")
    with c2:
        st.markdown("###### Grootste openstaande projecten (nog te plannen)")
        top = ov.nlargest(10, "nog_te_plannen")[["project", "nog_te_plannen"]].iloc[::-1]
        fig3 = go.Figure(go.Bar(x=top["nog_te_plannen"], y=top["project"], orientation="h",
                                marker_color=GOLD, text=[fmt(v) for v in top["nog_te_plannen"]],
                                textposition="auto"))
        fig3.update_layout(**PLOT, height=320); fig3.update_xaxes(title="uren", gridcolor="#EEF0F7")
        fig3.update_yaxes(tickfont=dict(size=9))
        st.plotly_chart(fig3, width="stretch")


# ══════════════════════════════════════════════════════════════════════════
def view_capaciteit(data: ms.MegensData):
    st.subheader("Projectplanning / capaciteit")
    st.caption("De capaciteitsploeg uit Syntess: contracturen per afdeling en medewerker, "
               "intern vs. ingeleend. (Geen verzuimregistratie in de bron → contract = bruto.)")
    mdw, cap = data.medewerkers, data.capacity_week

    alleen_pp = st.toggle("Alleen medewerkers in projectenplanning", value=True,
                          help="Filter op de vlag 'Projectenplanning (J/N)' uit Syntess")
    m = mdw[mdw["projectplanning"] == "J"] if alleen_pp else mdw

    n_ext = int((m["type"] == "Extern").sum())
    cap_wk = cap.groupby("week_start")["capaciteit_uren"].sum()
    cap_per_wk = cap_wk.mean() if len(cap_wk) else 0
    kpi_cards([
        {"lbl": "Medewerkers", "val": f"{len(m)}", "sub": f"{n_ext} extern ingeleend"},
        {"lbl": "Contractcapaciteit", "val": fmt(m["contract_uren"].sum()), "sub": "uren over de horizon", "cls": "accent"},
        {"lbl": "Capaciteit per week", "val": fmt(cap_per_wk), "sub": "bruto, alle afdelingen"},
        {"lbl": "Extern-aandeel", "val": f"{n_ext/len(m)*100:.0f}%" if len(m) else "—",
         "sub": "van de planningsploeg", "cls": "warn" if len(m) and n_ext/len(m) > 0.3 else "ok"},
    ])

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("###### Contractcapaciteit per afdeling (horizon)")
        by_afd = m.groupby("afdeling")["contract_uren"].sum().sort_values(ascending=True).tail(12)
        fig = go.Figure(go.Bar(x=by_afd.values, y=by_afd.index, orientation="h",
                               marker_color=NAVY2, text=[fmt(v) for v in by_afd.values], textposition="auto"))
        fig.update_layout(**PLOT, height=360); fig.update_xaxes(title="uren", gridcolor="#EEF0F7")
        fig.update_yaxes(tickfont=dict(size=10))
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("###### Intern vs. extern")
        ie = m.groupby("type")["contract_uren"].sum()
        fig = go.Figure(go.Pie(labels=ie.index.tolist(), values=ie.values.tolist(), hole=0.6,
                               marker_colors=[NAVY, GOLD]))
        fig.update_layout(**{**PLOT, "showlegend": True}, height=360)
        st.plotly_chart(fig, width="stretch")

    st.markdown("###### Medewerkers")
    disp = m[["medewerker", "afdeling", "type", "contract_uren"]].copy()
    disp.columns = ["Medewerker", "Afdeling", "Type", "Contracturen (horizon)"]
    st.dataframe(disp, width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════════════════
def view_projecten(data: ms.MegensData):
    st.subheader("Begrotingsuren per project")
    st.caption("Megens' hoofdpagina, nu interactief: per project de begroting, wat er geboekt is, "
               "wat nog te plannen is en of de behoefte wordt overschreden.")
    ov = data.projecten.copy()

    c0a, c0b, c0c = st.columns(3)
    with c0a:
        alleen_uitv = st.toggle("Alleen in uitvoering", value=True,
                                help="Fase 'Opdracht' of 'Technisch gereed' (verbergt regie/storing-shells)")
    fases = sorted(ov["fase"].dropna().unique())
    if alleen_uitv:
        ov = ov[ov["fase"].isin(["Opdracht", "Technisch gereed"])]
    with c0b:
        afd_opts = ["(alle)"] + sorted([a for a in ov["afdeling"].dropna().unique() if a])
        afd = st.selectbox("Afdeling", afd_opts)
    if afd != "(alle)":
        ov = ov[ov["afdeling"] == afd]
    with c0c:
        zoek = st.text_input("Zoek project", "")
    if zoek:
        ov = ov[ov["project"].str.contains(zoek, case=False, na=False)]

    n_over = int((ov["overschrijding"] > 0).sum())
    kpi_cards([
        {"lbl": "Projecten", "val": f"{len(ov)}"},
        {"lbl": "Begroot (uren)", "val": fmt(ov["begrotingsuren"].sum()), "cls": "accent"},
        {"lbl": "Geboekt (uren)", "val": fmt(ov["geboekt"].sum())},
        {"lbl": "Nog te plannen", "val": fmt(ov["nog_te_plannen"].sum()), "cls": "warn"},
        {"lbl": "Met overschrijding", "val": f"{n_over}", "cls": "risk" if n_over else "ok"},
    ])

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("###### Grootste nog te plannen (top 12)")
        top = ov.nlargest(12, "nog_te_plannen")[["project", "nog_te_plannen"]].iloc[::-1]
        fig = go.Figure(go.Bar(x=top["nog_te_plannen"], y=top["project"], orientation="h",
                               marker_color=GOLD, text=[fmt(v) for v in top["nog_te_plannen"]], textposition="auto"))
        fig.update_layout(**PLOT, height=360); fig.update_xaxes(title="uren", gridcolor="#EEF0F7")
        fig.update_yaxes(tickfont=dict(size=9))
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("###### Besteed vs. begroot (bubbel = begroting)")
        d = ov[ov["begrotingsuren"] > 0].copy()
        d["besteed_pct"] = d["besteed_pct"].clip(upper=250)
        over = d["overschrijding"] > 0
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=d.loc[~over, "besteed_pct"], y=d.loc[~over, "nog_te_plannen"],
                                 mode="markers", name="Binnen behoefte",
                                 marker=dict(size=np.sqrt(d.loc[~over, "begrotingsuren"])*0.9,
                                             color=NAVY2, opacity=0.55, line=dict(width=1, color="white")),
                                 text=d.loc[~over, "project"], hovertemplate="%{text}<br>besteed %{x:.0f}%<extra></extra>"))
        fig.add_trace(go.Scatter(x=d.loc[over, "besteed_pct"], y=d.loc[over, "nog_te_plannen"],
                                 mode="markers", name="Overschrijding",
                                 marker=dict(size=np.sqrt(d.loc[over, "begrotingsuren"])*0.9,
                                             color=RED, opacity=0.7, line=dict(width=1, color="white")),
                                 text=d.loc[over, "project"], hovertemplate="%{text}<br>besteed %{x:.0f}%<extra></extra>"))
        fig.add_vline(x=100, line_dash="dot", line_color=GREY)
        fig.update_layout(**PLOT, height=360)
        fig.update_xaxes(title="% van begroting besteed", gridcolor="#EEF0F7")
        fig.update_yaxes(title="nog te plannen (u)", gridcolor="#EEF0F7")
        st.plotly_chart(fig, width="stretch")

    st.markdown("###### Projectenlijst")
    tbl = ov[["project", "fase", "projectleider", "begrotingsuren", "geboekt", "besteed_pct",
              "nog_te_plannen", "overschrijding", "calculatie_uren", "pct_gereed"]].copy()
    tbl["besteed_pct"] = tbl["besteed_pct"].round(0)
    tbl.columns = ["Project", "Fase", "Projectleider", "Begroot", "Geboekt", "Besteed %",
                   "Nog te plannen", "Overschrijding", "Calculatie", "% gereed"]
    st.dataframe(tbl, width="stretch", hide_index=True, column_config={
        "Besteed %": st.column_config.NumberColumn(format="%d%%"),
        "% gereed": st.column_config.NumberColumn(format="%d%%"),
    })

    # ── Drilldown ────────────────────────────────────────────────────────────
    st.markdown("###### Detail per project")
    if len(ov):
        keuze = st.selectbox("Project", ov["project"].tolist())
        r = ov[ov["project"] == keuze].iloc[0]
        bp_txt = f"{r['besteed_pct']:.0f}%" if pd.notna(r["besteed_pct"]) else "n.v.t."
        cc1, cc2 = st.columns([2, 3])
        with cc1:
            st.markdown(
                f'<div style="font-size:13px;line-height:1.9">'
                f'<b>Fase:</b> {r["fase"] or "—"}<br>'
                f'<b>Begroot:</b> {fmt(r["begrotingsuren"])} u<br>'
                f'<b>Calculatie:</b> {fmt(r["calculatie_uren"])} u<br>'
                f'<b>Geboekt:</b> {fmt(r["geboekt"])} u '
                f'({bp_txt} van begroting)<br>'
                f'<b>Nog te plannen:</b> {fmt(r["nog_te_plannen"])} u<br>'
                f'<b>Overschrijding:</b> <span style="color:{RED if r["overschrijding"]>0 else GREEN}">'
                f'{fmt(r["overschrijding"])} u</span></div>', unsafe_allow_html=True)
        with cc2:
            wk = _booked_week(int(r["project_key"]))
            if len(wk):
                wk = wk.sort_values("week_start")
                wk["cum"] = wk["geboekt"].cumsum()
                fig = go.Figure()
                fig.add_trace(go.Bar(x=wk["week_start"], y=wk["geboekt"], name="Geboekt/week", marker_color=NAVY_LIGHT))
                fig.add_trace(go.Scatter(x=wk["week_start"], y=wk["cum"], name="Cumulatief", line=dict(color=NAVY2, width=2)))
                if r["begrotingsuren"] > 0:
                    fig.add_hline(y=r["begrotingsuren"], line_dash="dash", line_color=GOLD,
                                  annotation_text="begroting", annotation_position="top left")
                fig.update_layout(**PLOT, height=240)
                fig.update_yaxes(title="uren", gridcolor="#EEF0F7")
                st.plotly_chart(fig, width="stretch")
            else:
                st.caption("Geen geboekte uren op dit project.")


# ══════════════════════════════════════════════════════════════════════════
def _adviezen(data: ms.MegensData) -> list[tuple]:
    ov, mdw, dem, cap = data.projecten, data.medewerkers, data.demand_week, data.capacity_week
    adv = []

    over = ov[ov["overschrijding"] > 0].sort_values("overschrijding", ascending=False)
    if len(over):
        t = over.iloc[0]
        adv.append(("risico", f"{len(over)} projecten overschrijden de begrote uren",
                    f"Grootste: <b>{t['project']}</b> — <b>+{fmt(t['overschrijding'])} u</b> boven behoefte "
                    f"({fmt(t['geboekt'])} geboekt op {fmt(t['begrotingsuren'])} begroot). "
                    f"Samen ± {fmt(over['overschrijding'].sum())} u overschrijding. Stuur op meerwerk-facturatie en bijsturing."))

    net_gestart = ov[(ov["geboekt"] < 0.15 * ov["begrotingsuren"]) & (ov["nog_te_plannen"] > 500)]
    if len(net_gestart):
        adv.append(("let_op", f"{len(net_gestart)} grote projecten staan grotendeels nog in te plannen",
                    f"Samen <b>{fmt(net_gestart['nog_te_plannen'].sum())} u</b> nog te plannen met nog nauwelijks "
                    f"geboekte uren. Zorg dat deze tijdig in de weekplanning landen — dit is de komende "
                    f"capaciteitsclaim."))

    pp = mdw[mdw["projectplanning"] == "J"]
    if len(pp):
        ext = int((pp["type"] == "Extern").sum())
        if ext / len(pp) > 0.30:
            adv.append(("let_op", "Hoge afhankelijkheid van ingeleend personeel",
                        f"<b>{ext/len(pp)*100:.0f}%</b> van de planningsploeg ({ext} van {len(pp)}) is extern "
                        f"ingeleend. Prima voor pieken, maar bewaak marge, continuïteit en kennisborging."))

    ntp = ov["nog_te_plannen"].sum()
    cap_12 = cap.groupby("week_start")["capaciteit_uren"].sum().head(12).sum()
    if ntp > 0 and cap_12 > 0:
        adv.append(("neutraal", "Openstaand werk past ruim in de bruto capaciteit",
                    f"Totaal <b>{fmt(ntp)} u</b> nog te plannen; de bruto capaciteit is ± {fmt(cap_12)} u over "
                    f"12 weken. Let op: dit is capaciteit vóór verlof/ziekte (niet in Syntess geregistreerd) "
                    f"én exclusief het reeds lopende werk — de effectieve ruimte is kleiner."))

    afd_top = dem.groupby("afdeling")["vraag_uren"].sum().sort_values(ascending=False)
    if len(afd_top):
        a = afd_top.index[0]
        adv.append(("neutraal", f"Meeste openstaande vraag bij afdeling {a}",
                    f"<b>{fmt(afd_top.iloc[0])} u</b> nog in te plannen werk zit bij <b>{a}</b>. "
                    f"Controleer of die afdeling de bemensing heeft om dit op te vangen."))
    if not adv:
        adv.append(("goed", "Geen bijzonderheden", "Geen projecten met overschrijding en het openstaande werk is beheersbaar."))
    return adv


def view_ai(data: ms.MegensData):
    st.subheader("AI-adviezen")
    st.markdown('<div style="font-size:12.5px;color:#8A8DB0;margin-bottom:10px">Signalen automatisch '
                'afgeleid uit Megens\' echte Syntess-data. In productie schrijft Claude hier een '
                'bestuurlijke samenvatting op basis van dezelfde signalen.</div>', unsafe_allow_html=True)
    for kind, title, body in _adviezen(data):
        advice_card(kind, title, body)
    if not os.getenv("ANTHROPIC_API_KEY"):
        st.caption("💡 Zet ANTHROPIC_API_KEY in .env voor een live Claude-samenvatting.")


# ══════════════════════════════════════════════════════════════════════════
def render(tab_key: str):
    data = _load()
    {"management": view_management, "capaciteit": view_capaciteit,
     "projecten": view_projecten, "ai": view_ai}[tab_key](data)
