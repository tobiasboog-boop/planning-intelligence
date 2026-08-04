"""
views.py — De dashboards (building blocks aan de outputkant).

  • Management overzicht   — vraag vs capaciteit, bezetting, knelpunten
  • Team / medewerker      — bezetting per team, beschikbaarheid per medewerker
  • Project-analyse         — calculatie vs werkelijk vs prognose, uitloop
  • AI-adviezen             — geautomatiseerde signalen & aanbevelingen
  • Inrichting              — building blocks aan/uit (het schaalbaarheids-verhaal)

Elke view degradeert netjes als een benodigd building block uit staat.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import engine
from config import (ClientProfile, BLOCKS, VIEWS, NAVY, NAVY2, NAVY_LIGHT,
                    GOLD, GREEN, AMBER, RED, GREY)
from theme import kpi_cards, pill, advice_card, STATUS
from data_gen import DataBundle


def fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def _missing(view_key: str, profile: ClientProfile) -> list[str]:
    v = VIEWS[view_key]
    return [BLOCKS[b].label for b in v.requires if not profile.blocks.get(b)]


def _blocked(view_key: str, profile: ClientProfile) -> bool:
    miss = _missing(view_key, profile)
    if miss:
        st.markdown(
            f'<div class="note">Deze weergave heeft de building block(s) '
            f'<b>{", ".join(miss)}</b> nodig. Activeer die in de zijbalk onder '
            f'<b>Inrichting</b> om dit dashboard te tonen.</div>',
            unsafe_allow_html=True)
        return True
    return False


PLOT_LAYOUT = dict(
    font=dict(family="Segoe UI, sans-serif", size=12, color="#3A3D63"),
    paper_bgcolor="white", plot_bgcolor="white",
    margin=dict(l=10, r=10, t=36, b=10), legend=dict(orientation="h", y=1.12, x=0),
    title=dict(font=dict(size=14, color=NAVY)),
)


# ══════════════════════════════════════════════════════════════════════════
# MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════
def view_management(bundle: DataBundle, profile: ClientProfile):
    st.subheader("Management overzicht")
    if _blocked("management", profile):
        return
    k = engine.headline_kpis(bundle, profile)
    bal = engine.balance(bundle, profile)
    org = engine.org_by_week(bal)

    bez = k.get("bezetting_gem", np.nan)
    bez_cls = "ok" if bez and bez <= profile.target_utilization else ("warn" if bez and bez <= 1.0 else "risk")
    cards = [
        {"lbl": "Benodigde uren", "val": fmt(k.get("vraag_totaal", 0)),
         "sub": f"over {profile.horizon_weeks} weken", "cls": "accent"},
        {"lbl": "Beschikbare capaciteit", "val": fmt(k.get("cap_totaal", 0)),
         "sub": "effectief (na verlof/ziekte × efficiency)"},
        {"lbl": "Gem. bezettingsgraad", "val": f"{bez*100:.0f}%" if bez==bez else "—",
         "sub": f"streef {profile.target_utilization*100:.0f}%", "cls": bez_cls},
        {"lbl": "Tekort (piek)", "val": fmt(k.get("tekort_totaal", 0)),
         "sub": f"in {k.get('piekweken_tekort',0)} weken", "cls": "risk" if k.get("tekort_totaal",0)>0 else "ok"},
        {"lbl": "Projecten met risico", "val": f"{k.get('projecten_risico',0)}",
         "sub": f"van {k.get('n_projecten',0)} projecten", "cls": "risk" if k.get("projecten_risico",0) else "ok"},
    ]
    kpi_cards(cards)

    # ── Vraag vs capaciteit over de tijd ────────────────────────────────────
    st.markdown("###### Vraag vs. beschikbare capaciteit per week")
    x = [w for w in org["week_start"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=org["capaciteit"], name="Capaciteit", mode="lines",
                             line=dict(color=NAVY2, width=0), fill="tozeroy",
                             fillcolor="rgba(54,54,162,0.12)"))
    fig.add_trace(go.Scatter(x=x, y=org["capaciteit"], name="Capaciteit", mode="lines",
                             line=dict(color=NAVY2, width=2), showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=org["benodigde_uren"], name="Benodigde uren",
                             mode="lines+markers", line=dict(color=GOLD, width=3),
                             marker=dict(size=5)))
    # tekort-weken markeren
    tek = org[org["tekort"] > 0]
    if len(tek):
        fig.add_trace(go.Scatter(x=tek["week_start"], y=tek["benodigde_uren"],
                                 name="Tekort", mode="markers",
                                 marker=dict(color=RED, size=9, symbol="triangle-up")))
    fig.update_layout(**PLOT_LAYOUT, height=320)
    fig.update_yaxes(title="uren", gridcolor="#EEF0F7")
    fig.update_xaxes(gridcolor="#F5F6FA")
    st.plotly_chart(fig, width="stretch")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("###### Bezettingsgraad per team per week")
        piv = bal.pivot_table(index="team", columns="week_idx", values="bezetting", aggfunc="mean")
        piv = piv.reindex(columns=range(profile.horizon_weeks))
        colorscale = [[0.0, "#CBD9F5"], [0.5, "#DCE7FB"], [0.607, GREEN],
                      [0.714, "#A7E3B8"], [0.786, AMBER], [1.0, RED]]
        z = piv.values * 100
        fig2 = go.Figure(go.Heatmap(
            z=z, x=[f"w{bundle.weeks[i].isocalendar().week}" for i in piv.columns],
            y=list(piv.index), zmin=0, zmax=140, colorscale=colorscale,
            colorbar=dict(title="%", thickness=10),
            hovertemplate="%{y}<br>%{x}: %{z:.0f}%<extra></extra>"))
        fig2.update_layout(**{**PLOT_LAYOUT, "margin": dict(l=10, r=10, t=10, b=10)}, height=max(240, 40*len(piv)))
        st.plotly_chart(fig2, width="stretch")
    with c2:
        st.markdown("###### Team-bezetting (horizon)")
        ts = engine.team_summary(bal, profile)
        rows = ""
        for _, r in ts.iterrows():
            b = r["bezetting"]
            stt = "goed" if b <= profile.target_utilization else ("let_op" if b <= 1.0 else "risico")
            rows += (f'<tr><td style="padding:6px 8px;font-weight:600;color:{NAVY}">{r["team"]}</td>'
                     f'<td style="padding:6px 8px;text-align:right">{b*100:.0f}%</td>'
                     f'<td style="padding:6px 8px;text-align:right">{fmt(r["tekort"])}</td>'
                     f'<td style="padding:6px 8px">{pill(stt)}</td></tr>')
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
            f'<tr style="color:#8A8DB0;font-size:10.5px;text-transform:uppercase">'
            f'<th style="text-align:left;padding:4px 8px">Team</th>'
            f'<th style="text-align:right;padding:4px 8px">Bezetting</th>'
            f'<th style="text-align:right;padding:4px 8px">Tekort (u)</th>'
            f'<th style="text-align:left;padding:4px 8px">Status</th></tr>{rows}</table>',
            unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# TEAM / MEDEWERKER
# ══════════════════════════════════════════════════════════════════════════
def view_team(bundle: DataBundle, profile: ClientProfile):
    st.subheader("Team / medewerker-analyse")
    if _blocked("team", profile):
        return
    ms = engine.medewerker_summary(bundle, profile)
    teams = sorted(ms["team"].unique())
    show_teams = profile.blocks.get("team_alloc", False)

    c0a, c0b = st.columns([2, 3])
    with c0a:
        team_sel = st.selectbox("Team", ["Alle teams"] + teams if show_teams else ["Alle teams"])
    sub = ms if team_sel == "Alle teams" else ms[ms["team"] == team_sel]

    tot_besch = sub["beschikbaar"].sum()
    tot_afw = sub["afwezig"].sum()
    n_ext = int((sub["intern_extern"] == "Extern").sum())
    kpi_cards([
        {"lbl": "Medewerkers", "val": f"{len(sub)}", "sub": f"{n_ext} extern ingehuurd"},
        {"lbl": "Beschikbare uren", "val": fmt(tot_besch), "sub": f"over {profile.horizon_weeks} weken", "cls": "accent"},
        {"lbl": "Afwezig (verlof/ziek/opl.)", "val": fmt(tot_afw),
         "sub": f"{tot_afw/(tot_besch+tot_afw)*100:.0f}% van bruto" if (tot_besch+tot_afw) else "—", "cls": "warn"},
    ])

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("###### Beschikbaarheid opgebouwd (som per categorie)")
        agg = sub[["verlof", "ziekte", "opleiding", "feestdag", "beschikbaar"]].sum()
        fig = go.Figure(go.Bar(
            x=[agg["beschikbaar"], agg["verlof"], agg["ziekte"], agg["opleiding"], agg["feestdag"]],
            y=["Beschikbaar", "Verlof", "Ziekte", "Opleiding", "Feestdag"],
            orientation="h",
            marker_color=[GREEN, NAVY2, RED, GOLD, NAVY_LIGHT],
            text=[fmt(v) for v in [agg["beschikbaar"], agg["verlof"], agg["ziekte"], agg["opleiding"], agg["feestdag"]]],
            textposition="auto"))
        fig.update_layout(**PLOT_LAYOUT, height=250)
        fig.update_xaxes(title="uren", gridcolor="#EEF0F7")
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("###### Intern vs. extern")
        ie = sub.groupby("intern_extern")["beschikbaar"].sum().reset_index()
        fig = go.Figure(go.Pie(labels=ie["intern_extern"], values=ie["beschikbaar"], hole=0.6,
                               marker_colors=[NAVY, GOLD]))
        fig.update_layout(**{**PLOT_LAYOUT, "showlegend": True}, height=250)
        st.plotly_chart(fig, width="stretch")

    # bezetting per team (alleen bij demand + team_alloc)
    if profile.blocks.get("demand") and show_teams:
        st.markdown("###### Bezetting per team over de horizon")
        bal = engine.balance(bundle, profile)
        ts = engine.team_summary(bal, profile)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=ts["team"], y=ts["capaciteit"], name="Capaciteit", marker_color=NAVY_LIGHT))
        fig.add_trace(go.Bar(x=ts["team"], y=ts["benodigde_uren"], name="Vraag", marker_color=GOLD))
        fig.update_layout(**PLOT_LAYOUT, height=280, barmode="group")
        fig.update_yaxes(title="uren", gridcolor="#EEF0F7")
        st.plotly_chart(fig, width="stretch")

    st.markdown("###### Medewerkers")
    disp = sub[["medewerker", "team", "intern_extern", "contracturen",
                "verlof", "ziekte", "opleiding", "beschikbaar", "beschikbaar_pct"]].copy()
    disp = disp.sort_values("beschikbaar", ascending=False)
    disp.columns = ["Medewerker", "Team", "Type", "Contract/wk", "Verlof",
                    "Ziekte", "Opleiding", "Beschikbaar", "Beschikbaar %"]
    disp["Beschikbaar %"] = (disp["Beschikbaar %"] * 100).round(0)
    st.dataframe(disp, width="stretch", hide_index=True,
                 column_config={"Beschikbaar %": st.column_config.ProgressColumn(
                     "Beschikbaar %", format="%.0f%%", min_value=0, max_value=100)})


# ══════════════════════════════════════════════════════════════════════════
# PROJECT-ANALYSE
# ══════════════════════════════════════════════════════════════════════════
def view_project(bundle: DataBundle, profile: ClientProfile):
    st.subheader("Project-analyse")
    if _blocked("project", profile):
        return
    pa = engine.project_analysis(bundle, profile)
    use_fc = profile.blocks.get("forecast", False)

    n_risk = int((pa["status"] == "risico").sum())
    n_let = int((pa["status"] == "let_op").sum())
    kpi_cards([
        {"lbl": "Projecten", "val": f"{len(pa)}"},
        {"lbl": "Gecalculeerd", "val": fmt(pa["calc_uren"].sum()), "sub": "uren", "cls": "accent"},
        {"lbl": "Verwachte uitloop", "val": fmt(pa["uitloop_uren"].clip(lower=0).sum()),
         "sub": "uren boven calculatie", "cls": "risk" if n_risk else "warn"},
        {"lbl": "Risico / aandacht", "val": f"{n_risk} / {n_let}",
         "sub": "projecten", "cls": "risk" if n_risk else "warn"},
    ])

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("###### Grootste verwachte uitloop (uren)")
        top = pa[pa["uitloop_uren"] > 0].head(10).sort_values("uitloop_uren")
        colmap = {"risico": RED, "let_op": AMBER, "goed": GREEN}
        fig = go.Figure(go.Bar(
            x=top["uitloop_uren"], y=top["project"], orientation="h",
            marker_color=[colmap[s] for s in top["status"]],
            text=[f"+{fmt(v)}" for v in top["uitloop_uren"]], textposition="auto"))
        fig.update_layout(**PLOT_LAYOUT, height=340)
        fig.update_xaxes(title="uren boven calculatie", gridcolor="#EEF0F7")
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("###### Besteed vs. verwachte uitloop")
        colmap = {"risico": RED, "let_op": AMBER, "goed": GREEN}
        fig = go.Figure()
        for s in ["goed", "let_op", "risico"]:
            d = pa[pa["status"] == s]
            fig.add_trace(go.Scatter(
                x=d["besteed_pct"]*100, y=d["uitloop_pct"]*100, mode="markers",
                name=STATUS[s]["label"],
                marker=dict(size=np.sqrt(d["calc_uren"])*1.2, color=colmap[s],
                            line=dict(width=1, color="white"), opacity=0.75),
                text=d["project"],
                hovertemplate="%{text}<br>besteed %{x:.0f}%<br>uitloop %{y:.0f}%<extra></extra>"))
        fig.add_hline(y=0, line_color=GREY)
        fig.add_vline(x=100, line_dash="dot", line_color=GREY)
        fig.update_layout(**PLOT_LAYOUT, height=340)
        fig.update_xaxes(title="% budget besteed", gridcolor="#EEF0F7")
        fig.update_yaxes(title="% verwachte uitloop", gridcolor="#EEF0F7")
        st.plotly_chart(fig, width="stretch")

    st.markdown("###### Projectenlijst")
    tbl = pa.copy()
    cols = ["project", "team", "calc_uren", "werkelijk_uren", "besteed_pct"]
    labels = ["Project", "Team", "Calculatie", "Werkelijk", "Besteed %"]
    if use_fc:
        cols += ["prog_resterend", "prognose_totaal", "uitloop_uren", "vertraging_wk"]
        labels += ["Prognose rest", "Prognose totaal", "Uitloop", "Vertraging (wk)"]
    tbl_disp = tbl[cols].copy()
    tbl_disp["besteed_pct"] = (tbl_disp["besteed_pct"] * 100).round(0)
    tbl_disp["Status"] = tbl["status"].map(lambda s: STATUS[s]["label"])
    tbl_disp.columns = labels + ["Status"]
    st.dataframe(tbl_disp, width="stretch", hide_index=True,
                 column_config={"Besteed %": st.column_config.NumberColumn(format="%d%%")})

    # ── Drilldown ────────────────────────────────────────────────────────────
    if use_fc:
        st.markdown("###### Detail & tijdlijn")
        sel = st.selectbox("Project", pa["project"].tolist())
        r = pa[pa["project"] == sel].iloc[0]
        cc1, cc2 = st.columns([2, 3])
        with cc1:
            st.markdown(
                f'<div style="font-size:13px;line-height:1.9">'
                f'<b>Status:</b> {pill(r["status"])}<br>'
                f'<b>Calculatie:</b> {fmt(r["calc_uren"])} u<br>'
                f'<b>Werkelijk:</b> {fmt(r["werkelijk_uren"])} u ({r["besteed_pct"]*100:.0f}%)<br>'
                f'<b>Prognose resterend:</b> {fmt(r["prog_resterend"])} u<br>'
                f'<b>Prognose totaal:</b> {fmt(r["prognose_totaal"])} u '
                f'(<span style="color:{RED if r["uitloop_uren"]>0 else GREEN}">{"+" if r["uitloop_uren"]>=0 else ""}{fmt(r["uitloop_uren"])} u</span>)<br>'
                f'<b>Vertraging:</b> {int(r["vertraging_wk"])} wk<br>'
                f'<b>Opmerking:</b> <i>{r["opmerking"]}</i></div>',
                unsafe_allow_html=True)
        with cc2:
            tl = pd.DataFrame([
                dict(Fase="Calculatie", Start=r["calc_start"], Eind=r["calc_eind"], kleur="calc"),
                dict(Fase="Prognose", Start=r["calc_start"], Eind=r["prog_eind"], kleur="prog"),
            ])
            fig = px.timeline(tl, x_start="Start", x_end="Eind", y="Fase", color="kleur",
                              color_discrete_map={"calc": NAVY_LIGHT, "prog": GOLD})
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(**{**PLOT_LAYOUT, "showlegend": False}, height=180)
            st.plotly_chart(fig, width="stretch")


# ══════════════════════════════════════════════════════════════════════════
# AI-ADVIEZEN
# ══════════════════════════════════════════════════════════════════════════
def _rule_based_advice(bundle: DataBundle, profile: ClientProfile) -> list[tuple]:
    adv = []
    bal = engine.balance(bundle, profile)
    org = engine.org_by_week(bal)
    ts = engine.team_summary(bal, profile)

    # 1) Piekweken met tekort
    tek = org[org["tekort"] > 0].sort_values("tekort", ascending=False)
    if len(tek):
        wk = tek.iloc[0]
        weeknr = wk["week_start"].isocalendar().week
        adv.append(("risico", f"Capaciteitstekort piekt in week {weeknr}",
                    f"Organisatiebreed komt er in week {weeknr} circa <b>{fmt(wk['tekort'])} uur</b> "
                    f"tekort ({wk['bezetting']*100:.0f}% bezetting). In totaal zijn er "
                    f"<b>{int((org['tekort']>0).sum())} weken</b> met tekort. Overweeg extern in te huren, "
                    f"werk te herplannen of piekprojecten te faseren."))
    # 2) Overbezette teams
    over = ts[ts["bezetting"] > 1.0]
    for _, t in over.head(2).iterrows():
        adv.append(("risico", f"Team {t['team']} is overvraagd",
                    f"Team <b>{t['team']}</b> zit op <b>{t['bezetting']*100:.0f}%</b> bezetting over de horizon "
                    f"(tekort ± {fmt(t['tekort'])} u). Schuif werk naar teams met ruimte of huur gericht in."))
    # 3) Onderbezette teams (kans)
    onder = ts[ts["bezetting"] < 0.75].sort_values("bezetting")
    for _, t in onder.head(2).iterrows():
        adv.append(("goed", f"Team {t['team']} heeft ruimte",
                    f"Team <b>{t['team']}</b> zit op slechts <b>{t['bezetting']*100:.0f}%</b> bezetting. "
                    f"Hier is ruimte om werk van overvraagde teams op te vangen of extra acquisitie in te plannen."))
    # 4) Projecten met uitloop
    if profile.blocks.get("demand"):
        pa = engine.project_analysis(bundle, profile)
        risk = pa[pa["status"] == "risico"].sort_values("uitloop_uren", ascending=False)
        if len(risk):
            top = risk.iloc[0]
            adv.append(("let_op", f"{len(risk)} projecten dreigen budget te overschrijden",
                        f"Grootste: <b>{top['project']}</b> — prognose <b>+{fmt(top['uitloop_uren'])} u</b> "
                        f"boven calculatie ({top['besteed_pct']*100:.0f}% al besteed). "
                        f"Samen goed voor ± {fmt(risk['uitloop_uren'].sum())} u extra. Stuur op scope, "
                        f"meerwerk-facturatie en bemensing."))
    # 5) Extern-afhankelijkheid
    if profile.blocks.get("availability"):
        ms = engine.medewerker_summary(bundle, profile)
        ext = ms[ms["intern_extern"] == "Extern"]["beschikbaar"].sum()
        tot = ms["beschikbaar"].sum()
        if tot and ext / tot > 0.15:
            adv.append(("let_op", "Relatief hoge afhankelijkheid van externe inhuur",
                        f"<b>{ext/tot*100:.0f}%</b> van de beschikbare capaciteit is extern ingehuurd. "
                        f"Prima om pieken op te vangen, maar bewaak marge en kennisborging."))
    if not adv:
        adv.append(("goed", "Planning in balans",
                    "Vraag en capaciteit liggen binnen de streefwaarden en geen enkel project "
                    "vertoont significante uitloop. Geen directe interventie nodig."))
    return adv


def view_ai(bundle: DataBundle, profile: ClientProfile):
    st.subheader("AI-adviezen")
    if _blocked("ai", profile):
        return
    st.markdown(
        f'<div style="font-size:12.5px;color:#8A8DB0;margin-bottom:10px">'
        f'Signalen automatisch afgeleid uit de gecombineerde building blocks. '
        f'In productie schrijft een taalmodel (Claude) hier een bestuurlijke '
        f'samenvatting op basis van dezelfde signalen.</div>', unsafe_allow_html=True)

    for kind, title, body in _rule_based_advice(bundle, profile):
        advice_card(kind, title, body)

    # Optioneel: live LLM-samenvatting als er een sleutel is
    if os.getenv("ANTHROPIC_API_KEY"):
        with st.expander("Bestuurlijke samenvatting genereren (Claude)"):
            if st.button("Genereer samenvatting"):
                _try_llm_summary(bundle, profile)
    else:
        st.caption("💡 Zet ANTHROPIC_API_KEY in .env om hier een live Claude-samenvatting te genereren.")


def _try_llm_summary(bundle, profile):
    try:
        import anthropic
        signals = "\n".join(f"- {t}: {b}" for _, t, b in _rule_based_advice(bundle, profile))
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-opus-4-8", max_tokens=600,
            messages=[{"role": "user", "content":
                       f"Je bent capaciteitsadviseur voor een installatiebedrijf. Schrijf een "
                       f"korte, zakelijke managementsamenvatting (max 6 zinnen, jij-vorm) op basis "
                       f"van deze signalen:\n{signals}"}])
        st.markdown(msg.content[0].text)
    except Exception as e:
        st.warning(f"Kon geen live samenvatting genereren: {e}")


# ══════════════════════════════════════════════════════════════════════════
# INRICHTING (building-block explainer — hét schaalbaarheidsverhaal)
# ══════════════════════════════════════════════════════════════════════════
def view_setup(bundle: DataBundle, profile: ClientProfile):
    st.subheader("Inrichting — building blocks")
    st.markdown(
        f'<div style="font-size:13px;color:#5D6089;margin-bottom:14px">'
        f'De tool is opgebouwd uit losse <b>building blocks</b>. Per klant zet je ze '
        f'aan of uit — geen maatwerk-code. Actief profiel: <b>{profile.name}</b>.</div>',
        unsafe_allow_html=True)

    st.markdown("###### Databronnen (input)")
    for key, blk in BLOCKS.items():
        on = profile.blocks.get(key, False)
        st.markdown(
            f'<div class="bb {"on" if on else "off"}"><div class="h">'
            f'<span class="nm">{"● " if on else "○ "}{blk.label}</span>'
            f'<span class="src">{blk.source}</span></div>'
            f'<div class="d">{blk.description}</div>'
            f'<div class="f">Velden: {" · ".join(blk.fields)}</div></div>',
            unsafe_allow_html=True)

    st.markdown("###### Dashboards (output)")
    cols = st.columns(len(VIEWS))
    for col, (key, v) in zip(cols, VIEWS.items()):
        on = profile.views.get(key, False)
        req = ", ".join(BLOCKS[b].label for b in v.requires)
        col.markdown(
            f'<div class="bb {"on" if on else "off"}" style="min-height:120px">'
            f'<div class="nm">{"● " if on else "○ "}{v.label}</div>'
            f'<div class="f" style="margin-top:6px">Vereist:<br>{req}</div></div>',
            unsafe_allow_html=True)

    st.markdown("###### Instellingen (per klant)")
    kpi_cards([
        {"lbl": "Planningshorizon", "val": f"{profile.horizon_weeks} wk"},
        {"lbl": "Streefbezetting", "val": f"{profile.target_utilization*100:.0f}%"},
        {"lbl": "Default efficiency", "val": f"{profile.default_efficiency*100:.0f}%",
         "sub": "als teams-block uit staat"},
        {"lbl": "Databron (prototype)", "val": "Synthetisch", "sub": "→ Syntess / U-Serve in productie"},
    ])
