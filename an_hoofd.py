"""
an_hoofd.py — Het hoofdscherm: "Kun je het werk aan?"

Eén vraag, één antwoord. Bovenaan een conclusie in gewone taal, daaronder één grafiek
en de drie grootste knelpunten. De diepte (heatmap per team, bemensing, signalen) zit
achter een klik — niet in beeld tenzij je erom vraagt.

Dit vervangt de vijf losse tabs als klantweergave; de uitgebreide analyses blijven
beschikbaar in de configuratiemodus.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import (ClientProfile, NAVY, NAVY2, NAVY_LIGHT, GOLD, GREEN, AMBER, RED, GREY)
from contract import PlanningData
from an_common import (fmt, pct, guard, PLOT, per_week, beslag_kleur, beslag_status,
                       caveat_box, capaciteit_kolom)
from theme import kpi_cards, pill
import seasonality as sn

MND = {1: "januari", 2: "februari", 3: "maart", 4: "april", 5: "mei", 6: "juni",
       7: "juli", 8: "augustus", 9: "september", 10: "oktober", 11: "november",
       12: "december"}


def _wk(ts) -> str:
    ts = pd.Timestamp(ts)
    return f"week {ts.isocalendar().week}"


def _venster(data: PlanningData, horizon: int) -> pd.DatetimeIndex:
    """Week-as die bij de huidige week begint (een balans die in het verleden start
    is niet planbaar)."""
    weken = data.weken(horizon)
    vandaag = pd.Timestamp.today().normalize()
    maandag = vandaag - pd.Timedelta(days=vandaag.weekday())
    if len(weken) and weken[0] < maandag <= weken[-1]:
        return pd.date_range(start=maandag, periods=horizon, freq="7D")
    return weken


def _basis(data: PlanningData, profile: ClientProfile, opts: dict):
    """Vraag, capaciteit en beslag per week over de betrokken teams."""
    weken = _venster(data, opts.get("horizon", profile.horizon_weken))
    vraag, cap = data.vraag.copy(), data.capaciteit.copy()

    # alleen capaciteit van teams die daadwerkelijk openstaand werk hebben
    teams_met_vraag = set(vraag["team"].dropna().unique()) if len(vraag) else set()
    cap_rel = cap[cap["team"].isin(teams_met_vraag)] if teams_met_vraag else cap.iloc[0:0]
    if not len(cap_rel):
        cap_rel = cap

    kolom = capaciteit_kolom(data, opts.get("seizoen", False))
    eff = (opts.get("efficiency_pct", 100) / 100) if opts.get("efficiency") else 1.0

    v_w = per_week(vraag, "uren", weken)
    c_w = per_week(cap_rel, kolom, weken) * eff
    bruto_w = per_week(cap_rel, "contract_uren", weken) * eff
    beslag = (v_w / c_w.replace(0, np.nan) * 100)
    return weken, v_w, c_w, bruto_w, beslag, cap_rel, kolom


def _tempo_antwoord(data, opts, weken, c_w):
    """Conclusie op basis van het WERKELIJKE tempo.

    Bij Megens wordt vooruit nauwelijks gepland; dan is de planning geen maat voor
    de vrije ruimte. Wat er per week doorheen gaat, is dat wel.
    """
    t = data.tempo_per_week()
    if not t:
        return None
    cap_wk = float(c_w.mean()) if len(c_w) else 0.0
    bezet = t["totaal"]
    vrij = cap_wk - bezet
    openstaand = float(data.vraag["uren"].sum()) if len(data.vraag) else 0.0
    proj_tempo = t["projecturen"]

    # Hoe lang duurt het openstaande werk bij het huidige projecttempo?
    weken_tempo = (openstaand / proj_tempo) if proj_tempo > 0 else float("nan")
    # En hoe lang als je alleen de vrije ruimte inzet?
    weken_vrij = (openstaand / vrij) if vrij > 0 else float("inf")

    label_cap = "effectief beschikbaar" if opts.get("seizoen") else "bruto contract"
    basis = (f"Gemeten over {t['weken']} volledige weken "
             f"({pd.Timestamp(t['van']).strftime('%d-%m')} t/m "
             f"{pd.Timestamp(t['tot']).strftime('%d-%m-%Y')}).")

    if vrij <= 0:
        return ("risico",
                f"Nee &mdash; je zit al vol. Er gaat <b>{fmt(bezet)} uur per week</b> doorheen "
                f"terwijl er <b>{fmt(cap_wk)} uur</b> {label_cap} is.",
                f"Het openstaande werk van <b>{fmt(openstaand)} uur</b> kan er alleen bij als er "
                f"iets anders af gaat, of met extra mensen. {basis}", t, cap_wk, bezet, vrij,
                openstaand, weken_tempo)

    kind = "risico" if weken_vrij > 26 else ("let_op" if weken_vrij > 13 else "goed")
    kop = (f"Het openstaande werk van <b>{fmt(openstaand)} uur</b> kost bij je huidige tempo "
           f"ongeveer <b>{weken_tempo:.0f} weken</b> &mdash; met alleen je vrije ruimte "
           f"<b>{weken_vrij:.0f} weken</b>.")
    inleen = float(data.tempo["uren_buiten_populatie"].median()) if \
        "uren_buiten_populatie" in data.tempo.columns else 0.0
    extra = ""
    if inleen > 0:
        extra = (f" Daarnaast wordt er <b>{fmt(inleen)} uur per week</b> geboekt door mensen "
                 f"zonder contracturen &mdash; ingeleende capaciteit buiten deze telling.")
    toe = (f"Je eigen ploeg draait nu <b>{fmt(proj_tempo)} projecturen</b> plus "
           f"<b>{fmt(t['indirecte_uren'])} indirecte uren</b> per week, samen {fmt(bezet)} van de "
           f"{fmt(cap_wk)} uur {label_cap}. Dat laat <b>{fmt(vrij)} uur per week</b> echt vrij."
           f"{extra} {basis}")
    return (kind, kop, toe, t, cap_wk, bezet, vrij, openstaand, weken_tempo)


def _antwoord(data, profile, opts, weken, v_w, c_w, beslag, cap_rel, kolom):
    """De conclusie in gewone taal: past het werk in de bemensing, en waar niet?"""
    eff = (opts.get("efficiency_pct", 100) / 100) if opts.get("efficiency") else 1.0
    krap = beslag[beslag > 100].dropna()

    # knelpunt per team per week (voor de 'waar' in het antwoord)
    knel = pd.DataFrame()
    if len(data.vraag) and len(cap_rel):
        dm = data.vraag.copy(); dm["week_start"] = pd.to_datetime(dm["week_start"])
        cm = cap_rel.copy(); cm["week_start"] = pd.to_datetime(cm["week_start"])
        dm = dm[dm["week_start"].isin(weken)]; cm = cm[cm["week_start"].isin(weken)]
        d = dm.groupby(["team", "week_start"])["uren"].sum()
        c = cm.groupby(["team", "week_start"])[kolom].sum() * eff
        knel = pd.concat([d.rename("vraag"), c.rename("cap")], axis=1).dropna()
        knel["beslag"] = knel["vraag"] / knel["cap"].replace(0, np.nan) * 100
        knel["tekort"] = (knel["vraag"] - knel["cap"]).clip(lower=0)

    if len(krap):
        piek = krap.idxmax()
        tekort_piek = float((v_w - c_w).loc[piek])
        maand = MND[pd.Timestamp(piek).month]
        # welk team knelt daar het hardst?
        team_txt = ""
        if len(knel):
            wk_knel = knel[knel.index.get_level_values("week_start") == piek]
            if len(wk_knel):
                top = wk_knel["tekort"].idxmax()
                team_txt = f" bij <b>{top[0]}</b>"
        seiz = ""
        if opts.get("seizoen"):
            f = sn.week_factoren([piek], sn.SeasonParams(
                vakantiedagen=profile.vakantiedagen, adv_dagen=profile.adv_dagen,
                ziekte_pct=profile.ziekte_pct / 100, opleiding_pct=profile.opleiding_pct / 100,
                uren_per_dag=profile.uren_per_dag))
            seiz = (f" — in {maand} is maar <b>{f.iloc[0]*100:.0f}%</b> van je mensen "
                    f"beschikbaar door verlof en feestdagen")
        return ("risico",
                f"Nee — in {_wk(piek)} kom je circa <b>{fmt(tekort_piek)} uur</b> tekort"
                f"{team_txt}{seiz}.",
                f"In totaal zijn er <b>{len(krap)}</b> {'week' if len(krap)==1 else 'weken'} "
                f"waarin het openstaande werk niet in de bemensing past.")

    hoog = beslag[(beslag > opts.get("streef", 90))].dropna()
    gem = (v_w.sum() / c_w.sum() * 100) if c_w.sum() else float("nan")
    if len(hoog):
        piek = hoog.idxmax()
        return ("let_op",
                f"Krap — in {_wk(piek)} zit je op <b>{hoog.max():.0f}%</b> van je capaciteit.",
                f"Gemiddeld is het beslag <b>{pct(gem)}</b>. Het past, maar er is weinig ruimte "
                f"voor uitloop of extra werk in {len(hoog)} "
                f"{'week' if len(hoog)==1 else 'weken'}.")
    return ("goed",
            f"Ja — het openstaande werk past in je bemensing.",
            f"Het beslag op de capaciteit van de betrokken teams is gemiddeld <b>{pct(gem)}</b> "
            f"over de komende {len(weken)} weken.")


def _antwoordblok(kind: str, kop: str, toelichting: str) -> None:
    kleur = {"risico": RED, "let_op": AMBER, "goed": GREEN}[kind]
    bg = {"risico": "#FEF2F2", "let_op": "#FFFBEB", "goed": "#ECFDF5"}[kind]
    st.markdown(
        f'<div style="background:{bg};border-left:5px solid {kleur};border-radius:0 14px 14px 0;'
        f'padding:20px 24px;margin:2px 0 18px">'
        f'<div style="font-size:20px;font-weight:750;color:{NAVY};line-height:1.35">{kop}</div>'
        f'<div style="font-size:14px;color:#4A4D74;margin-top:8px;line-height:1.55">{toelichting}</div>'
        f'</div>', unsafe_allow_html=True)


def render(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    if guard(data, "vraag", "capaciteit"):
        return

    weken, v_w, c_w, bruto_w, beslag, cap_rel, kolom = _basis(data, profile, opts)

    # Realisatietempo is de eerlijkste maat zodra vooruit nauwelijks gepland wordt.
    tempo_res = _tempo_antwoord(data, opts, weken, c_w)
    if tempo_res:
        kind, kop, toe, t, cap_wk, bezet, vrij, openstaand, weken_tempo = tempo_res
        _antwoordblok(kind, kop, toe)

        kpi_cards([
            {"lbl": "Openstaand werk", "val": fmt(openstaand), "sub": "uren nog te plannen",
             "cls": "accent"},
            {"lbl": "Tempo projecturen", "val": fmt(t["projecturen"]), "sub": "per week (mediaan)"},
            {"lbl": "Indirect", "val": fmt(t["indirecte_uren"]), "sub": "per week"},
            {"lbl": "Vrije ruimte", "val": fmt(vrij), "sub": "uren per week",
             "cls": "risk" if vrij <= 0 else ("warn" if vrij < 0.15 * cap_wk else "ok")},
            {"lbl": "Doorlooptijd", "val": f"{weken_tempo:.0f} wk" if weken_tempo == weken_tempo else "—",
             "sub": "bij huidig tempo",
             "cls": "risk" if weken_tempo > 26 else ("warn" if weken_tempo > 13 else "ok")},
        ])
        if "uren_buiten_populatie" in data.tempo.columns:
            inleen = float(data.tempo["uren_buiten_populatie"].median())
            if inleen > 0:
                st.caption(f"Let op: hiernaast wordt er nog {fmt(inleen)} uur per week geboekt "
                           f"door mensen zonder contracturen in Syntess (ingeleend of niet "
                           f"vastgelegd). Die capaciteit zit niet in de cijfers hierboven.")

        st.markdown("###### Waar je capaciteit nu aan opgaat")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=["Per week"], y=[t["projecturen"]], name="Projecturen (lopend)",
                             marker_color=NAVY2, width=[0.42]))
        fig.add_trace(go.Bar(x=["Per week"], y=[t["indirecte_uren"]], name="Indirecte uren",
                             marker_color=NAVY_LIGHT, width=[0.42]))
        fig.add_trace(go.Bar(x=["Per week"], y=[max(0.0, vrij)], name="Vrije ruimte",
                             marker_color=GREEN if vrij > 0 else RED, width=[0.42]))
        fig.add_hline(y=cap_wk, line_dash="dot", line_color=NAVY,
                      annotation_text=("effectief beschikbaar" if opts.get("seizoen")
                                       else "bruto contracturen"),
                      annotation_position="top left")
        fig.update_layout(**PLOT, height=300, barmode="stack")
        fig.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
        st.plotly_chart(fig, width="stretch")
        st.caption("Het lopende tempo is gemeten op de geboekte uren, niet op de planning — "
                   "vooruit wordt er in Syntess nauwelijks vastgelegd.")

        with st.expander("Wat staat er wél vooruit gepland?"):
            try:
                import megens_source as _ms
                wb = _ms.fetch_werkbon_planning(_ms.get_client())
            except Exception:
                wb = pd.DataFrame()
            if len(wb):
                wb = wb.sort_values("week_start").head(14)
                figw = go.Figure()
                figw.add_trace(go.Bar(x=wb["week_start"], y=wb["voorbereide_uren"],
                                      name="Voorbereide uren", marker_color=GOLD))
                figw.add_trace(go.Scatter(x=wb["week_start"], y=wb["werkbonnen"],
                                          name="Aantal werkbonnen", yaxis="y2",
                                          line=dict(color=NAVY2, width=2)))
                figw.update_layout(**PLOT, height=280,
                                   yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                               title="werkbonnen"))
                figw.update_yaxes(title="uren", gridcolor="#EEF0F7")
                st.plotly_chart(figw, width="stretch")
                st.caption(f"Werkbonnen met een afspraakdatum vooruit: "
                           f"{int(wb['werkbonnen'].sum())} stuks, samen "
                           f"{fmt(wb['voorbereide_uren'].sum())} voorbereide uren. "
                           f"Dat is een fractie van de {fmt(t['totaal'])} uur die er per week "
                           f"werkelijk doorheen gaat — de rest wordt niet vooraf gepland.")
            else:
                st.caption("Geen vooruit ingeplande werkbonnen gevonden.")

        _detail_blokken(data, profile, opts, weken, cap_rel, kolom)
        caveat_box(data)
        return

    kind, kop, toelichting = _antwoord(data, profile, opts, weken, v_w, c_w, beslag,
                                       cap_rel, kolom)

    # ── het antwoord ─────────────────────────────────────────────────────────
    _antwoordblok(kind, kop, toelichting)

    # ── de grafiek ───────────────────────────────────────────────────────────
    label_cap = ("Beschikbaar (na verlof, ziekte en feestdagen)" if opts.get("seizoen")
                 else "Beschikbaar (bruto contracturen)")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=list(weken), y=c_w.values, name=label_cap, mode="lines",
                             line=dict(color=NAVY2, width=2.5),
                             fill="tozeroy", fillcolor="rgba(54,54,162,0.09)"))
    if opts.get("seizoen"):
        fig.add_trace(go.Scatter(x=list(weken), y=bruto_w.values, name="Bruto contracturen",
                                 mode="lines", line=dict(color=NAVY_LIGHT, width=1.5, dash="dot")))
    fig.add_trace(go.Bar(x=list(weken), y=v_w.values, name="Nog in te plannen werk",
                         marker_color=[beslag_kleur(b) for b in beslag.fillna(0)],
                         opacity=0.95, customdata=beslag.fillna(0).values,
                         hovertemplate="%{x|%d-%m-%Y}<br>%{y:.0f} uur werk<br>"
                                       "beslag: %{customdata:.0f}%<extra></extra>"))
    fig.update_layout(**PLOT, height=340, barmode="overlay")
    fig.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Alleen de {cap_rel['team'].nunique()} afdeling(en) met openstaand werk — "
               f"niet de hele organisatie. Rode staven: meer werk dan capaciteit.")

    _detail_blokken(data, profile, opts, weken, cap_rel, kolom)
    caveat_box(data)

def _detail_blokken(data: PlanningData, profile: ClientProfile, opts: dict,
                    weken, cap_rel, kolom) -> None:
    """Knelpunten, heatmap per afdeling en bemensing — gedeeld door beide weergaven."""
    # ── de drie grootste knelpunten ──────────────────────────────────────────
    st.markdown("###### Waar het knelt")
    eff = (opts.get("efficiency_pct", 100) / 100) if opts.get("efficiency") else 1.0
    dm = data.vraag.copy(); dm["week_start"] = pd.to_datetime(dm["week_start"])
    cm = cap_rel.copy(); cm["week_start"] = pd.to_datetime(cm["week_start"])
    dm = dm[dm["week_start"].isin(weken)]; cm = cm[cm["week_start"].isin(weken)]
    if len(dm) and len(cm):
        d = dm.groupby(["team", "week_start"])["uren"].sum()
        c = cm.groupby(["team", "week_start"])[kolom].sum() * eff
        kn = pd.concat([d.rename("vraag"), c.rename("cap")], axis=1).dropna()
        kn["beslag"] = kn["vraag"] / kn["cap"].replace(0, np.nan) * 100
        kn["tekort"] = (kn["vraag"] - kn["cap"]).clip(lower=0)
        top = kn.nlargest(3, "tekort")
        top = top[top["tekort"] > 0]
        if len(top):
            rijen = ""
            for (team, wk), r in top.iterrows():
                rijen += (
                    f'<tr><td style="padding:9px 10px;font-weight:650;color:{NAVY}">{team}</td>'
                    f'<td style="padding:9px 10px">{_wk(wk)}</td>'
                    f'<td style="padding:9px 10px;text-align:right;font-weight:650;color:{RED}">'
                    f'{fmt(r["tekort"])} u tekort</td>'
                    f'<td style="padding:9px 10px;text-align:right">{r["beslag"]:.0f}%</td>'
                    f'<td style="padding:9px 10px">{pill(beslag_status(r["beslag"]))}</td></tr>')
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<tr style="color:#8A8DB0;font-size:10.5px;text-transform:uppercase;'
                f'letter-spacing:.5px"><th style="text-align:left;padding:5px 10px">Afdeling</th>'
                f'<th style="text-align:left;padding:5px 10px">Wanneer</th>'
                f'<th style="text-align:right;padding:5px 10px">Tekort</th>'
                f'<th style="text-align:right;padding:5px 10px">Beslag</th>'
                f'<th style="text-align:left;padding:5px 10px">Status</th></tr>{rijen}</table>',
                unsafe_allow_html=True)
        else:
            st.caption("Geen weken waarin het werk de capaciteit overschrijdt.")

        # ── diepte achter een klik ───────────────────────────────────────────
        with st.expander("Alle afdelingen per week bekijken"):
            hm = (kn["beslag"].unstack().reindex(columns=weken).dropna(how="all").sort_index())
            if len(hm):
                figh = go.Figure(go.Heatmap(
                    z=hm.values, x=[f"wk {pd.Timestamp(w).isocalendar().week}" for w in hm.columns],
                    y=list(hm.index), zmin=0, zmax=120,
                    colorscale=[[0.0, "#EEF0FB"], [0.45, NAVY_LIGHT], [0.7, NAVY2],
                                [0.83, AMBER], [1.0, RED]],
                    colorbar=dict(title="%", thickness=10),
                    hovertemplate="%{y}<br>%{x}: %{z:.0f}% beslag<extra></extra>"))
                figh.update_layout(**{**PLOT, "margin": dict(l=10, r=10, t=10, b=10)},
                                   height=max(200, 42 * len(hm)))
                figh.update_yaxes(tickfont=dict(size=10))
                st.plotly_chart(figh, width="stretch")
                st.caption("Beslag op de capaciteit per afdeling per week. Rood = meer werk dan mensen.")

    # ── bemensing achter een klik ────────────────────────────────────────────
    if data.heeft("medewerkers"):
        with st.expander("Wie er beschikbaar is"):
            m = data.medewerkers
            mp = m[m["in_planning"] == True] if "in_planning" in m.columns else m  # noqa: E712
            if not len(mp):
                mp = m
            n_ext = int((mp["type"] == "Extern").sum())
            kaarten = [
                {"lbl": "Medewerkers", "val": f"{len(mp)}", "sub": "in de planning"},
                {"lbl": "Ingeleend", "val": f"{n_ext}",
                 "sub": pct(n_ext / len(mp) * 100) + " van de ploeg" if len(mp) else "—",
                 "cls": "warn" if len(mp) and n_ext / len(mp) > 0.3 else ""},
                {"lbl": "Contracturen", "val": fmt(mp["contract_uren"].sum()),
                 "sub": "bruto over de horizon", "cls": "accent"},
            ]
            if opts.get("seizoen") and "beschikbaar_uren" in data.capaciteit.columns:
                bruto = data.capaciteit["contract_uren"].sum()
                netto = data.capaciteit["beschikbaar_uren"].sum()
                if bruto:
                    kaarten.append({"lbl": "Verlies door seizoen", "val": fmt(bruto - netto),
                                    "sub": pct((1 - netto / bruto) * 100) + " van bruto",
                                    "cls": "warn"})
            kpi_cards(kaarten)
            per_team = (mp.groupby("team")
                          .agg(medewerkers=("mdw_key", "nunique"),
                               extern=("type", lambda s: int((s == "Extern").sum())),
                               contracturen=("contract_uren", "sum"))
                          .sort_values("contracturen", ascending=False))
            per_team.columns = ["Medewerkers", "Ingeleend", "Contracturen"]
            st.dataframe(per_team, width="stretch")
