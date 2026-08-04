"""
an_adviezen.py — Analyse "Adviezen".

Automatisch afgeleide, actiegerichte signalen voor de directie. Elk advies:

    signaal (met echte getallen)  →  de juiste vervolgvraag

De regels rekenen uitsluitend op het canonieke contract (contract.PlanningData).
Ontbreekt een bron, dan valt de bijbehorende regel weg — er wordt niets verzonnen
en er wordt niets geëxtrapoleerd. De tool signaleert; de bedrijfsvoering blijft
aan de directie.
"""
from __future__ import annotations
import os
import re

import numpy as np
import pandas as pd
import streamlit as st

from an_common import fmt, pct, guard, caveat_box, capaciteit_kolom
from config import ClientProfile
from contract import PlanningData
from theme import advice_card, MND

# Sorteervolgorde: risico eerst, dan aandacht, dan de rest.
RANG = {"risico": 0, "let_op": 1, "neutraal": 2, "goed": 3}

MODEL = "claude-opus-4-8"


# ── kleine helpers ────────────────────────────────────────────────────────────
def _num(df: pd.DataFrame, kolom: str) -> pd.Series:
    """Numerieke kolom uit een frame; niet-numeriek/ontbrekend wordt 0."""
    if kolom not in df.columns:
        return pd.Series(0.0, index=df.index, dtype="float64")
    return pd.to_numeric(df[kolom], errors="coerce").fillna(0.0)


def _wk(ts) -> str:
    """Weeklabel in het Nederlands: 'week 27 (30 jun)'."""
    t = pd.Timestamp(ts)
    return f"week {t.isocalendar().week} ({t.day} {MND[t.month]})"


def _wkn(reeks) -> str:
    return ", ".join(_wk(t) for t in reeks)


def _plat(html: str) -> str:
    """HTML uit een advies-body strippen (voor de LLM-input)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def _mv(n: int, enkel: str, meer: str) -> str:
    """Nederlands enkelvoud/meervoud in een kop: 1 project staat / 3 projecten staan."""
    return f"{n} {enkel}" if n == 1 else f"{n} {meer}"


def _rekenbasis(data: PlanningData, profile: ClientProfile, opts: dict) -> tuple[int, int, float, str]:
    horizon = int(opts.get("horizon") or profile.horizon_weken)
    streef = int(opts.get("streef") or profile.streefbezetting)
    eff = 1.0
    if opts.get("efficiency"):
        eff = float(opts.get("efficiency_pct") or profile.default_efficiency) / 100.0
    kolom = capaciteit_kolom(data, bool(opts.get("seizoen"))) if data.heeft("capaciteit") else ""
    return horizon, streef, eff, kolom


# ── vraag vs. capaciteit over de horizon ─────────────────────────────────────
def _balans(data: PlanningData, opts: dict, horizon: int, eff: float, kolom: str):
    """Geeft (weken, vraag_per_week, cap_per_week, team_tabel) of None."""
    if not (data.heeft("vraag") and data.heeft("capaciteit")):
        return None

    weken = data.weken(horizon)

    v = data.vraag.copy()
    v["week_start"] = pd.to_datetime(v["week_start"])
    v["uren"] = _num(v, "uren")
    v = v[v["week_start"].isin(weken)]

    c = data.capaciteit.copy()
    c["week_start"] = pd.to_datetime(c["week_start"])
    c["cap"] = _num(c, kolom) * eff
    c = c[c["week_start"].isin(weken)]
    if not len(c):
        return None

    vraag_w = v.groupby("week_start")["uren"].sum().reindex(weken, fill_value=0.0)
    cap_w = c.groupby("week_start")["cap"].sum().reindex(weken, fill_value=0.0)

    teams = pd.DataFrame({"cap": c.groupby("team")["cap"].sum()})
    teams["vraag"] = v.groupby("team")["uren"].sum().reindex(teams.index).fillna(0.0)
    teams["beslag"] = teams["vraag"] / teams["cap"].replace(0, np.nan) * 100
    teams = teams.reset_index().rename(columns={"index": "team"})
    return weken, vraag_w, cap_w, teams


# ── de regels ────────────────────────────────────────────────────────────────
def _signalen(data: PlanningData, profile: ClientProfile, opts: dict) -> list[tuple[str, str, str]]:
    horizon, streef, eff, kolom = _rekenbasis(data, profile, opts)
    adv: list[tuple[str, str, str]] = []

    pr = data.projecten.copy()
    for k in ("begroot", "geboekt", "nog_te_plannen", "overschrijding"):
        pr[k] = _num(pr, k)
    if "project" not in pr.columns:
        pr["project"] = pr.index.astype(str)

    # 1) Projecten die over de begrote uren heen zijn
    over = pr[pr["overschrijding"] > 0].sort_values("overschrijding", ascending=False)
    if len(over):
        top = over.iloc[0]
        adv.append((
            "risico",
            f"{_mv(len(over), 'project staat', 'projecten staan')} boven de begrote uren",
            f"Samen <b>{fmt(over['overschrijding'].sum())} uur</b> overschrijding. Grootste: "
            f"<b>{top['project']}</b> met <b>+{fmt(top['overschrijding'])} uur</b> "
            f"({fmt(top['geboekt'])} geboekt op {fmt(top['begroot'])} begroot). "
            f"Vervolgvraag: is dit meerwerk dat nog gefactureerd kan worden, of is de begroting "
            f"te laag geweest? Kijk eerst naar dit project.",
        ))

    # 2) Grote projecten die nog nauwelijks gestart zijn → de komende capaciteitsclaim
    ntp_pos = pr.loc[pr["nog_te_plannen"] > 0, "nog_te_plannen"]
    if len(ntp_pos):
        drempel = float(max(100.0, np.nanpercentile(ntp_pos, 75)))
        nauwelijks = pr[(pr["begroot"] > 0)
                       & (pr["geboekt"] < 0.15 * pr["begroot"])
                       & (pr["nog_te_plannen"] > drempel)]
        nauwelijks = nauwelijks.sort_values("nog_te_plannen", ascending=False)
        if len(nauwelijks):
            top = nauwelijks.iloc[0]
            adv.append((
                "let_op",
                f"{_mv(len(nauwelijks), 'groot project staat', 'grote projecten staan')} "
                f"nog vrijwel volledig open",
                f"Samen <b>{fmt(nauwelijks['nog_te_plannen'].sum())} uur</b> nog in te plannen, terwijl er "
                f"nog minder dan 15% van de begrote uren geboekt is (drempel: meer dan "
                f"{fmt(drempel)} uur open). Grootste: <b>{top['project']}</b> met "
                f"<b>{fmt(top['nog_te_plannen'])} uur</b>. Dit is de capaciteitsclaim die eraan komt. "
                f"Vervolgvraag: staan deze projecten al in de weekplanning en klopt de startdatum nog?",
            ))

    bal = _balans(data, opts, horizon, eff, kolom)

    # 3) Weken boven 100% beslag + teams boven de streefbezetting
    if bal is not None:
        weken, vraag_w, cap_w, teams = bal
        beslag_w = (vraag_w / cap_w.replace(0, np.nan) * 100).dropna()
        krap = beslag_w[beslag_w > 100]
        if len(krap):
            piek = krap.sort_values(ascending=False)
            pw = piek.index[0]
            tekort = float(vraag_w.loc[pw] - cap_w.loc[pw])
            adv.append((
                "risico",
                f"In {len(krap)} van de {len(beslag_w)} weken past het werk niet in de bemensing",
                f"Zwaarste week is <b>{_wk(pw)}</b>: <b>{pct(piek.iloc[0])} beslag</b> "
                f"({fmt(vraag_w.loc[pw])} uur werk tegen {fmt(cap_w.loc[pw])} uur capaciteit, "
                f"tekort ± <b>{fmt(tekort)} uur</b>). Vervolgvraag: schuif je werk naar een rustiger week, "
                f"huur je in, of gaat er iets van de planning af?",
            ))

        boven = teams[teams["beslag"] > streef].sort_values("beslag", ascending=False)
        if len(boven):
            regels = " · ".join(
                f"<b>{r['team']}</b> {pct(r['beslag'])}" for _, r in boven.head(4).iterrows())
            adv.append((
                "let_op" if float(boven["beslag"].max()) <= 100 else "risico",
                f"{_mv(len(boven), 'team zit', 'teams zitten')} boven de streefbezetting "
                f"van {streef}%",
                f"Over {horizon} weken: {regels}. Vervolgvraag: is dit een piek van een paar weken "
                f"(dan opvangen binnen het team) of structureel over de hele horizon "
                f"(dan is het een bemensingsvraag)?",
            ))

        # 4) Teams met veel ruimte → kans om te herverdelen
        ruim = teams[(teams["beslag"].notna()) & (teams["beslag"] < 60)].sort_values("beslag")
        if len(ruim):
            vrij = float((ruim["cap"] - ruim["vraag"]).sum())
            regels = " · ".join(
                f"<b>{r['team']}</b> {pct(r['beslag'])}" for _, r in ruim.head(4).iterrows())
            adv.append((
                "goed",
                f"{_mv(len(ruim), 'team heeft', 'teams hebben')} ruimte in de planning",
                f"{regels} — samen ± <b>{fmt(vrij)} uur</b> onbenutte capaciteit over {horizon} weken. "
                f"Vervolgvraag: kan werk van de drukke teams hierheen, of is dit werk dat deze mensen "
                f"niet kunnen doen (kwalificatie, regio, project)?",
            ))

    # 5) Aandeel extern / ingeleend
    if data.heeft("medewerkers"):
        mdw = data.medewerkers.copy()
        if "in_planning" in mdw.columns:
            vlag = mdw["in_planning"].fillna(False).astype(bool)
            if vlag.any():
                mdw = mdw[vlag]
        if len(mdw) and "type" in mdw.columns:
            extern = int((mdw["type"].astype(str).str.strip().str.lower() == "extern").sum())
            aandeel = extern / len(mdw) * 100
            if aandeel > 30:
                adv.append((
                    "let_op",
                    "Groot deel van de ploeg is ingeleend",
                    f"<b>{pct(aandeel)}</b> van de mensen in de planning is extern "
                    f"({extern} van {len(mdw)}). Prima om pieken op te vangen. Vervolgvraag: wat doet dit "
                    f"met je marge per uur, en welke kennis zit nu bij mensen die morgen weg kunnen zijn?",
                ))

    # 6) Zomerdip: waar zakt de beschikbaarheid, en staat daar juist werk?
    if opts.get("seizoen") and data.heeft("capaciteit") \
            and "season_factor" in data.capaciteit.columns and bal is not None:
        weken, vraag_w, cap_w, _ = bal
        c = data.capaciteit.copy()
        c["week_start"] = pd.to_datetime(c["week_start"])
        c = c[c["week_start"].isin(weken)]
        fac = pd.to_numeric(c["season_factor"], errors="coerce")
        fac = c.assign(f=fac).groupby("week_start")["f"].mean().reindex(weken).dropna()
        if len(fac) and float(fac.min()) < 0.97:
            dip = fac.nsmallest(min(3, len(fac))).sort_index()
            dip_vraag = float(vraag_w.reindex(dip.index).fillna(0.0).sum())
            dip_cap = float(cap_w.reindex(dip.index).fillna(0.0).sum())
            dip_beslag = dip_vraag / dip_cap * 100 if dip_cap else float("nan")
            soort = "risico" if (dip_cap and dip_beslag > 100) else (
                "let_op" if (dip_cap and dip_beslag > streef) else "neutraal")
            staat = (f"In die weken staat <b>{fmt(dip_vraag)} uur</b> werk tegen "
                     f"<b>{fmt(dip_cap)} uur</b> effectief beschikbaar "
                     f"(<b>{pct(dip_beslag)} beslag</b>). " if dip_cap else
                     "Voor die weken staat er geen capaciteit in de bron, dus het beslag is "
                     "niet te berekenen. ")
            adv.append((
                soort,
                "Beschikbaarheid zakt het diepst in de vakantieperiode",
                f"Laagste weken: {_wkn(dip.index)} — daar is gemiddeld nog "
                f"<b>{pct(float(dip.mean()) * 100)}</b> van de bruto contracturen beschikbaar "
                f"(verlof, feestdagen, ziekte, opleiding). {staat}"
                f"Vervolgvraag: welk werk kun je vóór of ná deze weken zetten, en wat moet er "
                f"écht in de vakantieperiode gebeuren?",
            ))

    # 7) Niets te melden
    if not adv:
        adv.append((
            "goed",
            "Geen signalen die actie vragen",
            "Geen projecten boven de begrote uren, geen weken boven 100% beslag en geen teams boven "
            "de streefbezetting. Vervolgvraag: is er ruimte om werk naar voren te halen of extra "
            "werk aan te nemen?",
        ))

    return sorted(adv, key=lambda a: RANG.get(a[0], 9))


# ── optionele bestuurlijke samenvatting (Claude) ──────────────────────────────
def _samenvatting(data: PlanningData, signalen: list[tuple[str, str, str]], sleutel: str) -> None:
    """Laat een taalmodel de signalen samenvatten. Alleen de gegeven getallen."""
    try:
        import anthropic
    except ImportError:
        st.warning("De anthropic-SDK is niet geïnstalleerd. Installeer die met "
                   "`pip install anthropic` om een samenvatting te genereren.")
        return

    labels = {"risico": "ACTIE NODIG", "let_op": "AANDACHT",
              "goed": "KANS", "neutraal": "SIGNAAL"}
    invoer = "\n".join(f"- [{labels.get(k, 'SIGNAAL')}] {t}: {_plat(b)}" for k, t, b in signalen)
    prompt = (
        "Je bent adviseur capaciteits- en projectplanning bij een installatiebedrijf. "
        f"Hieronder staan de signalen die automatisch uit de planningsdata van {data.meta.klant} "
        "zijn afgeleid.\n\n"
        f"{invoer}\n\n"
        "Schrijf een bestuurlijke samenvatting voor de directie:\n"
        "- Nederlands, jij-vorm, zakelijk, maximaal 6 zinnen.\n"
        "- Begin met wat er nú het meeste aandacht vraagt.\n"
        "- Gebruik alleen de getallen die hierboven staan; verzin niets bij en reken niets om.\n"
        "- Benoem het signaal en de vraag die de directie moet beantwoorden. Schrijf niet voor "
        "hoe zij hun bedrijf moeten leiden.\n"
        "- Geen kopjes, geen opsomming, geen emoji."
    )

    try:
        client = anthropic.Anthropic()
        bericht = client.messages.create(
            model=MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        if bericht.stop_reason == "refusal":
            st.warning("Het model heeft deze vraag niet beantwoord. De signalen hierboven blijven "
                       "gewoon geldig.")
            return
        tekst = "\n\n".join(b.text for b in bericht.content if b.type == "text").strip()
        if not tekst:
            st.warning("Er kwam geen tekst terug. Probeer het opnieuw.")
            return
        st.session_state[sleutel] = tekst
    except anthropic.RateLimitError:
        st.warning("Te veel verzoeken achter elkaar. Wacht even en probeer het opnieuw.")
    except anthropic.AuthenticationError:
        st.warning("De ANTHROPIC_API_KEY wordt niet geaccepteerd. Controleer de sleutel in `.env`.")
    except anthropic.APIConnectionError:
        st.warning("Geen verbinding met de API. Controleer je netwerkverbinding.")
    except anthropic.APIStatusError as e:
        st.warning(f"De API gaf een fout terug (status {e.status_code}). Probeer het later opnieuw.")
    except Exception as e:                                    # noqa: BLE001 — UI mag nooit crashen
        st.warning(f"Kon geen samenvatting genereren: {e}")


def _llm_blok(data: PlanningData, profile: ClientProfile,
              signalen: list[tuple[str, str, str]]) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        st.caption("Een bestuurlijke samenvatting in doorlopende tekst kan er automatisch bij: "
                   "zet daarvoor ANTHROPIC_API_KEY in het .env-bestand.")
        return

    sleutel = f"adviezen_samenvatting_{profile.key}"
    with st.expander("Bestuurlijke samenvatting"):
        st.caption("Vat de signalen hierboven samen in doorlopende tekst. Er worden geen nieuwe "
                   "getallen berekend — alleen de signalen uit deze analyse gaan mee.")
        if st.button("Bestuurlijke samenvatting genereren", key=f"btn_{sleutel}"):
            with st.spinner("Bezig met samenvatten…"):
                _samenvatting(data, signalen, sleutel)
        if st.session_state.get(sleutel):
            st.markdown(st.session_state[sleutel])


# ── entrypoint ───────────────────────────────────────────────────────────────
def render(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    if guard(data, "projecten"):
        return

    horizon, streef, eff, _ = _rekenbasis(data, profile, opts)
    st.caption(f"Signalen automatisch afgeleid uit de gekoppelde bronnen — {data.meta.bron_label}. "
               f"Je ziet het signaal, de getallen erachter en de vraag die eronder ligt; de keuze "
               f"blijft bij jou.")

    basis = [f"horizon {horizon} weken", f"streefbezetting {streef}%"]
    if data.heeft("capaciteit"):
        basis.append("capaciteit seizoensgecorrigeerd" if opts.get("seizoen")
                     else "bruto contracturen (geen seizoenscorrectie)")
    if opts.get("efficiency"):
        basis.append(f"efficiency-factor {int(round(eff * 100))}%")
    st.caption("Rekenbasis: " + " · ".join(basis) + ".")

    signalen = _signalen(data, profile, opts)
    for kind, titel, body in signalen:
        advice_card(kind, titel, body)

    _llm_blok(data, profile, signalen)
    caveat_box(data)
