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
    """Week-as die ALTIJD bij de huidige week begint.

    Niet afleiden uit de data: de onderhoudsplanning bevat ook regels met een plandatum
    in het verleden (achterstand), en die trokken het venster jaren terug — met als
    gevolg een capaciteitsreeks van nul. Vooruitkijken begint bij vandaag.
    """
    vandaag = pd.Timestamp.today().normalize()
    maandag = vandaag - pd.Timedelta(days=vandaag.weekday())
    return pd.date_range(start=maandag, periods=horizon, freq="7D")


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


def _cap_reeks(data, opts, weken) -> pd.Series:
    """Effectieve capaciteit per week van de HELE ploeg.

    Bewust niet gefilterd op 'teams met openstaand werk': het tempo wordt over de
    hele capaciteitspopulatie gemeten, dus de capaciteit moet dat ook zijn. Anders
    vergelijk je het tempo van iedereen met de capaciteit van een paar afdelingen.
    """
    kolom = capaciteit_kolom(data, opts.get("seizoen", False))
    eff = (opts.get("efficiency_pct", 100) / 100) if opts.get("efficiency") else 1.0
    cap = data.capaciteit
    if "n_dagen" in cap.columns:
        # Randweken van het contracturen-venster bevatten maar 2-4 werkdagen. Die als
        # volwaardige week meetekenen ziet uit als een capaciteitsinstorting; eruit.
        dagen = cap.groupby("week_start")["n_dagen"].max()
        cap = cap[cap["week_start"].isin(set(dagen[dagen >= 5].index))]
    return per_week(cap, kolom, weken) * eff


def _rapport_antwoord(data, opts, weken):
    """Conclusie op basis van de RAPPORTDEFINITIES, niets zelf verzonnen.

    Openstaand werk = projectwerk (pagina 'Begrotingsuren per project') plus nog uit te
    voeren onderhoud (rapport Onderhoudsplanning). Capaciteit = de pagina 'Projectplanning':
    eigen monteurs met de projectenplanning-vlag, exclusief inleen.
    """
    vandaag = pd.Timestamp.today().normalize()
    v = data.vraag.copy()
    v["week_start"] = pd.to_datetime(v["week_start"])
    soort = v["soort"] if "soort" in v.columns else pd.Series("Projecten", index=v.index)
    v["soort"] = soort

    vooruit = v[v["week_start"] >= vandaag]
    per_soort = vooruit.groupby("soort")["uren"].sum().to_dict()
    projecten = float(per_soort.get("Projecten", 0.0))
    onderhoud = float(per_soort.get("Onderhoud", 0.0))
    openstaand = projecten + onderhoud
    achterstand = float(v[(v["soort"] == "Onderhoud") & (v["week_start"] < vandaag)]["uren"].sum())

    cap = _cap_reeks(data, opts, weken)
    cap_nz = cap[cap > 0]
    cap_wk = float(cap_nz.median()) if len(cap_nz) else 0.0
    # n_mw staat per afdeling-week; het totaal is de som over afdelingen in één week.
    n_mw = 0
    if len(data.capaciteit) and "n_mw" in data.capaciteit.columns:
        n_mw = int(data.capaciteit.groupby("week_start")["n_mw"].sum().max())

    weken_nodig = (openstaand / cap_wk) if cap_wk > 0 else float("nan")
    kind = "risico" if weken_nodig > 40 else ("let_op" if weken_nodig > 20 else "goed")

    kop = (f"Er staat <b>{fmt(openstaand)} uur</b> werk open. Met "
           f"<b>{fmt(cap_wk)} uur per week</b> eigen capaciteit is dat "
           f"<b>{weken_nodig:.0f} weken</b> vol.")
    delen = []
    if projecten:
        delen.append(f"<b>{fmt(projecten)} uur</b> projectwerk")
    if onderhoud:
        delen.append(f"<b>{fmt(onderhoud)} uur</b> onderhoud")
    toe = "Opgebouwd uit " + " en ".join(delen) + "."
    if achterstand > 0:
        toe += (f" Daarnaast staat er <b>{fmt(achterstand)} uur</b> onderhoud met een plandatum "
                f"in het verleden — achterstand die er nog bij komt.")
    toe += (f" De capaciteit is die van {n_mw and str(n_mw) + ' ' or ''}eigen monteurs met de "
            f"projectenplanning-vlag; ingeleende capaciteit rekent het rapport niet mee.")
    return kind, kop, toe, dict(projecten=projecten, onderhoud=onderhoud,
                                openstaand=openstaand, achterstand=achterstand,
                                cap_wk=cap_wk, weken_nodig=weken_nodig, n_mw=n_mw)


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

    # Alles hieronder komt uit de rapportdefinities (zie herkomst-blok onderaan).
    kind, kop, toe, k = _rapport_antwoord(data, opts, weken)
    _antwoordblok(kind, kop, toe)

    kpi_cards([
        {"lbl": "Projectwerk open", "val": fmt(k["projecten"]), "sub": "uren nog te plannen",
         "cls": "accent"},
        {"lbl": "Onderhoud open", "val": fmt(k["onderhoud"]), "sub": "uren nog uit te voeren"},
        {"lbl": "Achterstand onderhoud", "val": fmt(k["achterstand"]), "sub": "plandatum verstreken",
         "cls": "risk" if k["achterstand"] > 0 else "ok"},
        {"lbl": "Eigen capaciteit", "val": fmt(k["cap_wk"]), "sub": "uren per week"},
        {"lbl": "Volgeboekt", "val": f"{k['weken_nodig']:.0f} wk" if k["weken_nodig"] == k["weken_nodig"] else "—",
         "sub": "als er niets bij komt",
         "cls": "risk" if k["weken_nodig"] > 40 else ("warn" if k["weken_nodig"] > 20 else "ok")},
    ])

    st.markdown("###### Openstaand werk per week, tegen je eigen capaciteit")
    _vraag_grafiek(data, opts, weken, k)
    _detail_blokken(data, profile, opts, weken, cap_rel, kolom)

    with st.expander("Wat gaat er werkelijk per week doorheen? (niet uit de rapporten)"):
        tp = data.tempo_per_week()
        if tp:
            st.markdown(
                f"De rapporten zeggen niets over het tempo. Gemeten op de geboekte uren draait "
                f"de hele organisatie **{fmt(tp['projecturen'])} projecturen** plus "
                f"**{fmt(tp['indirecte_uren'])} indirecte uren** per week "
                f"(mediaan over {tp['weken']} volledige weken). Let op: dat is de "
                f"**hele** ploeg, dus een ruimere populatie dan de eigen monteurs hierboven.")
            hist = data.tempo.sort_values("week_start")
            figt = go.Figure()
            figt.add_trace(go.Bar(x=hist["week_start"], y=hist["projecturen"],
                                  name="Projecturen", marker_color=NAVY2))
            figt.add_trace(go.Bar(x=hist["week_start"], y=hist["indirecte_uren"],
                                  name="Indirect", marker_color=NAVY_LIGHT))
            if "uren_buiten_populatie" in hist.columns:
                figt.add_trace(go.Bar(x=hist["week_start"], y=hist["uren_buiten_populatie"],
                                      name="Ingeleend", marker_color=GREY))
            figt.update_layout(**PLOT, height=280, barmode="stack")
            figt.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
            st.plotly_chart(figt, width="stretch")
        else:
            st.caption("Geen tempo-gegevens beschikbaar.")

    _herkomst()
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

def _tempo_grafiek(data, opts, weken, t, cap_wk, vrij) -> None:
    """Tijdreeks: geboekte uren per week (verleden) en beschikbare capaciteit (vooruit).

    Weken op de x-as — een planningsbeeld zonder tijdas zegt niets. Links wat er
    werkelijk door de ploeg ging, rechts wat er beschikbaar is (met de zomerdip),
    met het huidige tempo als stippellijn zodat de vrije ruimte zichtbaar wordt.
    """
    hist = data.tempo.copy().sort_values("week_start")
    cap = _cap_reeks(data, opts, weken)
    cap = cap[cap > 0]

    fig = go.Figure()
    if len(hist):
        fig.add_trace(go.Bar(x=hist["week_start"], y=hist["projecturen"],
                             name="Projecturen (geboekt)", marker_color=NAVY2))
        fig.add_trace(go.Bar(x=hist["week_start"], y=hist["indirecte_uren"],
                             name="Indirecte uren (geboekt)", marker_color=NAVY_LIGHT))
        if "uren_buiten_populatie" in hist.columns and hist["uren_buiten_populatie"].sum() > 0:
            fig.add_trace(go.Bar(x=hist["week_start"], y=hist["uren_buiten_populatie"],
                                 name="Ingeleend (geen contracturen)", marker_color=GREY))
    if len(cap):
        label = ("Beschikbaar na verlof en feestdagen" if opts.get("seizoen")
                 else "Beschikbaar (bruto contracturen)")
        fig.add_trace(go.Scatter(x=list(cap.index), y=cap.values, name=label, mode="lines",
                                 line=dict(color=NAVY, width=2.5),
                                 fill="tozeroy", fillcolor="rgba(22,19,111,0.06)"))
        fig.add_trace(go.Scatter(x=list(cap.index), y=[t["totaal"]] * len(cap),
                                 name="Huidig tempo (mediaan)", mode="lines",
                                 line=dict(color=GOLD, width=2, dash="dash")))

    # scheidslijn tussen gemeten verleden en beschikbare toekomst
    if len(cap):
        grens = pd.Timestamp(cap.index.min())
        fig.add_shape(type="line", x0=grens, x1=grens, xref="x", y0=0, y1=1, yref="paper",
                      line=dict(color=GREY, width=1, dash="dot"))
        fig.add_annotation(x=grens, xref="x", y=1.0, yref="paper", yanchor="bottom",
                           text="vanaf hier beschikbaar", showarrow=False,
                           font=dict(size=10, color="#8A8DB0"))

    fig.update_layout(**PLOT, height=360, barmode="stack")
    fig.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
    fig.update_xaxes(gridcolor="#F5F6FA", dtick=7 * 24 * 3600 * 1000 * 2, tickformat="%d-%m")
    st.plotly_chart(fig, width="stretch")
    uitleg = ("Links de weken die al geboekt zijn (dat is het werkelijke tempo), rechts de "
              "beschikbare capaciteit per week. Het verschil tussen de capaciteitslijn en de "
              "gestippelde tempolijn is je vrije ruimte")
    uitleg += (f" &mdash; nu {fmt(vrij)} uur per week." if vrij > 0
               else f" &mdash; die is er nu niet ({fmt(vrij)} uur).")
    st.caption(uitleg)

def _vraag_grafiek(data, opts, weken, k) -> None:
    """Openstaand werk per week, gesplitst naar soort, met de capaciteitslijn."""
    v = data.vraag.copy()
    v["week_start"] = pd.to_datetime(v["week_start"])
    if "soort" not in v.columns:
        v["soort"] = "Projecten"
    v = v[v["week_start"].isin(weken)]
    cap = _cap_reeks(data, opts, weken)

    kleur = {"Projecten": NAVY2, "Onderhoud": GOLD}
    fig = go.Figure()
    for s in ["Projecten", "Onderhoud"]:
        deel = v[v["soort"] == s]
        if not len(deel):
            continue
        reeks = deel.groupby("week_start")["uren"].sum().reindex(weken, fill_value=0.0)
        fig.add_trace(go.Bar(x=list(weken), y=reeks.values, name=s,
                             marker_color=kleur.get(s, NAVY_LIGHT)))
    capn = cap[cap > 0]
    if len(capn):
        fig.add_trace(go.Scatter(x=list(capn.index), y=capn.values, mode="lines",
                                 name="Eigen capaciteit per week",
                                 line=dict(color=NAVY, width=2.5)))
    fig.update_layout(**PLOT, height=340, barmode="stack")
    fig.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
    fig.update_xaxes(gridcolor="#F5F6FA", tickformat="%d-%m")
    st.plotly_chart(fig, width="stretch")
    st.caption("Staven: werk dat op die week gepland staat. Lijn: capaciteit van de eigen "
               "monteurs. Het openstaande werk is niet gelijkmatig over de weken verdeeld — "
               "waar de staaf boven de lijn komt, past het die week niet.")


def _herkomst() -> None:
    """Elk cijfer met de bron erbij, zodat het in Power BI na te slaan is."""
    with st.expander("Waar komt elk cijfer vandaan?"):
        rijen = [
            ("Projectwerk open", "Projectenplanning &rarr; <i>Begrotingsuren per project</i>",
             "Nog te plannen uren (methode 1), met de slicer-standaarden van die pagina: "
             "montagetaken, hoofdproject Actueel + fase Opdracht, einddatum in de toekomst"),
            ("Onderhoud open", "Onderhoudsplanning &rarr; <i>Nog uit te voeren</i>",
             "SSM Onderhoudsplanning en te verwachten kosten, kolom 'nog te verwachten aantal', "
             "op eigen Plandatum vanaf 2024-01-01"),
            ("Achterstand onderhoud", "Onderhoudsplanning &rarr; <i>Achterstand</i>",
             "zelfde bron, plandatum v&oacute;&oacute;r vandaag"),
            ("Eigen capaciteit", "Projectenplanning &rarr; <i>Projectplanning</i>",
             "Contracturen medewerkers, begrensd op Medewerker Status = N, "
             "Projectenplanning = Ja en Ingeleend = Nee, alleen werkdagen"),
        ]
        html = ('<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
                '<tr style="color:#8A8DB0;font-size:10.5px;text-transform:uppercase">'
                '<th style="text-align:left;padding:6px 8px">Cijfer</th>'
                '<th style="text-align:left;padding:6px 8px">Rapport / pagina</th>'
                '<th style="text-align:left;padding:6px 8px">Measure en filters</th></tr>')
        for a, b, c in rijen:
            html += (f'<tr><td style="padding:6px 8px;font-weight:650;color:{NAVY}">{a}</td>'
                     f'<td style="padding:6px 8px">{b}</td>'
                     f'<td style="padding:6px 8px;color:#5D6089">{c}</td></tr>')
        html += "</table>"
        st.markdown(html, unsafe_allow_html=True)
        st.caption("De seizoenscorrectie in de zijbalk is een Notifica-model en zit niet in "
                   "deze rapporten. Staat die aan, dan wijkt de capaciteit bewust af van "
                   "wat Power BI toont.")
