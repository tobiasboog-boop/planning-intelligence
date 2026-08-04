"""
an_controle.py — Analyse "Signalen & controle".

De Nederlandse tegenhanger van de Controle-pagina's uit het Power BI-rapport van de klant:
wat moet je nakijken VOORDAT je op deze cijfers stuurt?

Elke controle is één uitlegbare filter op het projectoverzicht (het canonieke frame
`projecten`). Twee dingen maken deze pagina eerlijk in plaats van alarmistisch:

  • Elke controle rapporteert tegen zijn **eigen noemer** (een overschrijding kan alleen
    op een begroot project, dus 25 van de 73 begrote projecten — niet 25 van 1.115).
  • Raakt een controle het merendeel van die noemer, dan is het geen serie losse fouten
    maar een **structureel patroon** (een inrichtingskeuze). Dat wordt als zodanig
    gelabeld en apart gehouden in de samenvatting, zodat het aantal dat je écht kunt
    oppakken zichtbaar blijft.

Werkt uitsluitend op het contract (contract.PlanningData). Controles waarvoor de bron de
benodigde kolom niet levert, worden overgeslagen én expliciet benoemd.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from an_common import PLOT, caveat_box, fmt, guard, pct
from config import AMBER, GREY, NAVY2, RED, ClientProfile
from contract import PlanningData
from theme import kpi_cards, pill

# ── Opmaak per ernstniveau ───────────────────────────────────────────────────
ERNST_RANG = {"risico": 2, "let_op": 1, "neutraal": 0}
ERNST_KLEUR = {"risico": RED, "let_op": AMBER, "neutraal": NAVY2}
ERNST_KPI_CLS = {"risico": "risk", "let_op": "warn", "neutraal": "accent"}

# Nette kolomkoppen voor de tabellen
LABELS = {
    "project_key": "Nr.",
    "project": "Project",
    "team": "Team",
    "fase": "Fase",
    "projectleider": "Projectleider",
    "begroot": "Begroot (u)",
    "geboekt": "Geboekt (u)",
    "nog_te_plannen": "Nog te plannen (u)",
    "overschrijding": "Overschrijding (u)",
    "calculatie": "Calculatie (u)",
    "pct_gereed": "Gereed (%)",
    "verschil": "Verschil (u)",
    "afwijking_pct": "Afwijking (%)",
}

NUM_KOLOMMEN = ["begroot", "geboekt", "nog_te_plannen", "overschrijding",
                "calculatie", "pct_gereed"]
TXT_KOLOMMEN = ["project", "team", "fase", "projectleider"]
BASIS = ["project_key", "project", "team", "fase", "projectleider"]
MAX_RIJEN = 60          # tabellen compact houden
LEEG_TEKST = {"", "none", "nan", "nat", "-", "0", "0.0", "onbekend", "n.v.t."}

# Grenzen voor "structureel patroon"
STRUCT_AANDEEL = 0.50   # raakt de helft of meer van zijn eigen noemer …
STRUCT_MIN_N = 10       # … bij minimaal dit aantal geraakte projecten …
STRUCT_MIN_NOEMER = 8   # … en een noemer die groot genoeg is om iets te betekenen


# ── Hulpfuncties ─────────────────────────────────────────────────────────────
def _prep(projecten: pd.DataFrame) -> pd.DataFrame:
    """Werkkopie met gegarandeerde kolommen en numerieke types."""
    p = projecten.copy()
    for k in NUM_KOLOMMEN:
        p[k] = pd.to_numeric(p[k], errors="coerce") if k in p.columns else np.nan
    for k in TXT_KOLOMMEN:
        if k not in p.columns:
            p[k] = np.nan
    if "project_key" not in p.columns:
        p["project_key"] = np.arange(1, len(p) + 1)
    return p


def _gevuld(p: pd.DataFrame, *kolommen: str) -> bool:
    """True als álle genoemde kolommen minstens één echte waarde bevatten."""
    return all(k in p.columns and p[k].notna().any() for k in kolommen)


def _leeg_tekst(s: pd.Series) -> pd.Series:
    """True waar een tekst-/sleutelkolom feitelijk niets zegt (leeg, NaN of 0)."""
    ruw = s.astype(str).str.strip().str.lower()
    return s.isna() | ruw.isin(LEEG_TEKST)


def _sleutels(s: pd.Series) -> pd.Series:
    """Projectsleutels normaliseren zodat int/str/float-varianten matchen."""
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _tabel(df: pd.DataFrame, kolommen: list[str]) -> None:
    """Toon de geraakte projecten compact, in de aangeleverde sortering."""
    kols = [k for k in kolommen if k in df.columns]
    toon = df.loc[:, kols].copy()
    for k in kols:
        if pd.api.types.is_numeric_dtype(toon[k]):
            toon[k] = toon[k].round(0)
        else:
            # gemengde types (bv. projectleider als naam én als sleutel) breken de
            # Arrow-serialisatie van st.dataframe → altijd als tekst tonen
            toon[k] = toon[k].astype("string").fillna("—").replace({"0": "—", "": "—"})
    if len(toon) > MAX_RIJEN:
        st.caption(f"Eerste {MAX_RIJEN} van {fmt(len(toon))} projecten (grootste eerst).")
        toon = toon.head(MAX_RIJEN)
    toon = toon.rename(columns={k: LABELS.get(k, k) for k in kols})
    cfg = {}
    for k in kols:
        label = LABELS.get(k, k)
        if pd.api.types.is_numeric_dtype(toon[label]):
            cfg[label] = st.column_config.NumberColumn(label, format="%.0f")
    st.dataframe(toon, width="stretch", hide_index=True, column_config=cfg)


def _controle(key, kpi, titel, ernst, betekenis, actie, rijen, kolommen,
              metriek, uren_label, uren_kort, noemer, noemer_label) -> dict:
    """Bouw één controle-record; uren/mediaan worden uit `metriek` afgeleid."""
    if len(rijen) and metriek in rijen.columns:
        waarden = pd.to_numeric(rijen[metriek], errors="coerce").abs()
        uren = float(waarden.sum(skipna=True))
        mediaan = float(waarden.median(skipna=True)) if waarden.notna().any() else np.nan
    else:
        uren, mediaan = 0.0, np.nan
    return {"key": key, "kpi": kpi, "titel": titel, "ernst": ernst,
            "betekenis": betekenis, "actie": actie, "df": rijen, "kolommen": kolommen,
            "aantal": int(len(rijen)), "uren": uren, "mediaan": mediaan,
            "uren_label": uren_label, "uren_kort": uren_kort,
            "noemer": int(noemer), "noemer_label": noemer_label,
            "structureel": False}


def _markeer_structureel(controles: list[dict]) -> None:
    """Label controles die het merendeel van hun eigen noemer raken.

    `structureel` = de oorzaak zit in het proces, niet in losse projecten.
    `actielijst`  = de geraakte projecten vormen tóch een concrete werklijst. Een
    structureel patroon met ernst 'risico' (bv. uren die in geen enkele week staan)
    blijft op de actielijst: de oorzaak is systemisch, de uren zijn concreet.
    """
    for c in controles:
        c["structureel"] = bool(
            c["aantal"] >= STRUCT_MIN_N
            and c["noemer"] >= STRUCT_MIN_NOEMER
            and c["aantal"] >= STRUCT_AANDEEL * c["noemer"])
        c["actielijst"] = (not c["structureel"]) or c["ernst"] == "risico"


# ── Controles opbouwen ───────────────────────────────────────────────────────
def _bouw_controles(data: PlanningData, p: pd.DataFrame, opts: dict) -> tuple[list[dict], list[str]]:
    controles: list[dict] = []
    overgeslagen: list[str] = []

    b = p["begroot"].fillna(0.0)
    g = p["geboekt"].fillna(0.0)
    ntp = p["nog_te_plannen"].fillna(0.0)
    ov = p["overschrijding"].fillna(0.0)
    cal = p["calculatie"].fillna(0.0)

    n_begroot = int((b > 0).sum())
    n_geboekt = int((g > 0).sum())
    n_ntp = int((ntp > 0).sum())
    L_BEGROOT = "begrote projecten"
    L_NTP = "projecten met nog te plannen werk"

    # a) Overschrijding — meer geboekt dan begroot
    if _gevuld(p, "overschrijding"):
        r = p[ov > 0].sort_values("overschrijding", ascending=False)
        controles.append(_controle(
            "overschrijding", "Overschrijding", "Meer geboekt dan begroot", "risico",
            "Op deze projecten zijn meer uren geboekt dan de werkvoorbereiding heeft begroot. "
            "De resterende capaciteitsbehoefte is hier vrijwel zeker te laag: het werk loopt "
            "door, maar er staat geen begroting meer tegenover.",
            "Laat de projectleider de restbehoefte per project herijken en de begroting bijwerken "
            "(of vastleggen dat het meerwerk is). Zonder herijking plan je met een te lage vraag.",
            r, BASIS + ["begroot", "geboekt", "overschrijding", "nog_te_plannen"],
            "overschrijding", "overschreden uren", "u overschreden",
            n_begroot or len(p), L_BEGROOT))
    else:
        overgeslagen.append("Overschrijding (kolom `overschrijding` niet gevuld)")

    # b) Geboekt zonder begroting.
    #    Let op: raakt dit het merendeel van de geboekte projecten, dan is het geen serie
    #    fouten maar een inrichtingskeuze (service-/onderhoudswerk wordt niet begroot).
    #    De ernst wordt dan bewust afgeschaald — zie _markeer_structureel.
    if _gevuld(p, "geboekt", "begroot"):
        r = p[(g > 0) & (b <= 0)].sort_values("geboekt", ascending=False)
        struct = (len(r) >= STRUCT_MIN_N and n_geboekt >= STRUCT_MIN_NOEMER
                  and len(r) >= STRUCT_AANDEEL * n_geboekt)
        controles.append(_controle(
            "geen_begroting", "Uren zonder begroting", "Geboekte uren zonder begroting",
            "let_op" if struct else "risico",
            "Hier zijn wél uren geschreven, maar staat geen begroting in de werkvoorbereiding. "
            "Deze projecten zijn onzichtbaar in elke capaciteitsprognose: ze gebruiken mensen, "
            "maar claimen geen uren.",
            "Bepaal welk deel service-/onderhoudswerk is dat bewust niet wordt begroot, en welk "
            "deel projectwerk is waarvan de begroting nooit is vastgelegd. Dat laatste alsnog "
            "begroten; het eerste als vaste aftrek op je beschikbare capaciteit meenemen, anders "
            "plan je met uren die al vergeven zijn.",
            r, BASIS + ["begroot", "geboekt", "nog_te_plannen"],
            "geboekt", "geboekte uren zonder begroting", "u geboekt",
            n_geboekt or len(p), "projecten met geboekte uren"))
    else:
        overgeslagen.append("Uren zonder begroting (kolom `geboekt` of `begroot` niet gevuld)")

    # c) Begroting zonder voortgang
    if _gevuld(p, "begroot", "geboekt", "nog_te_plannen"):
        r = p[(b > 0) & (g == 0) & (ntp > 0)].sort_values("nog_te_plannen", ascending=False)
        controles.append(_controle(
            "niet_gestart", "Nog niet gestart", "Begroting zonder voortgang", "let_op",
            "Begroot, nog te plannen, maar nog geen enkel uur geboekt. Dat kan kloppen (het "
            "project start later), maar deze projecten leggen nu al beslag op je capaciteit in "
            "de planningshorizon.",
            "Check per project de startdatum. Wat verschuift of vervalt moet uit de vraag, anders "
            "reken je met werk dat er (nog) niet is.",
            r, BASIS + ["begroot", "nog_te_plannen", "pct_gereed"],
            "nog_te_plannen", "nog te plannen uren", "u te plannen",
            n_begroot or len(p), L_BEGROOT))
    else:
        overgeslagen.append("Nog niet gestart (begroot/geboekt/nog te plannen niet volledig gevuld)")

    # d) Nog te plannen groter dan de begroting
    if _gevuld(p, "nog_te_plannen", "begroot"):
        m = ntp > b
        r = p[m].copy()
        r["verschil"] = (ntp - b)[m]
        r = r.sort_values("verschil", ascending=False)
        controles.append(_controle(
            "ntp_boven_begroot", "Te plannen > begroot", "Nog te plannen hoger dan de begroting",
            "let_op",
            "Er staat meer werk in te plannen dan er ooit is begroot. Meestal een dubbeltelling "
            "(werk twee keer opgevoerd) of een herplanning die niet in de begroting is doorgevoerd.",
            "Leg deze projecten naast de begrotingsregels: dubbel opgevoerd, of verouderde "
            "begroting? Dubbeltellingen maken je capaciteitstekort kunstmatig groot.",
            r, BASIS + ["begroot", "nog_te_plannen", "verschil"],
            "verschil", "uren boven de begroting", "u boven begroting",
            n_ntp or len(p), L_NTP))
    else:
        overgeslagen.append("Te plannen > begroot (kolom `nog_te_plannen` of `begroot` niet gevuld)")

    # e) Calculatie versus begroting
    if _gevuld(p, "calculatie", "begroot"):
        basis = (b > 0) & (cal > 0)
        afw = (cal - b).abs() / b.replace(0, np.nan) * 100
        m = basis & (afw > 25)
        r = p[m].copy()
        r["afwijking_pct"] = afw[m]
        r["verschil"] = (cal - b)[m]
        r = r.sort_values("afwijking_pct", ascending=False)
        controles.append(_controle(
            "calc_vs_begroot", "Calculatie wijkt af", "Calculatie en begroting lopen uiteen",
            "let_op",
            "De gecalculeerde uren (verkoop) en de begrote uren (werkvoorbereiding) verschillen "
            "meer dan 25%. Er zijn dan twee waarheden over hetzelfde project — en de planning "
            "gebruikt er maar één.",
            "Bespreek per project welke leidend is. Structureel te laag calculeren kost marge; "
            "structureel te hoog begroten blokkeert capaciteit die je elders nodig hebt.",
            r, BASIS + ["calculatie", "begroot", "verschil", "afwijking_pct"],
            "verschil", "uren verschil calculatie vs. begroting", "u verschil",
            int(basis.sum()) or len(p), "projecten met calculatie én begroting"))
    else:
        overgeslagen.append("Calculatie wijkt af (kolom `calculatie` of `begroot` niet gevuld)")

    # f) Ontbrekende projectleider
    if _gevuld(p, "projectleider", "begroot"):
        m = _leeg_tekst(p["projectleider"]) & (b > 0)
        r = p[m].sort_values("begroot", ascending=False)
        controles.append(_controle(
            "geen_pl", "Geen projectleider", "Projecten zonder projectleider", "neutraal",
            "Begrote projecten zonder gekoppelde projectleider (leeg of niet gekoppeld). Er is dan "
            "niemand die de restbehoefte bijstelt — precies de handeling waarvan de "
            "betrouwbaarheid van de planning afhangt.",
            "Koppel een projectleider in het ERP. Dit is de goedkoopste controle van de lijst en "
            "maakt de andere signalen direct toewijsbaar.",
            r, BASIS + ["begroot", "geboekt", "nog_te_plannen"],
            "begroot", "begrote uren zonder projectleider", "u begroot",
            n_begroot or len(p), L_BEGROOT))
    else:
        overgeslagen.append("Geen projectleider (kolom `projectleider` of `begroot` niet gevuld)")

    # g) Nog te plannen werk dat in geen enkele week staat
    if data.heeft("vraag") and _gevuld(p, "nog_te_plannen"):
        vk = set(_sleutels(pd.Series(data.vraag["project_key"])))
        m = (ntp > 0) & ~_sleutels(p["project_key"]).isin(vk)
        r = p[m].sort_values("nog_te_plannen", ascending=False)
        wk = pd.to_datetime(data.vraag["week_start"], errors="coerce")
        venster = (f"De weekplanning loopt nu van {wk.min():%d-%m-%Y} t/m {wk.max():%d-%m-%Y} "
                   f"(horizon in de tool: {int(opts.get('horizon', 26))} weken). "
                   if wk.notna().any() else "")
        controles.append(_controle(
            "buiten_planning", "Niet in weekplanning", "Werk dat in geen enkele week staat",
            "risico",
            "Deze projecten hebben nog te plannen uren, maar komen in geen enkele week van de "
            f"planning voor. {venster}Ze zitten dus niet in de capaciteitsbalans en niet in de "
            "teamcijfers per week: onzichtbaar werk dat straks alsnog moet gebeuren.",
            "Zet deze uren in de tijd (startweek plus doorlooptijd), of leg vast dat het project "
            "buiten de horizon valt. Zolang dit openstaat is elk capaciteitsoverschot in de "
            "balans te optimistisch.",
            r, BASIS + ["begroot", "geboekt", "nog_te_plannen"],
            "nog_te_plannen", "nog te plannen uren buiten de weekplanning", "u niet in de planning",
            n_ntp or len(p), L_NTP))
    elif not data.heeft("vraag"):
        overgeslagen.append("Niet in weekplanning (building block Benodigde uren ontbreekt)")

    # h) Werk zonder herkenbaar team (gebruikt capaciteit indien aanwezig)
    if _gevuld(p, "nog_te_plannen"):
        team_leeg = _leeg_tekst(p["team"])
        toelichting = "Het team is niet gevuld op het project"
        if data.heeft("capaciteit") and "team" in data.capaciteit.columns:
            cap_teams = set(data.capaciteit["team"].astype(str).str.strip())
            team_leeg = team_leeg | ~p["team"].astype(str).str.strip().isin(cap_teams)
            toelichting = "Het team is niet gevuld, of komt niet voor in de capaciteitsopgave"
        m = team_leeg & (ntp > 0)
        r = p[m].sort_values("nog_te_plannen", ascending=False)
        controles.append(_controle(
            "geen_team", "Team ontbreekt", "Werk zonder herkenbaar team", "let_op",
            f"{toelichting}. Dit werk landt daardoor in geen enkele teamplanning: het telt mee in "
            "het totaal, maar je ziet niet wie het moet doen.",
            "Koppel het project aan een afdeling of team, of voeg dat team toe aan de "
            "capaciteitsopgave. Zonder teamkoppeling is een tekort per team niet te onderbouwen.",
            r, BASIS + ["begroot", "nog_te_plannen"],
            "nog_te_plannen", "nog te plannen uren zonder teamcapaciteit", "u zonder team",
            n_ntp or len(p), L_NTP))

    _markeer_structureel(controles)
    controles.sort(key=lambda c: (c["aantal"] > 0, ERNST_RANG[c["ernst"]], c["aantal"]),
                   reverse=True)
    return controles, overgeslagen


# ── Grafiek: waar zit het gewicht ────────────────────────────────────────────
def _gewicht_chart(controles: list[dict]) -> None:
    top = [c for c in controles if c["aantal"] > 0 and c["uren"] > 0]
    if len(top) < 2:
        return
    top = sorted(top, key=lambda c: c["uren"])          # oplopend = grootste bovenaan
    x = [c["uren"] for c in top]
    st.markdown("##### Waar zit het gewicht")
    fig = go.Figure(go.Bar(
        x=x, y=[c["kpi"] for c in top], orientation="h",
        marker=dict(color=[GREY if c["structureel"] else ERNST_KLEUR[c["ernst"]] for c in top],
                    line=dict(width=0)),
        text=[f"{fmt(c['uren'])} u · {fmt(c['aantal'])} proj."
              + ("  (structureel)" if c["structureel"] else "") for c in top],
        textposition="outside", textfont=dict(size=11),
        customdata=[[fmt(c["aantal"]), c["uren_label"], fmt(c["uren"]),
                     f"{fmt(c['noemer'])} {c['noemer_label']}"] for c in top],
        hovertemplate=("<b>%{y}</b><br>%{customdata[0]} van %{customdata[3]}<br>"
                       "%{customdata[2]} uur (%{customdata[1]})<extra></extra>"),
    ))
    fig.update_layout(**PLOT)
    fig.update_layout(
        height=max(190, 34 * len(top) + 96), showlegend=False,
        margin=dict(l=10, r=10, t=10, b=34),
        xaxis=dict(title="uren in het spel", gridcolor="#EEF0FB", zeroline=False,
                   range=[0, max(x) * 1.45]),
        yaxis=dict(automargin=True, ticksuffix="  "),
    )
    st.plotly_chart(fig, width="stretch")
    st.caption("Gebruik dit om te prioriteren, niet als optelsom: de urensoort verschilt per "
               "signaal (overschreden, geboekt, nog te plannen) en projecten kunnen in meerdere "
               "signalen voorkomen. Grijs = structureel patroon over vrijwel de hele groep; dat "
               "vraagt een inrichtingskeuze, geen actie per project.")


# ── Render ───────────────────────────────────────────────────────────────────
def render(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    if guard(data, "projecten"):
        return

    st.subheader("Signalen & controle")
    st.caption(
        "Voordat je op de cijfers uit de andere tabbladen stuurt, moet je weten waar de "
        "administratie zichzelf tegenspreekt. Elke controle hieronder is één filter op het "
        "projectoverzicht: geen rekenfout van de tool, maar een plek waar begroting, boeking en "
        "planning niet op elkaar aansluiten. Wat hier openstaat, maakt de capaciteitsplanning "
        "net zo onzeker als de registratie eronder — en is meteen je werklijst."
    )

    p = _prep(data.projecten)
    controles, overgeslagen = _bouw_controles(data, p, opts)

    if not controles:
        st.markdown('<div class="note">Het projectoverzicht bevat te weinig gevulde kolommen om '
                    'controles op uit te voeren. Koppel begroting, geboekte uren en nog te '
                    'plannen uren om deze pagina te activeren.</div>', unsafe_allow_html=True)
        caveat_box(data)
        return

    # ── KPI-rij: aantal geraakte projecten per controle ──────────────────────
    kaarten = []
    for c in controles:
        if not c["aantal"]:
            sub = "geen signalen"
            cls = "ok"
        else:
            sub = (f"{fmt(c['uren'])} {c['uren_kort']}" if c["uren"] > 0
                   else f"van {fmt(c['noemer'])} {c['noemer_label']}")
            if c["structureel"]:
                sub = f"structureel · {sub}"
            cls = ERNST_KPI_CLS[c["ernst"]]
        kaarten.append({"lbl": c["kpi"], "val": fmt(c["aantal"]), "sub": sub, "cls": cls})
    kpi_cards(kaarten)

    _gewicht_chart(controles)

    # ── Controles ────────────────────────────────────────────────────────────
    st.markdown("#### Controles")
    met_signaal = [c for c in controles if c["aantal"] > 0]
    open_keys = {c["key"] for c in met_signaal[:3]}

    for c in controles:
        woord = "project" if c["aantal"] == 1 else "projecten"
        label = f"{c['titel']} — {fmt(c['aantal'])} {woord}"
        if c["structureel"]:
            label += " (structureel)"
        with st.expander(label, expanded=c["key"] in open_keys):
            st.markdown(pill(c["ernst"] if c["aantal"] else "goed"), unsafe_allow_html=True)
            if not c["aantal"]:
                st.markdown(f"Geen signalen op deze controle ({fmt(c['noemer'])} "
                            f"{c['noemer_label']} nagekeken). Deze cijfers kun je zonder "
                            f"correctie gebruiken.")
                continue
            aandeel = c["aantal"] / c["noemer"] * 100 if c["noemer"] else np.nan
            st.markdown(f"**Raakt {fmt(c['aantal'])} van de {fmt(c['noemer'])} "
                        f"{c['noemer_label']}** ({pct(aandeel)}).")
            if c["structureel"]:
                st.markdown(
                    "**Structureel patroon.** Dit raakt vrijwel de hele groep. Behandel het niet "
                    "als een reeks losse fouten: het is een inrichtingskeuze in de manier waarop "
                    "jullie vastleggen. Eén besluit lost het op; project-voor-project nalopen niet.")
            st.markdown(f"**Wat het betekent.** {c['betekenis']}")
            st.markdown(f"**Wat je eraan doet.** {c['actie']}")
            if c["uren"] > 0:
                extra = (f" — mediaan {fmt(c['mediaan'])} uur per project"
                         if pd.notna(c["mediaan"]) else "")
                st.caption(f"Omvang: {fmt(c['uren'])} uur ({c['uren_label']}){extra}.")
            _tabel(c["df"], c["kolommen"])

    # ── Samenvatting ─────────────────────────────────────────────────────────
    st.markdown("#### Samenvatting")
    n_tot = int(len(p))
    geraakt: set[str] = set()
    concreet: set[str] = set()
    for c in met_signaal:
        keys = set(_sleutels(c["df"]["project_key"]))
        geraakt |= keys
        if not c["structureel"]:
            concreet |= keys
    n_ger, n_con = len(geraakt), len(concreet)
    aandeel = (n_ger / n_tot * 100) if n_tot else np.nan
    structureel = [c for c in met_signaal if c["structureel"]]

    def _cls(v):
        return "risk" if v >= 40 else ("warn" if v >= 15 else "ok")

    kaarten = [
        {"lbl": "Projecten in beeld", "val": fmt(n_tot), "sub": "actuele portefeuille"},
        {"lbl": "Met minimaal één signaal", "val": fmt(n_ger),
         "sub": f"{pct(aandeel)} van de portefeuille", "cls": _cls(aandeel)},
    ]
    if structureel:
        aandeel_con = (n_con / n_tot * 100) if n_tot else np.nan
        kaarten.append({"lbl": "Concreet op te pakken", "val": fmt(n_con),
                        "sub": f"{pct(aandeel_con)} — buiten de structurele patronen",
                        "cls": _cls(aandeel_con)})
    else:
        kaarten.append({"lbl": "Aandeel portefeuille", "val": pct(aandeel),
                        "sub": "van de projecten in beeld", "cls": _cls(aandeel)})
    kaarten.append({"lbl": "Controles met signaal",
                    "val": f"{len(met_signaal)}/{len(controles)}",
                    "sub": "uitgevoerde controles", "cls": "accent"})
    kpi_cards(kaarten)

    if met_signaal:
        top = met_signaal[0]
        regels = [
            f"Begin bij **{top['titel'].lower()}**: {fmt(top['aantal'])} van de "
            f"{fmt(top['noemer'])} {top['noemer_label']}"
            + (f", {fmt(top['uren'])} uur {top['uren_label']}" if top["uren"] > 0 else "") + ".",
            "Eén project kan in meerdere controles voorkomen, dus de aantallen per controle "
            "tellen niet op tot het totaal hierboven.",
        ]
        if structureel:
            namen = ", ".join(c["titel"].lower() for c in structureel)
            regels.append(
                f"Het hoge totaal komt vooral door een structureel patroon ({namen}). Dat is één "
                f"inrichtingsvraag, geen werklijst — daarom staat het aantal dat je écht per "
                f"project kunt oppakken er los naast.")
        for r in regels:
            st.markdown(r)
    else:
        st.markdown("Geen enkele controle geeft een signaal. De projectadministratie sluit aan "
                    "op de planning.")

    if overgeslagen:
        st.caption("Niet uitgevoerd omdat de bron de kolom (nog) niet levert: "
                   + "; ".join(overgeslagen) + ".")

    caveat_box(data)
