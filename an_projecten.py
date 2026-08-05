"""
an_projecten.py — Analyse "Projectvoortgang".

Vervangt de statische Power BI-pagina: per project begroot vs. geboekt vs. nog te
plannen, plus een vooruitblik (burn-up) die laat zien of het resterende werk nog
binnen de begroting landt.

Werkt uitsluitend op het canonieke contract (contract.PlanningData):
  • verplicht : projecten
  • optioneel : realisatie (burn-up), vraag (vooruitblik), prognose (bijsturing)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from config import (NAVY, NAVY2, NAVY_LIGHT, GOLD, GREEN, AMBER, RED, GREY,
                    ClientProfile)
from contract import PlanningData
from an_common import PLOT, fmt, pct, guard, caveat_box
from theme import kpi_cards

# Fases die "in uitvoering" betekenen (Syntess-projectfases; klant-configureerbaar)
FASES_UITVOERING = ["Opdracht", "Technisch gereed"]

NUM_KOLOMMEN = ["begroot", "geboekt", "nog_te_plannen", "overschrijding", "calculatie"]


# ── Voorbereiding ──────────────────────────────────────────────────────────
def _prep(projecten: pd.DataFrame) -> pd.DataFrame:
    """Numeriek maken en afgeleiden berekenen (deling door nul afgevangen)."""
    ov = projecten.copy()
    for k in NUM_KOLOMMEN:
        ov[k] = pd.to_numeric(ov.get(k), errors="coerce").fillna(0.0)
    ov["pct_gereed"] = pd.to_numeric(ov.get("pct_gereed"), errors="coerce")
    for k in ("project", "fase", "projectleider", "team"):
        if k not in ov.columns:
            ov[k] = None
    ov["project"] = ov["project"].fillna("(zonder projectnaam)").astype(str)

    ov["besteed_pct"] = ov["geboekt"] / ov["begroot"].replace(0, np.nan) * 100
    ov["verwacht"] = ov["geboekt"] + ov["nog_te_plannen"]
    ov["boven_begroting"] = ov["verwacht"] - ov["begroot"]
    return ov.sort_values("nog_te_plannen", ascending=False)


def _kleur_boven(waarde: float) -> str:
    return RED if waarde > 0 else GREEN


def _week_serie(df: pd.DataFrame, project_key) -> pd.Series:
    """Uren per week voor één project, opgeteld en op datum gesorteerd."""
    if df is None or not len(df) or "project_key" not in df.columns:
        return pd.Series(dtype="float64")
    d = df[df["project_key"] == project_key].copy()
    if not len(d):
        return pd.Series(dtype="float64")
    d["week_start"] = pd.to_datetime(d["week_start"], errors="coerce")
    d = d.dropna(subset=["week_start"])
    if not len(d):
        return pd.Series(dtype="float64")
    return d.groupby("week_start")["uren"].sum().sort_index()


# ── Hoofd-render ───────────────────────────────────────────────────────────
def render(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    if guard(data, "projecten"):
        return

    st.subheader("Projectvoortgang")
    st.caption("Per project: wat is begroot, wat is er geboekt en wat moet er nog ingepland "
               "worden. De vooruitblik per project laat zien of het resterende werk nog "
               "binnen de begroting landt.")

    ov_all = _prep(data.projecten)
    heeft_fase = ov_all["fase"].notna().any()
    ov = ov_all

    # ── 1. Filterrij ───────────────────────────────────────────────────────
    f1, f2, f3 = st.columns(3)
    with f1:
        alleen_uitv = st.toggle(
            "Alleen in uitvoering", value=bool(heeft_fase), key="pr_alleen_uitv",
            disabled=not heeft_fase,
            help=("Fase " + " of ".join(f"'{f}'" for f in FASES_UITVOERING) +
                  " — verbergt regie- en storingsprojecten."
                  if heeft_fase else "Deze bron levert geen projectfase."))
    if heeft_fase and alleen_uitv:
        ov = ov[ov["fase"].isin(FASES_UITVOERING)]
    with f2:
        teams = sorted([t for t in ov["team"].dropna().unique() if str(t).strip()])
        team_keuze = st.selectbox("Team", ["(alle)"] + teams, key="pr_team")
    if team_keuze != "(alle)":
        ov = ov[ov["team"] == team_keuze]
    with f3:
        # Zowel kiezen als zoeken: een selectbox is te lang om door te scrollen bij honderden
        # projecten, een zoekveld alleen dwingt je te weten hoe het project heet. Dus beide —
        # de lijst volgt de zoekterm, en met '(alle)' blijft het hele overzicht staan.
        zoek = st.text_input("Zoek project", "", key="pr_zoek",
                            placeholder="deel van de naam of het nummer…")
        namen = sorted(ov["project"].dropna().astype(str).unique())
        if zoek:
            namen = [n for n in namen if zoek.lower() in n.lower()]
        keuze = st.selectbox(f"Of kies een project ({len(namen)})", ["(alle)"] + namen,
                            key="pr_keuze")
    if keuze != "(alle)":
        ov = ov[ov["project"].astype(str) == keuze]
    elif zoek:
        ov = ov[ov["project"].str.contains(zoek, case=False, na=False, regex=False)]

    if not len(ov):
        st.info("Geen projecten die aan deze filters voldoen. Pas het filter of de zoekterm aan.")
        caveat_box(data)
        return

    # ── 2. KPI-rij ─────────────────────────────────────────────────────────
    n_over = int((ov["overschrijding"] > 0).sum())
    kpi_cards([
        {"lbl": "Projecten", "val": f"{len(ov)}",
         "sub": f"van {len(ov_all)} in de bron"},
        {"lbl": "Begroot (uren)", "val": fmt(ov["begroot"].sum()), "cls": "accent"},
        {"lbl": "Geboekt (uren)", "val": fmt(ov["geboekt"].sum())},
        {"lbl": "Nog te plannen", "val": fmt(ov["nog_te_plannen"].sum()), "cls": "warn"},
        {"lbl": "Met overschrijding", "val": f"{n_over}",
         "cls": "risk" if n_over else "ok",
         "sub": f"+{fmt(ov['overschrijding'].sum())} u totaal" if n_over else "geen"},
    ])

    # ── 3 + 4. Grafieken ───────────────────────────────────────────────────
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("###### Grootste nog in te plannen werk (top 12)")
        top = ov[ov["nog_te_plannen"] > 0].nlargest(12, "nog_te_plannen")
        if len(top):
            top = top[["project", "nog_te_plannen"]].iloc[::-1]
            fig = go.Figure(go.Bar(
                x=top["nog_te_plannen"], y=top["project"], orientation="h",
                marker_color=GOLD, text=[fmt(v) for v in top["nog_te_plannen"]],
                textposition="auto",
                hovertemplate="%{y}<br>%{x:.0f} uur nog te plannen<extra></extra>"))
            fig.update_layout(**PLOT, height=380)
            fig.update_xaxes(title="uren nog te plannen", gridcolor="#EEF0F7")
            fig.update_yaxes(tickfont=dict(size=9))
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("Geen openstaande uren binnen deze selectie.")

    with g2:
        st.markdown("###### Verdeling: percentage van de begroting besteed")
        d = ov[(ov["begroot"] > 0) & ov["besteed_pct"].notna()].copy()
        if len(d):
            bins = [-0.001, 25, 50, 75, 100, 125, 150, float("inf")]
            labels = ["0-25%", "25-50%", "50-75%", "75-100%", "100-125%", "125-150%", "150%+"]
            d["bucket"] = pd.cut(d["besteed_pct"], bins=bins, labels=labels, right=True)
            counts = d["bucket"].value_counts().reindex(labels, fill_value=0)
            colors = [NAVY_LIGHT, NAVY_LIGHT, NAVY2, NAVY2, AMBER, RED, RED]
            fig = go.Figure(go.Bar(
                x=labels, y=counts.values, marker_color=colors,
                text=counts.values, textposition="outside",
                hovertemplate="%{x} van de begroting besteed<br>%{y} projecten<extra></extra>"))
            # Grens waar de begroting bereikt wordt (tussen 75-100% en 100-125%)
            fig.add_shape(type="line", x0=3.5, x1=3.5, xref="x", y0=0, y1=1, yref="paper",
                          line=dict(color=NAVY, width=1, dash="dot"))
            fig.add_annotation(x=3.5, xref="x", y=1.0, yref="paper", yanchor="bottom",
                               text="begroting bereikt", showarrow=False,
                               font=dict(size=10, color=NAVY))
            fig.update_layout(**PLOT, height=380)
            fig.update_yaxes(title="aantal projecten", gridcolor="#EEF0F7")
            fig.update_xaxes(title="geboekt als % van begroting")
            st.plotly_chart(fig, width="stretch")
            n_voorbij = int((d["besteed_pct"] > 100).sum())
            st.caption(f"Bij {n_voorbij} van de {len(d)} projecten met begrote uren is de "
                       f"begroting al voorbij. Projecten zonder begrote uren staan hier niet in.")
        else:
            st.caption("Geen projecten met begrote uren binnen deze selectie.")

    # ── 5. Projectenlijst ──────────────────────────────────────────────────
    st.markdown("###### Projectenlijst")
    tbl = ov[["project", "fase", "projectleider", "begroot", "geboekt", "besteed_pct",
              "nog_te_plannen", "overschrijding", "calculatie", "pct_gereed"]].copy()
    tbl["besteed_pct"] = tbl["besteed_pct"].round(0)
    tbl["pct_gereed"] = tbl["pct_gereed"].round(0)
    for k in ("begroot", "geboekt", "nog_te_plannen", "overschrijding", "calculatie"):
        tbl[k] = tbl[k].round(0)
    tbl.columns = ["Project", "Fase", "Projectleider", "Begroot", "Geboekt", "Besteed %",
                   "Nog te plannen", "Overschrijding", "Calculatie", "% gereed"]
    st.dataframe(tbl, width="stretch", hide_index=True, column_config={
        "Besteed %": st.column_config.NumberColumn(format="%d%%"),
        "% gereed": st.column_config.NumberColumn(format="%d%%"),
    })

    # ── 6. Drilldown ───────────────────────────────────────────────────────
    st.markdown("###### Detail per project")
    keuzes = ov["project_key"].tolist()
    labels = dict(zip(ov["project_key"], ov["project"]))
    pk = st.selectbox("Project", keuzes, key="pr_detail",
                      format_func=lambda k: str(labels.get(k, k)))
    r = ov[ov["project_key"] == pk].iloc[0]

    begroot = float(r["begroot"])
    geboekt = float(r["geboekt"])
    ntp = float(r["nog_te_plannen"])
    verwacht = float(r["verwacht"])
    boven = float(r["boven_begroting"])
    bp = r["besteed_pct"]

    besteed_txt = (f"{pct(bp)} van de begroting" if pd.notna(bp)
                   else "geen begrote uren vastgelegd")

    d1, d2 = st.columns([2, 3])
    with d1:
        prog_html = ""
        if data.heeft("prognose"):
            pr = data.prognose[data.prognose["project_key"] == pk]
            if len(pr):
                p0 = pr.iloc[0]
                eind = pd.to_datetime(p0.get("prognose_eind"), errors="coerce")
                rest = pd.to_numeric(p0.get("resterend"), errors="coerce")
                prog_html = (
                    f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid #E7E9F5">'
                    f'<b>Prognose einddatum:</b> '
                    f'{eind.strftime("%d-%m-%Y") if pd.notna(eind) else "—"}<br>'
                    f'<b>Resterend volgens prognose:</b> '
                    f'{fmt(rest) if pd.notna(rest) else "—"} u<br>'
                    f'<b>Opmerking:</b> {p0.get("opmerking") or "—"}</div>')

        st.markdown(
            f'<div style="font-size:13px;line-height:1.9">'
            f'<b>Fase:</b> {r["fase"] if pd.notna(r["fase"]) else "—"}<br>'
            f'<b>Team:</b> {r["team"] if pd.notna(r["team"]) else "—"}<br>'
            f'<b>Begroot:</b> {fmt(begroot)} u<br>'
            f'<b>Calculatie:</b> {fmt(r["calculatie"])} u<br>'
            f'<b>Geboekt:</b> {fmt(geboekt)} u ({besteed_txt})<br>'
            f'<b>Nog in te plannen:</b> {fmt(ntp)} u<br>'
            f'<b>Verwachte eindstand:</b> {fmt(verwacht)} u '
            f'<span style="color:{_kleur_boven(boven)};font-weight:700">'
            f'({"+" if boven >= 0 else ""}{fmt(boven)} u t.o.v. begroting)</span>'
            f'{prog_html}</div>', unsafe_allow_html=True)

    with d2:
        rl = _week_serie(data.realisatie, pk) if data.heeft("realisatie") else pd.Series(dtype="float64")
        fwd = _week_serie(data.vraag, pk) if data.heeft("vraag") else pd.Series(dtype="float64")

        if not len(rl) and not len(fwd):
            st.caption("Voor dit project zijn geen geboekte uren per week en geen ingeplande "
                       "uren beschikbaar. De burn-up kan daarom niet getekend worden; de "
                       "totalen links komen uit het projectoverzicht.")
        else:
            vandaag = pd.Timestamp.today().normalize()
            fig = go.Figure()

            # Cumulatief geboekt (realisatie)
            cum_nu = 0.0
            anker_x = vandaag
            if len(rl):
                cum = rl.cumsum()
                cum_nu = float(cum.iloc[-1])
                anker_x = cum.index[-1]
                fig.add_trace(go.Scatter(
                    x=cum.index, y=cum.values, name="Geboekt (cumulatief)",
                    mode="lines+markers", line=dict(color=NAVY2, width=2.5),
                    marker=dict(size=4),
                    hovertemplate="week van %{x|%d-%m-%Y}<br>%{y:.0f} u geboekt<extra></extra>"))
            elif geboekt > 0:
                # Geen weekverdeling beschikbaar: start de vooruitblik op de geboekte stand
                cum_nu = geboekt
                fig.add_trace(go.Scatter(
                    x=[vandaag], y=[geboekt], name="Geboekt tot nu (totaal)",
                    mode="markers", marker=dict(size=9, color=NAVY2, symbol="circle"),
                    hovertemplate="%{y:.0f} u geboekt (totaal, geen weekverdeling)<extra></extra>"))

            # Vooruitblik: resterende vraag cumulatief bovenop de huidige stand
            if len(fwd):
                fwd_cum = cum_nu + fwd.cumsum()
                x_fwd = [anker_x] + list(fwd_cum.index)
                y_fwd = [cum_nu] + list(fwd_cum.values)
                fig.add_trace(go.Scatter(
                    x=x_fwd, y=y_fwd, name="Nog in te plannen (vooruitblik)",
                    mode="lines+markers", line=dict(color=GOLD, width=2.5, dash="dash"),
                    marker=dict(size=4),
                    hovertemplate="week van %{x|%d-%m-%Y}<br>%{y:.0f} u cumulatief<extra></extra>"))

            # Begrotingslijn (getal — veilig voor add_hline)
            if begroot > 0:
                fig.add_hline(y=begroot, line_dash="dot", line_color=NAVY,
                              annotation_text=f"begroting {fmt(begroot)} u",
                              annotation_position="top left",
                              annotation_font=dict(size=10, color=NAVY))
                if verwacht > begroot:
                    fig.add_hrect(y0=begroot, y1=verwacht, fillcolor=RED, opacity=0.10,
                                  line_width=0,
                                  annotation_text=f"+{fmt(verwacht - begroot)} u boven begroting",
                                  annotation_position="bottom right",
                                  annotation_font=dict(size=10, color=RED))

            # 'Vandaag'-lijn via add_shape (NOOIT add_vline met een datum)
            fig.add_shape(type="line", x0=vandaag, x1=vandaag, xref="x", y0=0, y1=1,
                          yref="paper", line=dict(color=GREY, width=1, dash="dot"))
            fig.add_annotation(x=vandaag, xref="x", y=1.0, yref="paper", yanchor="bottom",
                               text="vandaag", showarrow=False,
                               font=dict(size=10, color="#8A8DB0"))

            # Prognose-einddatum als tweede tijdlijn-markering
            if data.heeft("prognose"):
                pr = data.prognose[data.prognose["project_key"] == pk]
                if len(pr):
                    eind = pd.to_datetime(pr.iloc[0].get("prognose_eind"), errors="coerce")
                    if pd.notna(eind):
                        fig.add_shape(type="line", x0=eind, x1=eind, xref="x", y0=0, y1=1,
                                      yref="paper", line=dict(color=AMBER, width=1, dash="dash"))
                        fig.add_annotation(x=eind, xref="x", y=1.0, yref="paper",
                                           yanchor="bottom", text="prognose eind",
                                           showarrow=False, font=dict(size=10, color=AMBER))

            fig.update_layout(**PLOT, height=300)
            fig.update_yaxes(title="cumulatieve uren", gridcolor="#EEF0F7")
            fig.update_xaxes(gridcolor="#EEF0F7")
            st.plotly_chart(fig, width="stretch")

            if len(fwd):
                laatste = fwd.index.max()
                st.caption(f"Het nog in te plannen werk staat uitgezet tot en met de week van "
                           f"{laatste.strftime('%d-%m-%Y')}. "
                           f"{'De verwachte eindstand ligt boven de begroting.' if boven > 0 else 'De verwachte eindstand blijft binnen de begroting.'}")
            elif len(rl):
                st.caption("Er staan voor dit project geen uren meer ingepland; de lijn stopt "
                           "bij de laatste geboekte week.")

    caveat_box(data)
