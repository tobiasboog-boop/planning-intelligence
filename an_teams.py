"""
an_teams.py — Analyse "Teambezetting".

Wie is er beschikbaar, in welk team, intern of ingeleend — en wat de seizoenscorrectie
met die beschikbaarheid doet. Werkt uitsluitend op het canonieke contract
(contract.PlanningData): frames `capaciteit` en `medewerkers`.

Eerlijk in de labels:
  • Capaciteit is BRUTO contracturen, tenzij de seizoenscorrectie aan staat.
  • De contracturen per medewerker komen uit de capaciteitskalender van de bron en
    zijn NIET begrensd tot de gekozen horizon (de bron levert per medewerker geen
    week-dimensie). Alles wat wél horizon-gebonden is, komt uit `capaciteit`.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from config import NAVY, NAVY2, NAVY_LIGHT, GOLD, ClientProfile
from contract import PlanningData
from theme import kpi_cards
from an_common import PLOT, fmt, pct, guard, caveat_box, capaciteit_kolom

ALLE = "(alle)"


# ── helpers ───────────────────────────────────────────────────────────────────
def _aantal(df: pd.DataFrame) -> int:
    """Aantal unieke medewerkers (valt terug op rij-aantal)."""
    if not len(df):
        return 0
    if "mdw_key" in df.columns:
        return int(df["mdw_key"].nunique())
    return int(len(df))


def _aantal_per_team(df: pd.DataFrame) -> pd.Series:
    """Aantal unieke medewerkers per team (teamnaam als string-index)."""
    if not len(df):
        return pd.Series(dtype=float)
    key = df["team"].fillna("(onbekend)").astype(str)
    if "mdw_key" in df.columns:
        return df.groupby(key)["mdw_key"].nunique()
    return df.groupby(key).size()


def _weeklabel(ts) -> str:
    d = pd.Timestamp(ts)
    return f"wk {d.isocalendar().week}"


# ── analyse ───────────────────────────────────────────────────────────────────
def render(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    if guard(data, "capaciteit", "medewerkers"):
        return

    seizoen = bool(opts.get("seizoen", False))
    eff_aan = bool(opts.get("efficiency", False))
    eff_pct = int(opts.get("efficiency_pct", profile.default_efficiency))
    eff_factor = eff_pct / 100.0 if eff_aan else 1.0
    horizon = int(opts.get("horizon", profile.horizon_weken))

    capkol = capaciteit_kolom(data, seizoen)
    seizoen_actief = seizoen and capkol == "beschikbaar_uren"

    st.subheader("Teambezetting")
    st.caption(
        "De bemensing achter de planning: hoeveel mensen per team, hoeveel daarvan is "
        "ingeleend en hoeveel uren die ploeg in de gekozen horizon op papier heeft. "
        + ("Capaciteit is seizoensgecorrigeerd (verlof, ziekte, feestdagen)."
           if seizoen_actief else "Capaciteit is bruto: verlof, ziekte en feestdagen zitten er nog in.")
    )

    # ── week-as + capaciteit binnen de horizon ────────────────────────────────
    weken = data.weken(horizon)
    cap = data.capaciteit.copy()
    cap["week_start"] = pd.to_datetime(cap["week_start"])
    cap = cap[cap["week_start"].isin(weken)]
    for kol in ("contract_uren", "beschikbaar_uren"):
        if kol in cap.columns:
            cap[kol] = pd.to_numeric(cap[kol], errors="coerce").fillna(0.0)

    mdw = data.medewerkers.copy()
    if "contract_uren" in mdw.columns:
        mdw["contract_uren"] = pd.to_numeric(mdw["contract_uren"], errors="coerce").fillna(0.0)
    else:
        mdw["contract_uren"] = 0.0
    if "type" not in mdw.columns:
        mdw["type"] = "Intern"
    if "team" not in mdw.columns:
        mdw["team"] = "(onbekend)"

    # ── 1. filters ────────────────────────────────────────────────────────────
    f1, f2 = st.columns([2, 2])
    with f1:
        alleen_pp = st.toggle(
            "Alleen medewerkers in de projectplanning", value=True, key="teams_alleen_pp",
            help="Filtert op de planningsvlag uit de bron: alleen mensen die op projecten "
                 "gepland worden. Zet uit om de volledige personeelslijst te zien.")
    teams_beschikbaar = sorted(
        set(cap["team"].dropna().astype(str)) | set(mdw["team"].dropna().astype(str)))
    with f2:
        team_keuze = st.selectbox("Team", [ALLE] + teams_beschikbaar, index=0, key="teams_team")

    m = mdw
    if alleen_pp and "in_planning" in m.columns:
        gefilterd = m[m["in_planning"].fillna(False).astype(bool)]
        if len(gefilterd):
            m = gefilterd
        else:
            st.markdown(
                '<div class="note">De planningsvlag is bij deze bron voor niemand gezet — '
                'daarom zie je hieronder alle medewerkers.</div>', unsafe_allow_html=True)

    if team_keuze != ALLE:
        m = m[m["team"].astype(str) == team_keuze]
        cap = cap[cap["team"].astype(str) == team_keuze]

    n_mw = _aantal(m)
    n_ext = _aantal(m[m["type"] == "Extern"])
    ext_aandeel = (n_ext / n_mw * 100) if n_mw else np.nan

    bruto_u = float(cap["contract_uren"].sum()) if len(cap) else 0.0
    besch_u = float(cap[capkol].sum()) if len(cap) else 0.0
    effectief_u = besch_u * eff_factor
    verlies_u = max(0.0, bruto_u - besch_u)
    verlies_pct = (verlies_u / bruto_u * 100) if bruto_u > 0 else np.nan

    # ── 2. KPI-rij ────────────────────────────────────────────────────────────
    ploeg_sub = ("in de projectplanning" if alleen_pp else "volledige personeelslijst")
    if team_keuze != ALLE:
        ploeg_sub += f" — {team_keuze}"
    cards = [
        {"lbl": "Medewerkers", "val": fmt(n_mw), "sub": ploeg_sub},
        {"lbl": "Ingeleend (extern)", "val": fmt(n_ext),
         "sub": f"{pct(ext_aandeel)} van de ploeg",
         "cls": "warn" if (n_mw and ext_aandeel > 30) else ""},
        {"lbl": "Contractcapaciteit", "val": fmt(bruto_u),
         "sub": f"bruto uren, {horizon} weken", "cls": "accent"},
    ]
    if seizoen_actief:
        sub_eff = "na verlof, ziekte, feestdagen"
        if eff_aan:
            sub_eff += f" en {eff_pct}% efficiency"
        cards.append({"lbl": "Effectief beschikbaar", "val": fmt(effectief_u), "sub": sub_eff})
        cards.append({"lbl": "Verlies door seizoen", "val": fmt(verlies_u),
                      "sub": f"{pct(verlies_pct)} van de contracturen", "cls": "warn"})
    elif eff_aan:
        cards.append({"lbl": "Productief inzetbaar", "val": fmt(effectief_u),
                      "sub": f"{eff_pct}% efficiency op de contracturen"})
    kpi_cards(cards)

    if not len(cap):
        st.markdown(
            '<div class="note">Voor deze selectie staan er geen capaciteitsweken in de '
            'horizon. Kies een ander team of vergroot de horizon.</div>',
            unsafe_allow_html=True)

    # ── 3/4. capaciteit per team + intern/extern ──────────────────────────────
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("###### Contractcapaciteit per team (horizon)")
        if len(cap):
            per_team = cap.groupby("team", dropna=False).agg(
                contract=("contract_uren", "sum")).reset_index()
            if seizoen_actief:
                besch = cap.groupby("team", dropna=False)[capkol].sum()
                per_team["effectief"] = per_team["team"].map(besch).fillna(0.0) * eff_factor
            per_team["team"] = per_team["team"].fillna("(onbekend)").astype(str)
            per_team = per_team.sort_values("contract", ascending=True).tail(14)

            cnt = _aantal_per_team(m)
            ext = _aantal_per_team(m[m["type"] == "Extern"])
            cd = np.column_stack([
                per_team["team"].map(cnt).fillna(0).to_numpy(dtype=float),
                per_team["team"].map(ext).fillna(0).to_numpy(dtype=float),
            ])

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=per_team["contract"], y=per_team["team"], orientation="h",
                name="Contracturen (bruto)", marker_color=NAVY2,
                text=[fmt(v) for v in per_team["contract"]], textposition="auto",
                customdata=cd,
                hovertemplate="<b>%{y}</b><br>%{x:.0f} contracturen (bruto)<br>"
                              "%{customdata[0]:.0f} medewerkers, waarvan %{customdata[1]:.0f} extern"
                              "<extra></extra>"))
            if seizoen_actief:
                fig.add_trace(go.Bar(
                    x=per_team["effectief"], y=per_team["team"], orientation="h",
                    name="Effectief beschikbaar", marker_color=NAVY_LIGHT,
                    hovertemplate="<b>%{y}</b><br>%{x:.0f} uren effectief beschikbaar<extra></extra>"))
            fig.update_layout(**{**PLOT, "showlegend": seizoen_actief},
                              height=max(300, 42 * len(per_team) + 90), barmode="group",
                              bargap=0.25, bargroupgap=0.05)
            fig.update_xaxes(title="uren", gridcolor="#EEF0F7")
            fig.update_yaxes(tickfont=dict(size=10))
            st.plotly_chart(fig, width="stretch")
            st.caption("Uren uit de capaciteitskalender van de bron, opgeteld over de horizon. "
                       "Hover voor het aantal mensen per team.")
        else:
            st.caption("Geen capaciteitsuren in deze selectie.")

    with c2:
        st.markdown("###### Intern vs. ingeleend")
        if len(m) and m["contract_uren"].sum() > 0:
            labels = ["Intern", "Extern"]
            waarden = (m.groupby("type")["contract_uren"].sum()
                       .reindex(labels).fillna(0.0))
            aantallen = [_aantal(m[m["type"] == t]) for t in labels]
            fig = go.Figure(go.Pie(
                labels=labels, values=waarden.to_list(), hole=0.62,
                marker_colors=[NAVY, GOLD], sort=False, direction="clockwise",
                textinfo="percent",
                hovertext=[f"{a} medewerkers" for a in aantallen],
                hovertemplate="<b>%{label}</b><br>%{value:.0f} contracturen (%{percent})"
                              "<br>%{hovertext}<extra></extra>"))
            fig.add_annotation(text=f"<b>{pct(ext_aandeel)}</b><br>extern", x=0.5, y=0.5,
                               showarrow=False, font=dict(size=15, color=NAVY))
            fig.update_layout(**{**PLOT, "showlegend": True}, height=340)
            st.plotly_chart(fig, width="stretch")
            st.caption("Aandeel in contracturen. Een hoog extern-aandeel maakt je capaciteit "
                       "flexibel, maar ook duurder en minder voorspelbaar.")
        else:
            st.caption("Geen contracturen per medewerker in deze selectie.")

    # ── 5. beschikbaarheidsfactor per week (zomerdip) ─────────────────────────
    if seizoen_actief and "season_factor" in data.capaciteit.columns and len(cap):
        st.markdown("###### Beschikbaarheid per week (seizoenscorrectie)")
        sf = (cap.assign(season_factor=pd.to_numeric(cap["season_factor"], errors="coerce"))
              .groupby("week_start")["season_factor"].mean()
              .reindex(weken).dropna())
        if len(sf):
            waarden = (sf * 100).to_numpy(dtype=float)
            gem = float(np.nanmean(waarden))
            kleuren = [GOLD if v < gem - 5 else NAVY2 for v in waarden]
            labels = [_weeklabel(d) for d in sf.index]
            fig = go.Figure(go.Bar(
                x=labels, y=waarden, marker_color=kleuren,
                customdata=[d.strftime("%d-%m-%Y") for d in sf.index],
                hovertemplate="week van %{customdata}<br>%{y:.0f}% van de contracturen "
                              "beschikbaar<extra></extra>"))
            fig.add_hline(y=gem, line=dict(color=NAVY_LIGHT, width=1.5, dash="dash"),
                          annotation_text=f"gemiddeld {gem:.0f}%",
                          annotation_position="top left",
                          annotation_font=dict(size=11, color=NAVY))
            fig.update_layout(**PLOT, height=290, bargap=0.25)
            fig.update_yaxes(title="% beschikbaar", gridcolor="#EEF0F7", range=[0, 100])
            fig.update_xaxes(tickfont=dict(size=9))
            st.plotly_chart(fig, width="stretch")
            laagste = sf.idxmin()
            st.caption(
                f"Goud = weken die meer dan 5 procentpunt onder het gemiddelde liggen; "
                f"daar loopt je planning het snelst vast. Laagste week: "
                f"{_weeklabel(laagste)} ({laagste.strftime('%d-%m-%Y')}) met "
                f"{pct(float(sf.loc[laagste]) * 100)} beschikbaar.")
            if data.meta.seizoen_uitleg:
                st.caption(data.meta.seizoen_uitleg)

    # ── 6. medewerkerstabel ───────────────────────────────────────────────────
    st.markdown("###### Medewerkers")
    if len(m):
        disp = (m[["medewerker", "team", "type", "contract_uren"]]
                .sort_values("contract_uren", ascending=False).copy())
        disp["team"] = disp["team"].fillna("(onbekend)").astype(str)
        disp.columns = ["Medewerker", "Team", "Type", "Contracturen"]
        st.dataframe(
            disp, width="stretch", hide_index=True,
            column_config={"Contracturen": st.column_config.NumberColumn(
                "Contracturen", format="%.0f",
                help="Contracturen per medewerker over de volledige periode in de "
                     "capaciteitskalender van de bron — niet begrensd tot de horizon.")})
        st.caption(f"{fmt(n_mw)} medewerkers, waarvan {fmt(n_ext)} ingeleend. De contracturen "
                   f"per medewerker gelden over de hele bronperiode; de KPI's en de grafieken "
                   f"hierboven zijn wél begrensd tot de horizon van {horizon} weken.")
    else:
        st.caption("Geen medewerkers in deze selectie.")

    caveat_box(data)
