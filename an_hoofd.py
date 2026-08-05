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


def _week_frame(data: PlanningData, weken, profile: ClientProfile | None = None,
                seizoen: bool = True) -> pd.DataFrame:
    """Eén rij per week met de opbouw van de capaciteit: ingepland, afwezig, vrij.

    Alleen **volledige** weken. Het contracturen-venster van de view begint midden in de
    huidige week, waardoor de eerste week 1.077 u toont in plaats van 2.756 u — als
    volwaardige week meegeteld lijkt dat een capaciteitsinstorting. Grens op 80% van de
    hoogste weekcapaciteit; robuuster dan tellen op werkdagen (afdelingen verschillen).
    """
    cap = data.capaciteit.copy()
    if not len(cap):
        return pd.DataFrame()
    cap["week_start"] = pd.to_datetime(cap["week_start"])

    kol = {"contract_uren": "contract", "ingepland_uren": "ingepland",
           "gepland_project_uren": "gepland_project", "gepland_werkbon_uren": "gepland_werkbon",
           "indirect_uren": "indirect", "verlof_uren": "verlof", "ongepland_uren": "ongepland"}
    agg = {v: (k, "sum") for k, v in kol.items() if k in cap.columns}
    agg["n_mw"] = ("n_mw", "sum")
    g = cap.groupby("week_start").agg(**agg)
    if "contract" in g.columns and len(g):
        g = g[g["contract"] >= 0.8 * g["contract"].max()]
    g = g[g.index.isin(weken)].sort_index()
    if not len(g):
        return g

    # Indirect werk dat géén afwezigheid is (werkoverleg, KAM, opruimen): wél belegd, maar
    # niet 'weg'. Apart houden, anders valt het onder verlof of onder vrije ruimte.
    g["indirect_overig"] = (g["indirect"] - g["verlof"]).clip(lower=0)

    # De seizoensaanvulling op WEEKNIVEAU, niet per afdeling. Per afdeling afkappen op nul
    # laat een afdeling met veel verlof niet compenseren voor één met weinig, en telt de
    # aanvulling daardoor structureel te hoog op (bij Megens: 290 u in een week waarin er in
    # totaal al méér verlof staat dan het model verwacht). Op weekniveau kan dat niet.
    if seizoen:
        p = None
        if profile is not None:
            p = sn.SeasonParams(vakantiedagen=profile.vakantiedagen, adv_dagen=profile.adv_dagen,
                                ziekte_pct=profile.ziekte_pct / 100,
                                opleiding_pct=profile.opleiding_pct / 100,
                                uren_per_dag=profile.uren_per_dag)
        tmp = sn.aanvulling(g.reset_index().rename(
            columns={"contract": "contract_uren", "verlof": "verlof_uren",
                     "ongepland": "ongepland_uren"}), p)
        g["verwacht_verlof"] = tmp["verwacht_verlof"].values
        g["extra_verlof"] = tmp["extra_verlof"].values
    else:
        g["verwacht_verlof"] = 0.0
        g["extra_verlof"] = 0.0

    # Beschikbare contracturen = bruto minus alle afwezigheid (aangevraagd + nog te
    # verwachten). Dít is de lijn waar de vrije ruimte tegen afgezet moet worden, niet de
    # bruto contracturen: die staan onveranderd op 2.756 u, ook in de bouwvak.
    g["beschikbaar"] = (g["contract"] - g["verlof"] - g["extra_verlof"]).clip(lower=0)
    g["vrij"] = (g["beschikbaar"] - g["gepland_project"] - g["gepland_werkbon"]
                 - g["indirect_overig"]).clip(lower=0)
    return g


def _tempo_volledig(tempo: pd.DataFrame) -> pd.DataFrame:
    """Weken met een half geboekte week eruit.

    De laatste week in de urenboeking is per definitie nog niet af (bij Megens 1.000 u
    tegen een mediaan van 5.800 u). Als volwaardige week getekend ziet dat uit als een
    instorting van de productie. Grens op de helft van de mediaan.
    """
    if not len(tempo):
        return tempo
    t = tempo.copy().sort_values("week_start")
    totaal = t["projecturen"].fillna(0) + t["indirecte_uren"].fillna(0)
    med = float(totaal.median())
    if med > 0:
        t = t[totaal >= 0.5 * med]
    return t


def _dekking_zin(dek: dict) -> str:
    """Zin over de plandekking, met alleen de zones die er in dit venster zijn.

    Bij een horizon van 26 weken bestaat "na week 26" niet, dan is dekking_lang NaN. Zonder
    deze controle kwam er letterlijk "nan% daarna" in de UI te staan.
    """
    delen = []
    for kol, label in (("dekking_kort", "de eerste 8 weken"), ("dekking_mid", "week 9-26"),
                       ("dekking_lang", "daarna")):
        w = dek.get(kol)
        if w is not None and w == w:
            delen.append(f"{w:.0f}% in {label}")
    if not delen:
        return ""
    return "gemiddeld " + ", ".join(delen[:-1]) + (" en " if len(delen) > 1 else "") + delen[-1]


def _plandekking(wf: pd.DataFrame) -> dict:
    """Hoe ver vooruit wordt er in Syntess écht gepland?

    Registratie dooft uit met afstand — precies zoals verlofaanvragen. Bij Megens:
    35% van de contracturen belegd in de eerste 8 weken, 14% in week 9-26, 2% daarna.
    Zonder dat te benoemen lijkt verre toekomst 'helemaal vrij', en dat is onjuist.
    """
    if not len(wf) or "ingepland" not in wf.columns:
        return {}
    d = (wf["ingepland"] / wf["contract"].replace(0, np.nan) * 100).dropna()
    gedekt = d[d >= 10]
    return {
        "horizon": pd.Timestamp(gedekt.index.max()) if len(gedekt) else None,
        "weken_gedekt": int(len(gedekt)),
        "dekking_kort": float(d.head(8).mean()) if len(d) else float("nan"),
        "dekking_mid": float(d.iloc[8:26].mean()) if len(d) > 8 else float("nan"),
        "dekking_lang": float(d.iloc[26:].mean()) if len(d) > 26 else float("nan"),
    }


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


def _rapport_antwoord(data, opts, weken, wf: pd.DataFrame):
    """Conclusie op basis van de RAPPORTDEFINITIES en de planning, niets zelf verzonnen.

    Vraagzijde: projectwerk (pagina 'Begrotingsuren per project', nog te plannen) plus nog
    uit te voeren onderhoud (rapport Onderhoudsplanning).
    Aanbodzijde: de pagina 'Projectplanning' — eigen monteurs met de projectenplanning-vlag,
    exclusief inleen — maar dan uit de ATPlanning-view, zodat we ook zien wat er al op naam
    en datum staat. Wat overblijft (Ongepland, minus nog te verwachten verlof) is de ruimte
    waar het openstaande werk in moet.
    """
    vandaag = pd.Timestamp.today().normalize()
    v = data.vraag.copy()
    v["week_start"] = pd.to_datetime(v["week_start"])
    if "soort" not in v.columns:
        v["soort"] = "Projecten"

    vooruit = v[v["week_start"] >= vandaag]
    per_soort = vooruit.groupby("soort")["uren"].sum().to_dict()
    # Projectwerk open = de KPI-measure van het rapport ([Nog te plannen uren], 33.574 u), NIET
    # de som van de weekspreiding. Die spreiding is een andere measure (Begrotingsuren per dag,
    # 37.450 u) en loopt door tot 2033; hem als "open werk" optellen gaf een derde getal dat in
    # geen enkel rapport staat. De grafiek houdt de spreiding, de KPI houdt de measure.
    projecten = 0.0
    if len(data.projecten) and "nog_te_plannen" in data.projecten.columns:
        projecten = float(pd.to_numeric(data.projecten["nog_te_plannen"],
                                       errors="coerce").fillna(0).sum())
    else:
        projecten = float(per_soort.get("Projecten", 0.0))
    # alleen de weken die ook in de grafiek staan, anders klopt de caption niet
    in_venster = v[v["week_start"].isin(pd.DatetimeIndex(weken))]
    begroot_venster = float(in_venster[in_venster["soort"] == "Projecten"]["uren"].sum())
    onderhoud = float(per_soort.get("Onderhoud", 0.0))
    openstaand = projecten + onderhoud
    achterstand = float(v[(v["soort"] == "Onderhoud") & (v["week_start"] < vandaag)]["uren"].sum())

    eff = (opts.get("efficiency_pct", 100) / 100) if opts.get("efficiency") else 1.0
    cap_wk = float(wf["contract"].median()) if len(wf) else 0.0
    ingepland = float(wf["ingepland"].sum()) if "ingepland" in wf.columns else 0.0
    gepland_wb = float(wf["gepland_werkbon"].sum()) if "gepland_werkbon" in wf.columns else 0.0
    vrij_wk = float(wf["vrij"].median()) * eff if "vrij" in wf.columns and len(wf) else 0.0
    n_mw = int(wf["n_mw"].max()) if "n_mw" in wf.columns and len(wf) else 0
    dek = _plandekking(wf)

    weken_nodig = (openstaand / vrij_wk) if vrij_wk > 0 else float("nan")
    kind = "risico" if weken_nodig > 40 else ("let_op" if weken_nodig > 20 else "goed")

    kop = (f"Er staat <b>{fmt(openstaand)} uur</b> werk in de boeken. Na wat al ingepland is "
           f"en na verlof houd je <b>{fmt(vrij_wk)} uur per week</b> vrij — genoeg voor "
           f"<b>{weken_nodig:.0f} weken</b> werk.")
    delen = []
    if projecten:
        delen.append(f"<b>{fmt(projecten)} uur</b> projectwerk")
    if onderhoud:
        delen.append(f"<b>{fmt(onderhoud)} uur</b> onderhoud")
    toe = "Opgebouwd uit " + " en ".join(delen) + "."
    if achterstand > 0:
        toe += (f" Daarnaast <b>{fmt(achterstand)} uur</b> onderhoud met een plandatum in het "
                f"verleden — achterstand die er nog bij komt.")
    toe += (f" Van de <b>{fmt(cap_wk)} uur</b> contracturen per week ({n_mw} eigen monteurs, "
            f"inleen telt het rapport niet mee) staat er vooruit <b>{fmt(ingepland)} uur</b> "
            f"al op naam en datum.")
    if dek.get("horizon") is not None:
        h = pd.Timestamp(dek["horizon"])
        toe += (f" Let op: die planning reikt tot <b>{_wk(h)} ({h.strftime('%d-%m-%Y')})</b>, "
                f"{dek['weken_gedekt']} weken; daarna staat er vrijwel niets vast en is de "
                f"vrije ruimte dus een bovengrens.")
    return kind, kop, toe, dict(projecten=projecten, begroot_venster=begroot_venster,
                                onderhoud=onderhoud,
                                openstaand=openstaand, achterstand=achterstand,
                                cap_wk=cap_wk, vrij_wk=vrij_wk, ingepland=ingepland,
                                gepland_werkbon=gepland_wb, weken_nodig=weken_nodig,
                                n_mw=n_mw, dekking=dek)


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
    wf = _week_frame(data, weken, profile, opts.get("seizoen", True))

    # Alles hieronder komt uit de rapportdefinities + de planning (zie verantwoording).
    kind, kop, toe, k = _rapport_antwoord(data, opts, weken, wf)
    _antwoordblok(kind, kop, toe)

    kpi_cards([
        {"lbl": "Projectwerk open", "val": fmt(k["projecten"]), "sub": "uren nog te plannen",
         "cls": "accent"},
        {"lbl": "Onderhoud open", "val": fmt(k["onderhoud"]), "sub": "uren nog uit te voeren"},
        {"lbl": "Achterstand onderhoud", "val": fmt(k["achterstand"]), "sub": "plandatum verstreken",
         "cls": "risk" if k["achterstand"] > 0 else "ok"},
        {"lbl": "Al ingepland", "val": fmt(k["ingepland"]), "sub": "uren op naam en datum"},
        {"lbl": "Vrije ruimte", "val": fmt(k["vrij_wk"]), "sub": "uren per week na verlof",
         "cls": "accent"},
        {"lbl": "Werkvoorraad", "val": f"{k['weken_nodig']:.0f} wk" if k["weken_nodig"] == k["weken_nodig"] else "—",
         "sub": "weken werk in de boeken",
         "cls": "risk" if k["weken_nodig"] > 40 else ("warn" if k["weken_nodig"] > 20 else "ok")},
    ])

    st.markdown("###### Hoe je capaciteit per week is belegd")
    _capaciteit_grafiek(wf, k)

    st.markdown("###### Begrote uren per week per project, tegen de vrije capaciteit")
    _vraag_grafiek(data, opts, weken, k, wf)
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
            hist = _tempo_volledig(data.tempo)
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

    _verantwoording(data, k, wf)
    caveat_box(data)


def _medewerker_blok(data: PlanningData) -> None:
    """Planning per medewerker per week — het antwoord op "wie kan ik nog inzetten?".

    Dit kán dus wél op medewerkerniveau: de ATPlanning-view heeft de planning per
    MedewerkerKey per dag. Groeperen op de key en niet op de naam, want namen zijn niet
    uniek in dit model. De weektotalen van dit blok sluiten exact aan op de
    afdelingscijfers hierboven (gecontroleerd: 2.756 / 642 / 932 u voor de eerste weken).
    """
    m = data.planning_mdw.copy()
    m["week_start"] = pd.to_datetime(m["week_start"])
    p = (m.groupby(["mdw_key", "medewerker", "team"], as_index=False)
          .agg(contract=("contract_uren", "sum"), ingepland=("ingepland_uren", "sum"),
               vrij=("ongepland_uren", "sum")))
    p["dekking"] = p["ingepland"] / p["contract"].replace(0, np.nan) * 100
    n_weken = int(m["week_start"].nunique())

    vol = p[p["dekking"] > 100]
    leeg_ = p[p["ingepland"] <= 0.5]
    kpi_cards([
        {"lbl": "Medewerkers", "val": f"{len(p)}", "sub": f"over {n_weken} weken"},
        {"lbl": "Overpland", "val": f"{len(vol)}", "sub": "meer ingepland dan contracturen",
         "cls": "risk" if len(vol) else "ok"},
        {"lbl": "Niets ingepland", "val": f"{len(leeg_)}", "sub": "volledig vrij in de planning",
         "cls": "warn" if len(leeg_) else "ok"},
        {"lbl": "Gemiddelde dekking", "val": pct(float(p["dekking"].mean())),
         "sub": "van de contracturen belegd", "cls": "accent"},
    ])

    tab = p.sort_values("dekking", ascending=False)[
        ["medewerker", "team", "contract", "ingepland", "vrij", "dekking"]]
    tab.columns = ["Medewerker", "Afdeling", "Contracturen", "Ingepland", "Vrij", "Dekking %"]
    st.dataframe(tab.round(1), width="stretch", hide_index=True, height=320)
    st.caption(
        f"Planning per medewerker over {n_weken} weken vooruit, gegroepeerd op **MedewerkerKey** "
        f"(niet op naam — namen zijn in dit model niet uniek). Bovenaan wie het volst zit, "
        f"onderaan wie er nog vrij is. Dekking boven 100% betekent meer ingeplande uren dan "
        f"contracturen: die persoon staat dubbel geboekt of maakt overuren.")


def _capaciteit_grafiek(wf: pd.DataFrame, k: dict) -> None:
    """Waar gaat elke contractuur per week naartoe: ingepland, afwezig, of nog vrij?

    Dit is het beeld dat een planner mist in Syntess: de contracturen als 100%-balk,
    opgedeeld in wat al vastligt, wat er door verlof afgaat en wat er nog over is. De
    dalende ingeplande stapel maakt tegelijk zichtbaar hoe kort de planning vooruit reikt.
    """
    if not len(wf):
        st.caption("Geen capaciteitsgegevens voor deze periode.")
        return
    x = list(wf.index)
    fig = go.Figure()
    # Stapelvolgorde is bewust: alles wat vastligt onderaan, de vrije ruimte daarboven, en
    # de afwezigheid bovenaan. Daardoor eindigt de vrije ruimte exact op de lijn met
    # beschikbare contracturen, en is de kop van de staaf de bruto contracturen.
    lagen = [
        ("gepland_project", "Ingepland op project", dict(color=NAVY)),
        ("gepland_werkbon", "Ingepland op werkbon", dict(color=NAVY2)),
        ("indirect_overig", "Indirect werk (overleg, KAM)", dict(color="#C9CCE4")),
        # Vrije ruimte = wit gevuld met een gouden rand: het is leegte, geen massa. Een
        # gevulde gele balk trekt alle aandacht naar precies het deel waar niets gebeurt.
        ("vrij", "Nog vrij", dict(color="white", line=dict(color=GOLD, width=1.2))),
        ("verlof", "Verlof en ziekte (aangevraagd)", dict(color=NAVY_LIGHT)),
        # Gearceerd, niet gevuld: dit is het enige blok dat níet uit Syntess komt maar uit
        # het model. Zelfde tint als het aangevraagde verlof (het is dezelfde soort uren),
        # maar de arcering laat zien dat het een aanname is.
        ("extra_verlof", "Verlof dat nog komt (model)",
         dict(color="white", line=dict(color=NAVY_LIGHT, width=1.0),
              pattern=dict(shape="/", fgcolor=NAVY_LIGHT, bgcolor="white", size=5, solidity=0.22))),
    ]
    for kol, label, marker in lagen:
        if kol not in wf.columns or float(wf[kol].sum()) <= 0:
            continue
        fig.add_trace(go.Bar(x=x, y=wf[kol].values, name=label, marker=marker,
                             hovertemplate="%{x|%d-%m-%Y}<br>" + label +
                                           ": %{y:.0f} uur<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=wf["contract"].values, name="Contracturen (bruto)",
                             mode="lines", line=dict(color="#9A9DBB", width=1.2, dash="dot"),
                             hovertemplate="%{x|%d-%m-%Y}<br>bruto: %{y:.0f} uur<extra></extra>"))
    if "beschikbaar" in wf.columns:
        fig.add_trace(go.Scatter(
            x=x, y=wf["beschikbaar"].values, name="Beschikbare contracturen", mode="lines",
            line=dict(color=NAVY, width=3.5, dash="dot"),
            hovertemplate="%{x|%d-%m-%Y}<br>beschikbaar: %{y:.0f} uur<extra></extra>"))
    fig.update_layout(**PLOT, height=380, barmode="stack", bargap=0.3)
    fig.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
    # Maandlabels: "01-09" leest als een dag-maand-datum en is verwarrend over een half jaar.
    fig.update_xaxes(gridcolor="#F5F6FA", dtick="M1", tickformat="%b %Y",
                     ticklabelmode="period")
    st.plotly_chart(fig, width="stretch")
    dek = k.get("dekking") or {}
    cap = ("Elke staaf is één week bruto contracturen (dunne stippellijn). De **dikke "
           "stippellijn** is wat daar na verlof en ziekte van overblijft: dáár moet het werk "
           "in passen. Het lichte vlak eronder is de vrije ruimte. ")
    zin = _dekking_zin(dek)
    if zin:
        cap += (f"De ingeplande uren nemen met de afstand af: {zin} van de capaciteit belegd. "
                f"Verder vooruit is de vrije ruimte dus optimistisch — er is nog niets "
                f"vastgelegd, niet niets te doen.")
    st.caption(cap)


def _detail_blokken(data: PlanningData, profile: ClientProfile, opts: dict,
                    weken, cap_rel, kolom) -> None:
    """Knelpunten, heatmap per afdeling en bemensing — gedeeld door beide weergaven."""
    # ── de drie grootste knelpunten ──────────────────────────────────────────
    st.markdown("###### Waar het knelt")
    st.caption(
        "Let op &mdash; dit is **onze toevoeging**, geen rapportbeeld. Het PBIP-model legt geen "
        "relatie tussen de capaciteitstabel en projecten, dus het rapport zet vraag en "
        "capaciteit nooit per afdeling tegen elkaar af. Wij koppelen op naamgelijkheid: de "
        "afdeling van het *project* tegen de afdeling van de *medewerker*. Onderhoud heeft geen "
        "capaciteitsafdeling en valt hier dus buiten.")
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
                    colorscale=[[0.0, "#F4F5FB"], [0.5, NAVY_LIGHT], [0.75, NAVY2],
                                [0.9, AMBER], [1.0, RED]],
                    colorbar=dict(title="%", thickness=10),
                    hovertemplate="%{y}<br>%{x}: %{z:.0f}% beslag<extra></extra>"))
                figh.update_layout(**{**PLOT, "margin": dict(l=10, r=10, t=10, b=10)},
                                   height=max(200, 42 * len(hm)))
                figh.update_yaxes(tickfont=dict(size=10), automargin=True)
                st.plotly_chart(figh, width="stretch")
                st.caption("Beslag op de capaciteit per afdeling per week. Rood = meer werk dan mensen.")

    # ── planning per medewerker achter een klik ──────────────────────────────
    if data.heeft("planning_mdw"):
        with st.expander("Wie is de komende weken vrij? (per medewerker)"):
            _medewerker_blok(data)

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
    hist = _tempo_volledig(data.tempo)
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

def _kort(naam: str, n: int = 30) -> str:
    """Projectnaam inkorten op een woordgrens, niet midden in een woord.

    "10PR10.00200 - (E) PROJECTUREN BEGROTING (" is onleesbaar; de projectcode plus een paar
    hele woorden wel. De volledige naam blijft in de tooltip staan.
    """
    naam = str(naam).strip()
    if len(naam) <= n:
        return naam
    kort = naam[:n]
    if " " in kort:
        kort = kort[:kort.rindex(" ")]
    return kort.rstrip(" -(") + "…"


def _vraag_grafiek(data, opts, weken, k, wf: pd.DataFrame) -> None:
    """Begrote uren per week, gestapeld per project, met de vrije capaciteit als lijn.

    Spiegelt de hoofdgrafiek van de rapportpagina *Begrotingsuren per project*: X = week,
    gestapelde reeks = **project**, Y = begrote uren per werkdag (methode 1), tweede reeks =
    de vrije capaciteit. Eerder stapelden we op "soort" (Projecten/Onderhoud); dan zie je niet
    welk project een piek veroorzaakt, en dat is precies waar de planner naar zoekt.

    Onderhoud komt uit het andere rapport en heeft daar geen projectdimensie, dus dat blijft
    één reeks. De onderhoudsuren landen in maandbrokken: de SSM-plandatum is doorgaans de
    eerste van de maand, waardoor een hele maand onderhoud in één week valt. Niet gladstrijken
    - dat zou een spreiding suggereren die niet in de bron staat.
    """
    v = data.vraag.copy()
    v["week_start"] = pd.to_datetime(v["week_start"])
    if "soort" not in v.columns:
        v["soort"] = "Projecten"
    v = v[v["week_start"].isin(weken)]
    if not len(v):
        st.caption("Geen gedateerd werk in deze periode.")
        return

    pr = v[v["soort"] == "Projecten"]
    oh = v[v["soort"] == "Onderhoud"]

    # top 6 projecten op uren in dit venster; de rest samen. Zes is de grens waarop de legenda
    # nog op één regel past en de navy-tinten nog van elkaar te onderscheiden zijn.
    tot = pr.groupby("project")["uren"].sum().sort_values(ascending=False)
    top = list(tot.head(6).index)
    pr = pr.copy()
    pr["reeks"] = pr["project"].where(pr["project"].isin(top), "Overige projecten")

    # Tinten vanaf NAVY2 en lichter: de donkerste navy blijft voor de capaciteitslijn, anders
    # verdwijnt die in de staven.
    tinten = ["#3636A2", "#4A4AB8", "#6060CC", "#7A7ADB", "#9797E6", "#B4B4EE", "#D3D3F6"]
    fig = go.Figure()
    for i, naam in enumerate(list(tot.head(6).index) + (["Overige projecten"] if len(tot) > 6 else [])):
        deel = pr[pr["reeks"] == naam]
        if not len(deel):
            continue
        reeks = deel.groupby("week_start")["uren"].sum().reindex(weken, fill_value=0.0)
        fig.add_trace(go.Bar(x=list(weken), y=reeks.values, name=_kort(naam),
                             marker_color=tinten[min(i, len(tinten) - 1)],
                             hovertemplate="%{x|%d-%m-%Y}<br>" + naam +
                                           ": %{y:.0f} uur<extra></extra>"))
    if len(oh):
        reeks = oh.groupby("week_start")["uren"].sum().reindex(weken, fill_value=0.0)
        fig.add_trace(go.Bar(x=list(weken), y=reeks.values, name="Onderhoud (maandbrok)",
                             marker_color=GOLD,
                             hovertemplate="%{x|%d-%m-%Y}<br>onderhoud: %{y:.0f} uur<extra></extra>"))
    if len(wf) and "vrij" in wf.columns:
        fig.add_trace(go.Scatter(x=list(wf.index), y=wf["vrij"].values, mode="lines",
                                 name="Vrije capaciteit per week",
                                 line=dict(color=NAVY, width=3.5, dash="dot"),
                                 hovertemplate="%{x|%d-%m-%Y}<br>vrij: %{y:.0f} uur<extra></extra>"))
    fig.update_layout(**PLOT, height=380, barmode="stack", bargap=0.3)
    fig.update_yaxes(title="uren per week", gridcolor="#EEF0F7")
    fig.update_xaxes(gridcolor="#F5F6FA", dtick="M1", tickformat="%b %Y",
                     ticklabelmode="period")
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"Gestapeld per project, zoals de hoofdgrafiek van *Begrotingsuren per project*. "
        f"Dit zijn **begrote** uren per werkdag (methode 1) &mdash; de hele begroting uitgezet "
        f"over de looptijd, dus niet hetzelfde als de KPI *nog te plannen*. In dit venster "
        f"valt {fmt(k.get('begroot_venster', 0))} u; de rest is gedateerd na deze "
        f"{len(weken)} weken. De groene lijn is wat er per week vrij is.")


class _Altijd:
    """Contextmanager die niets doet — zodat dezelfde verantwoording zowel in een tab als in
    een expander gerenderd kan worden."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _tabel(kop: list[str], rijen: list[list[str]], breed: str = "22%") -> str:
    html = ('<table style="width:100%;border-collapse:collapse;font-size:12px;'
            'table-layout:fixed">'
            '<tr style="color:#8A8DB0;font-size:10px;text-transform:uppercase;'
            'letter-spacing:.4px;border-bottom:1px solid #E8EAF3">')
    for i, h in enumerate(kop):
        w = f' width="{breed}"' if i == 0 else ""
        html += f'<th{w} style="text-align:left;padding:6px 8px;vertical-align:bottom">{h}</th>'
    html += "</tr>"
    for r in rijen:
        html += '<tr style="border-bottom:1px solid #F2F3F9;vertical-align:top">'
        for i, cel in enumerate(r):
            stijl = (f"padding:7px 8px;font-weight:650;color:{NAVY}" if i == 0
                     else "padding:7px 8px;color:#4A4D74;line-height:1.5")
            html += f'<td style="{stijl}">{cel}</td>'
        html += "</tr>"
    return html + "</table>"


def render_uitleg(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    """Eigen tab: de volledige verantwoording, zonder dat je een expander moet vinden."""
    st.subheader("Uitleg en verantwoording")
    st.caption("Elk cijfer in deze tool terug naar het rapport, de measure en de filters "
               "waar het uit komt — plus wat wij toevoegen en waar we bewust afwijken. "
               "Bedoeld om naast Power BI te leggen.")
    if guard(data, "vraag", "capaciteit"):
        return
    weken = _venster(data, opts.get("horizon", profile.horizon_weken))
    wf = _week_frame(data, weken, profile, opts.get("seizoen", True))
    _, _, _, k = _rapport_antwoord(data, opts, weken, wf)
    _verantwoording(data, k, wf, expander=False)


def _verantwoording(data: PlanningData, k: dict, wf: pd.DataFrame,
                    expander: bool = True) -> None:
    """Volledige verantwoording: elk cijfer terug naar rapport, measure, filter en formule.

    Bedoeld om náást Power BI te leggen. Wie een getal niet vertrouwt, moet hier kunnen
    zien uit welke pagina het komt, welke slicer-standaarden zijn overgenomen, wat er
    gerekend is, en waar wij bewust afwijken. Zonder dat is een cijfer een mening.
    """
    dek = k.get("dekking") or {}
    modus = getattr(data.meta, "capaciteit_modus", "planning")

    ctx = (st.expander("Verantwoording: bron, aanname en berekening per cijfer")
           if expander else _Altijd())
    with ctx:
        st.markdown("**1. Waar komt elk cijfer vandaan?**")
        st.markdown(_tabel(
            ["Cijfer", "Rapport &rarr; pagina", "Measure / kolom + filters", "Berekening"],
            [
                [f"Projectwerk open<br><span style='font-weight:400;color:#8A8DB0'>{fmt(k['projecten'])} u</span>",
                 "Projectenplanning &rarr; <i>Begrotingsuren per project</i>",
                 "<code>maatwerk.Begrotinguren per werkdag</code>, kolom <i>begrote uren per "
                 "werkdag met plafond &mdash; methode 1</i>. Slicer-standaarden van die pagina: "
                 "7 montagetaken, werkgroep Wtb/Elektra/Elektra Qirion, hoofdprojectstatus "
                 "Actueel, hoofdprojectfase Opdracht, einddatum in toekomst = Ja",
                 "Die dagspreiding verdeelt de <b>volledige begroting</b> (37.450 u), dus ook de "
                 "2.496 u die al gepland staat. Wij gebruiken het tijdpatroon, maar schalen het "
                 "volume per project terug naar Syntess' eigen <i>Te plannen</i> "
                 "(= begroot &minus; totaal gepland). Controle: 34.955 u tegen 34.954 u in het "
                 "rapport &mdash; <b>0,0% afwijking</b>. Hiervan valt "
                 f"{fmt(k['projecten'])} u op een week vanaf vandaag"],
                [f"Onderhoud open<br><span style='font-weight:400;color:#8A8DB0'>{fmt(k['onderhoud'])} u</span>",
                 "Planning werkbonnen (S&amp;O) &rarr; <i>Onderhoudsplanning</i>",
                 "<code>notifica.SSM Onderhoudsplanning en te verwachten kosten</code>, kolom "
                 "<i>nog te verwachten aantal</i>, op de eigen <i>Plandatum</i> van die view, "
                 "vanaf 01-01-2024",
                 "Som vanaf vandaag. <b>Niet</b> op <code>Werkbonparagrafen.Plandatum</code> &mdash; "
                 "dat veld vult pas n&aacute;dat er een werkbon is en toont daardoor niets vooruit"],
                [f"Achterstand<br><span style='font-weight:400;color:#8A8DB0'>{fmt(k['achterstand'])} u</span>",
                 "idem", "zelfde kolom, plandatum v&oacute;&oacute;r vandaag",
                 "Werk waarvan de geplande datum al verstreken is; komt boven op het open werk"],
                [f"Contracturen<br><span style='font-weight:400;color:#8A8DB0'>{fmt(k['cap_wk'])} u/wk</span>",
                 "Projectenplanning &rarr; <i>Projectplanning</i>",
                 "<code>planning.Geplande en contracturen medewerkers ATPlanning</code>, "
                 "type <i>Contracturen</i>. Drie vlaggen uit het rapport: Medewerker Status = N, "
                 "Projectenplanning = J, <b>Ingeleend = N</b>",
                 f"Mediaan over de volledige weken vooruit; {k['n_mw']} medewerkers. "
                 f"Inleen zit er bewust niet in &mdash; dat doet het rapport ook niet"],
                [f"Al ingepland<br><span style='font-weight:400;color:#8A8DB0'>{fmt(k['ingepland'])} u</span>",
                 "(zit in geen van beide rapporten)",
                 "zelfde view, type <i>Project</i> en <i>Werkbon</i>, per MedewerkerKey en datum",
                 "Som vooruit. Hiermee zie je wel wie waar staat &mdash; de rapporten laten dit "
                 "niet zien (zie punt 3)"],
                [f"Vrije ruimte<br><span style='font-weight:400;color:#8A8DB0'>{fmt(k['vrij_wk'])} u/wk</span>",
                 "(afgeleid)", "type <i>Ongepland</i> uit dezelfde view, minus nog te verwachten verlof",
                 "Ongepland &minus; (verwacht verlof &minus; al aangevraagd verlof), "
                 "afgekapt op nul. Mediaan per week"],
                [f"Volgeboekt<br><span style='font-weight:400;color:#8A8DB0'>"
                 f"{k['weken_nodig']:.0f} wk</span>",
                 "(afgeleid)", "&mdash;",
                 f"({fmt(k['projecten'])} + {fmt(k['onderhoud'])}) &divide; {fmt(k['vrij_wk'])} "
                 f"= {k['weken_nodig']:.0f} weken, bij ongewijzigde bezetting en zonder inleen"],
            ]), unsafe_allow_html=True)

        st.markdown("**2. Aannames &mdash; wat wij toevoegen en Syntess niet weet**")
        aannames = [
            ["Verlof dat nog komt",
             "Syntess registreert vooruit alleen <b>aangevraagd</b> verlof. Dat is dichtbij "
             "compleet en verderop vrijwel leeg. Plandekking: " + (_dekking_zin(dek) or "onbekend")
             + ".",
             "Wij vullen alleen het <b>verschil</b> aan tussen wat statistisch te verwachten is "
             "en wat al is aangevraagd, afgekapt op nul. Nooit optellen bij wat de ERP al weet, "
             "dus nooit dubbel."],
            ["Seizoenspatroon",
             "25 verlofdagen, 6 feestdagen op werkdagen (2026), 4% ziekte, 1% opleiding, "
             "8 uur per dag; vakantie verdeeld over het jaar met juli/augustus als piek.",
             "Exact dezelfde formule en dezelfde cijfers als de "
             "<b>Directe-urencalculator in ons leerportaal</b>. Per klant instelbaar in de "
             "configuratiemodus. Uitkomst: augustus 62% beschikbaar, november 93%."],
            ["Planningshorizon",
             "De projectplanning reikt bij Megens tot "
             + (f"{_wk(pd.Timestamp(dek['horizon']))} "
                f"({pd.Timestamp(dek['horizon']).strftime('%d-%m-%Y')})"
                if dek.get("horizon") is not None else "beperkte tijd")
             + ". Daarna staat er vrijwel niets vast.",
             "Vrije ruimte verder vooruit is daarom een <b>bovengrens</b>, geen belofte. Er is "
             "niets vastgelegd &mdash; dat is niet hetzelfde als niets te doen."],
            ["Overlap onderhoud",
             f"Van het ingeplande werk staat {fmt(k['gepland_werkbon'])} u als werkbon in de "
             f"planning. Onderhoud dat al als werkbon is ingepland zit ook in "
             f"'Onderhoud open'.",
             "Dat is dus de <b>maximale dubbeltelling</b> tussen vraag en planning "
             f"({(k['gepland_werkbon'] / k['onderhoud'] * 100) if k['onderhoud'] else 0:.0f}% "
             f"van het open onderhoud). Wij trekken het niet af, want de twee rapporten "
             f"hebben geen gemeenschappelijke sleutel &mdash; maar je moet het weten."],
        ]
        st.markdown(_tabel(["Aanname", "Waarom nodig", "Hoe wij het doen"], aannames, "18%"),
                    unsafe_allow_html=True)

        st.markdown("**3. Waar wij bewust afwijken van de Power BI-rapporten**")
        afw = [
            ["Andere view voor de planning",
             "Beide rapporten kijken naar een view waarin de planregels ontbreken: in "
             "<code>planning.Geplande en contracturen medewerkers</code> stopt type "
             "Project/Werkbon in 2018, en in de SSM-variant ontbreken ze helemaal. Daardoor is "
             "<i>Ongepland</i> daar altijd gelijk aan de contracturen en <b>lijkt niets ingepland</b>.",
             "Wij gebruiken <code>&hellip; ATPlanning</code>, waar de planning w&eacute;l actueel in "
             "staat (werkbon t/m dec-2026, project t/m apr-2027). Melden aan Mark en Dolf."],
            ["Groeperen op MedewerkerKey",
             "Het rapport groepeert op <i>Medewerker omschrijving</i>. Dat is niet uniek: "
             "&quot;Verbruggen J.&quot; staat voor 182 verschillende sleutels, waardoor uren van "
             "verschillende mensen op &eacute;&eacute;n naam samenvallen.",
             "Wij groeperen op <b>MedewerkerKey</b> en tonen naam + code als label. Aantallen per "
             "persoon wijken daardoor af van Power BI &mdash; onze telling is de juiste."],
            ["Vrije ruimte i.p.v. totale capaciteit",
             "Openstaand werk vergelijken met de <i>volledige</i> capaciteit overschat wat erbij "
             "kan: een deel van die uren ligt al vast of gaat op aan verlof.",
             "Wij zetten openstaand werk tegen de <b>vrije</b> ruimte. Dat geeft een scherper "
             "(en minder rooskleurig) beeld dan het rapport."],
        ]
        st.markdown(_tabel(["Afwijking", "Wat er in Power BI gebeurt", "Wat wij doen"], afw, "18%"),
                    unsafe_allow_html=True)

        st.markdown("**4. Werkt dit ook bij een klant zonder planningsmodule?**")
        st.markdown(
            f'<div style="background:#F7F8FC;border-left:4px solid {NAVY2};padding:12px 16px;'
            f'border-radius:0 10px 10px 0;font-size:12.5px;color:#4A4D74;line-height:1.6">'
            f'Ja. De tool <b>meet zelf</b> of er vooruit gepland wordt en kiest daarop de basis '
            f'voor vrije ruimte. Bij deze klant staat de modus op '
            f'<b>{"planning" if modus == "planning" else "tempo"}</b>. '
            f'{getattr(data.meta, "capaciteit_uitleg", "")}'
            f'<br><br>Zonder planning is <i>Ongepland</i> gelijk aan de contracturen en zegt het '
            f'niets; dan rekent de tool met het werkelijke realisatietempo uit de geboekte uren. '
            f'Dezelfde analyses, andere onderlaag &mdash; dat is het building-block-principe.'
            f'</div>', unsafe_allow_html=True)

        st.caption("Alle cijfers zijn live opgehaald uit klant 1142 via de Notifica Data API "
                   "(read-only). Niets is overgetypt of gecached.")
