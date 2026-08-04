"""
source_synthetic.py — Bron-adapter: synthetische demo-data → canoniek contract.

Levert exact dezelfde frames als `source_megens.py`, maar volledig gegenereerd.
Daarmee kun je de configuratie (building blocks aan/uit) demonstreren zonder
klantdata, en werkt elke analyse ongewijzigd op beide bronnen.

    profile (config.ClientProfile)  ──►  load()  ──►  contract.PlanningData

Kenmerken van de generator:
  • Reproduceerbaar: `profile.seed` bepaalt alles (zelfde seed = zelfde plaatje).
  • Realistische spanning: een piekperiode waarin de vraag de capaciteit
    overschrijdt (deels doordat ingeleende krachten juist dán aflopen) en een
    handvol projecten met een overschrijding (geboekt > begroot).
  • Consistent: `projecten.nog_te_plannen` is exact de som van `vraag` per
    project, en `projecten.geboekt` exact de som van `realisatie` per project.
  • Eerlijk: de vraag is het **nog in te plannen werk** binnen de horizon,
    capaciteit is **bruto** tenzij de seizoenscorrectie aan staat.

Deze module doet GEEN eigen aannames over klantkolommen; het contract in
`contract.SCHEMA` is de enige waarheid.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import seasonality as sn
from contract import PlanningData, SourceMeta, leeg

# ── Naamgeving uit de installatiebranche ─────────────────────────────────────
TEAM_POOL = [
    "Elektrotechniek", "Werktuigbouw", "Service & Onderhoud",
    "Data & Netwerken", "Beveiliging", "Duurzaam / Zon",
]

VOORNAMEN = [
    "Jan", "Kees", "Ahmed", "Pieter", "Youssef", "Bart", "Dennis", "Rick",
    "Marco", "Sander", "Tim", "Wesley", "Ruben", "Joris", "Hakan", "Milan",
    "Dave", "Erik", "Ferry", "Gijs", "Hidde", "Ivo", "Jesse", "Koen",
    "Lars", "Mo", "Niels", "Peer", "Roel", "Stef", "Teun", "Ugur",
    "Vince", "Wouter", "Bram", "Daan", "Emre", "Freek", "Guus", "Jelle",
]
ACHTERNAMEN = [
    "de Vries", "Jansen", "van Dijk", "Bakker", "Visser", "Smit", "Meijer",
    "de Boer", "Mulder", "Bos", "Vos", "Peters", "Hendriks", "van Leeuwen",
    "Dekker", "Brouwer", "de Wit", "Dijkstra", "van den Berg", "Kok",
    "Jacobs", "Vermeulen", "van der Heijden", "Schouten", "van Beek",
    "Willems", "Kuipers", "Timmermans", "Verhoeven", "Maas",
]
PROJECTLEIDERS = [
    "Arjan Kuipers", "Ilse Verhoeven", "Ronald Smeets", "Fatima el Haddad",
    "Bas Wijnands", "Marloes Peeters",
]

PROJ_TYPES = [
    "Nieuwbouw", "Renovatie", "Utiliteitsbouw", "Onderhoudscontract",
    "Woningbouw", "Datacenter", "Zonnedak", "Verduurzaming",
    "Laadinfrastructuur", "Beveiligingsinstallatie",
]
PROJ_PLAATS = [
    "Eindhoven", "Tilburg", "Breda", "Venlo", "Helmond", "Weert",
    "Roermond", "Den Bosch", "Nijmegen", "Uden", "Boxtel", "Oss",
    "Valkenswaard", "Geldrop", "Veldhoven", "Best", "Waalre",
]

OPMERKINGEN_NEUTRAAL = [
    "Loopt volgens planning.",
    "Onderaannemer is ingepland.",
    "Materiaal is besteld en op tijd.",
    "Wekelijks afstemming met de klant.",
]
OPMERKINGEN_RISICO = [
    "Meerwerk in behandeling, nog niet begroot.",
    "Wacht op levering schakelmateriaal.",
    "Krappe bezetting in de piekweken.",
    "Klant heeft de scope uitgebreid.",
    "Revisietekening nodig voordat we verder kunnen.",
    "Uitloop door bouwkundige vertraging.",
]
# Projecten zonder in te plannen werk: alleen nog afronden.
OPMERKINGEN_AFROND = [
    "Werk is uitgevoerd, wacht op oplevering.",
    "Alleen nazorg en administratieve afronding.",
    "Opleverpunten zijn weggewerkt, klaar voor gereedmelding.",
]

# Werkdruk per team in de EERSTE weken: gemiddeld beslag op de bruto capaciteit
# over de kalibratieperiode. De eerste twee teams zitten structureel te vol.
# Bewust net onder 100% op bruto uren: zet je de seizoenscorrectie aan, dan
# wordt zichtbaar dat er in de vakantieperiode wél een tekort staat.
BELASTING = [1.06, 0.97, 0.86, 0.78, 0.92, 0.84]
KALIBRATIE_WEKEN = 12    # eerste weken waarop de werkdruk wordt afgestemd

HIST_WEKEN = 13          # weken realisatie vóór de planningshorizon (+ huidige week)
EXTERN_KANS = 0.18       # aandeel ingeleende krachten
UIT_PLANNING_KANS = 0.10 # aandeel medewerkers dat niet in de projectplanning zit

BRONNEN = [
    ("Benodigde / begrote uren", "synthetisch", "vraag per project per week (generator)"),
    ("Beschikbaarheid / capaciteit", "synthetisch", "contracturen per team per week"),
    ("Werkelijk bestede uren", "synthetisch", "geboekte uren per project per week"),
    ("Projectoverzicht", "synthetisch", "begroot, geboekt, nog te plannen, overschrijding"),
    ("Teams & medewerkers", "synthetisch", "team-indeling + intern/ingeleend"),
    ("Prognose project", "synthetisch", "verwacht resterend + verwachte einddatum"),
]


# ── Hulpjes ─────────────────────────────────────────────────────────────────
def _week_van_vandaag() -> pd.Timestamp:
    """Maandag van de huidige week — de planningshorizon begint 'nu'."""
    vandaag = pd.Timestamp.today().normalize()
    return vandaag - pd.Timedelta(days=int(vandaag.weekday()))


def _season_params(profile, params: sn.SeasonParams | None) -> sn.SeasonParams:
    """Neem de meegegeven parameters, of leid ze af uit het klantprofiel."""
    if params is not None:
        return params

    def frac(x, standaard):
        """Profielvelden staan in procenten (4.0 = 4%); een fractie (0.04) mag ook."""
        try:
            v = float(x)
        except (TypeError, ValueError):
            return standaard
        if v < 0:
            return standaard
        return v if v < 0.25 else v / 100.0

    return sn.SeasonParams(
        vakantiedagen=float(getattr(profile, "vakantiedagen", 25.0) or 25.0),
        adv_dagen=float(getattr(profile, "adv_dagen", 0.0) or 0.0),
        ziekte_pct=frac(getattr(profile, "ziekte_pct", 4.0), 0.04),
        opleiding_pct=frac(getattr(profile, "opleiding_pct", 1.0), 0.01),
        uren_per_dag=float(getattr(profile, "uren_per_dag", 8.0) or 8.0),
    )


def _blok_aan(profile, naam: str) -> bool:
    """Staat dit building block aan? Onbekend blok = aan (dan is er data)."""
    blokken = getattr(profile, "blocks", None) or {}
    return bool(blokken.get(naam, True))


def _teamnamen(n: int) -> list[str]:
    namen = list(TEAM_POOL[:n])
    ronde = 2
    while len(namen) < n:
        for basis in TEAM_POOL:
            namen.append(f"{basis} {ronde}")
            if len(namen) >= n:
                break
        ronde += 1
    return namen


def _leeg_capaciteit() -> pd.DataFrame:
    """Leeg capaciteitsframe inclusief season_factor (zoals de gevulde variant)."""
    df = leeg("capaciteit")
    df["season_factor"] = pd.Series(dtype="float64")
    return df


# ── Generatoren per frame ───────────────────────────────────────────────────
def _medewerkers(rng, teams: list[str], n: int, H: int,
                 piek_start: int, druk_teams: list[str]) -> pd.DataFrame:
    """Medewerkers met team, intern/extern en (intern) een actief-tot-week.

    Ingeleende krachten in de drukke teams lopen deels áf rond de piek — dat is
    precies de situatie die je vooruit wil zien.
    """
    combos = [(v, a) for v in VOORNAMEN for a in ACHTERNAMEN]
    keuze = rng.permutation(len(combos))[:n]

    # Elke ploeg houdt een vaste kern van 3 interne, inplanbare mensen; de kans op
    # inhuur bij de rest wordt gecompenseerd zodat het totaal ~EXTERN_KANS blijft.
    n_kern = min(n, 3 * len(teams))
    kans_extern = min(0.60, EXTERN_KANS * n / max(1, n - n_kern))

    per_team: dict[str, int] = {t: 0 for t in teams}
    rijen = []
    for i in range(n):
        team = teams[i % len(teams)]
        per_team[team] += 1
        kern = per_team[team] <= 3            # vaste kern: altijd intern én inplanbaar

        soort = "Intern" if kern or rng.random() >= kans_extern else "Extern"
        uren_week = float(rng.choice([24, 32, 36, 38, 40], p=[.05, .10, .25, .15, .45]))
        in_planning = True if kern else bool(rng.random() >= UIT_PLANNING_KANS)

        actief_tot = H - 1
        if soort == "Extern" and team in druk_teams and rng.random() < 0.60:
            actief_tot = int(min(H - 1, piek_start + 1))   # inhuur stopt in de piek
        elif soort == "Extern" and rng.random() < 0.25:
            actief_tot = int(rng.integers(max(2, H // 2), H))

        voornaam, achternaam = combos[int(keuze[i])]
        rijen.append({
            "mdw_key": 1000 + i,
            "medewerker": f"{voornaam} {achternaam}",
            "team": team,
            "type": soort,
            "_uren_week": uren_week,
            "in_planning": in_planning,
            "_actief_tot": actief_tot,
        })
    return pd.DataFrame(rijen)


def _capaciteit(mdw: pd.DataFrame, teams: list[str], weken: pd.DatetimeIndex) -> pd.DataFrame:
    """Contracturen + koppen per team per week (alleen wie in de planning zit)."""
    plan = mdw[mdw["in_planning"]]
    rijen = []
    for i, week in enumerate(weken):
        actief = plan[plan["_actief_tot"] >= i]
        som = actief.groupby("team")["_uren_week"].sum()
        koppen = actief.groupby("team")["_uren_week"].size()
        for team in teams:
            rijen.append({
                "team": team,
                "week_start": week,
                "contract_uren": float(som.get(team, 0.0)),
                "n_mw": int(koppen.get(team, 0)),
            })
    return pd.DataFrame(rijen)


def _week_gewichten(rng, start: int, duur: int, H: int, piek_start: int,
                    piek_len: int, boost: float, lopend: bool) -> dict[int, float]:
    """Verdeling van het nog in te plannen werk over de weken van één project.

    • lopend project → aflopend profiel (het zit al in productie, de eerste
      weken zijn het volst)
    • nieuw project  → bultprofiel (opstarten, doorwerken, afronden)

    Daarbovenop een piek-boost in de knelweken. Door daarna te normaliseren
    blijft het projecttotaal exact gelijk.
    """
    gewichten = {}
    for k in range(duur):
        i = start + k
        if i >= H:
            break
        frac = (k + 0.5) / max(duur, 1)
        if lopend:
            w = 1.0 - 0.45 * frac
        else:
            w = 0.55 + 0.60 * float(np.sin(np.pi * frac))
        w *= float(rng.uniform(0.88, 1.12))
        if piek_start <= i < piek_start + piek_len:
            w *= 1.0 + boost
        gewichten[i] = max(w, 0.02)
    totaal = sum(gewichten.values())
    if totaal <= 0:
        return {}
    return {i: w / totaal for i, w in gewichten.items()}


def _genereer(rng, teams, cap, weken, hist, n_proj, H, piek_start, piek_len, druk_teams):
    """Bouwt vraag, realisatie, projecten en prognose in één samenhangende set."""
    # bruto capaciteit per team per week (kalibratie is kalender-onafhankelijk)
    cap_team_week = (cap.pivot_table(index="team", columns="week_start",
                                     values="contract_uren", aggfunc="sum")
                     .reindex(columns=weken, fill_value=0.0).fillna(0.0))
    proj_team = [teams[j % len(teams)] for j in range(n_proj)]

    combos = [(t, p) for t in PROJ_TYPES for p in PROJ_PLAATS]
    naam_keuze = rng.permutation(len(combos))[: n_proj]

    # Levensfase: een paar projecten ronden af (bijna niets te plannen) en een
    # paar zitten in de nazorg (niets meer te plannen, wel geboekte uren).
    afronders = {j for j in range(n_proj) if j % 7 == 3}
    nazorg = {j for j in range(n_proj) if j % 13 == 5} if n_proj >= 12 else set()
    afronders -= nazorg

    # ── 1) Planning per project: ploeggrootte × weken bepaalt de omvang, zodat
    #       het weekbeslag altijd uitlegbaar blijft (geen 500 uur in één week
    #       voor een ploeg van drie).
    plan = []
    for j in range(n_proj):
        team = proj_team[j]
        soort, plaats = combos[int(naam_keuze[j])]
        lopend = j in afronders or j in nazorg or rng.random() < 0.55

        if j in afronders:
            start, duur = int(rng.integers(0, 2)), int(rng.integers(2, 5))
        elif lopend:
            # loopt al in productie → er moet deze of volgende week gewerkt worden
            start = int(rng.integers(0, 3))
            duur = int(np.clip(round(rng.normal(H * 0.50, H * 0.18)), 4, max(4, H - start)))
        else:
            # nog niet gestart: deels rond de drukke periode, deels verder vooruit
            if rng.random() < 0.45:
                start = int(np.clip(round(rng.normal(piek_start - 1, 2.5)), 1, max(1, H - 4)))
            else:
                start = int(rng.integers(1, max(2, H - 3)))
            duur = int(np.clip(round(rng.normal(H * 0.40, H * 0.15)), 3, max(3, H - start)))

        ploeg = int(rng.integers(2, 9))                      # 2 t/m 8 monteurs
        omvang_rest = ploeg * 36.0 * duur                    # nog te verrichten werk
        if j in nazorg:
            gereed = 1.0
        elif j in afronders:
            gereed = float(rng.uniform(0.85, 0.96))
        elif lopend:
            gereed = float(rng.uniform(0.15, 0.75))
        else:
            gereed = 0.0 if rng.random() < 0.55 else float(rng.uniform(0.01, 0.06))

        if gereed >= 1.0:
            omvang_rest = 0.0
            omvang_geboekt = ploeg * 36.0 * float(rng.integers(6, 16))
        else:
            omvang_geboekt = omvang_rest * gereed / (1.0 - gereed)

        boost = 0.60 if team in druk_teams else 0.20
        plan.append({
            "j": j, "key": 30001 + j, "naam": f"{soort} {plaats}", "team": team,
            "start": start, "duur": duur, "lopend": lopend,
            "gewichten": (_week_gewichten(rng, start, duur, H, piek_start, piek_len,
                                          boost, lopend) if omvang_rest > 0 else {}),
            "rest_ruw": omvang_rest, "geboekt_ruw": omvang_geboekt,
        })

    # ── 2) Per team ijken op de eerste weken: daar wordt écht gepland, dus daar
    #       moet het beslag realistisch zijn (BELASTING bepaalt wie te vol zit).
    #       Verder vooruit loopt de vraag vanzelf leeg — dat werk is nog niet
    #       vastgelegd. Ploeg-verhoudingen binnen een team blijven intact.
    K = int(min(KALIBRATIE_WEKEN, H))
    for t_idx, team in enumerate(teams):
        eigen = [p for p in plan if p["team"] == team]
        if not eigen:
            continue
        vraag_k = sum(p["rest_ruw"] * g
                      for p in eigen for i, g in p["gewichten"].items() if i < K)
        cap_k = float(cap_team_week.loc[team].iloc[:K].sum()) if team in cap_team_week.index else 0.0
        if vraag_k <= 0 or cap_k <= 0:
            continue
        factor = (cap_k * BELASTING[t_idx % len(BELASTING)]) / vraag_k
        for p in eigen:
            p["rest_ruw"] *= factor
            p["geboekt_ruw"] *= factor

    # ── 3) Weekvraag + historie uitschrijven ────────────────────────────────
    vraag_rijen, real_rijen, records = [], [], []
    for p in plan:
        key, naam, team = p["key"], p["naam"], p["team"]

        rest = 0.0
        laatste_week_idx = p["start"] if p["gewichten"] else 0
        for i, g in sorted(p["gewichten"].items()):
            uren = float(p["rest_ruw"] * g)
            if uren < 0.05:
                continue
            vraag_rijen.append({
                "project_key": key, "project": naam, "team": team,
                "week_start": weken[i], "uren": round(uren, 1),
            })
            rest += round(uren, 1)
            laatste_week_idx = max(laatste_week_idx, i)

        # Geboekte uren in de weken vóór (en in) de huidige week.
        geboekt = 0.0
        laatste_real = hist[-1]
        if p["geboekt_ruw"] > 1.0:
            k = int(rng.integers(3, len(hist) + 1))
            wk = hist[-k:]
            w = rng.gamma(3.0, 1.0, size=k)
            w[-1] *= 0.45                      # huidige week is nog niet vol geboekt
            w = w / w.sum()
            for pos, week in enumerate(wk):
                uren = float(p["geboekt_ruw"] * w[pos])
                if uren < 0.05:
                    continue
                real_rijen.append({"project_key": key, "week_start": week,
                                   "uren": round(uren, 1)})
                geboekt += round(uren, 1)
                laatste_real = week

        records.append({
            "project_key": key, "project": naam, "team": team,
            "_rest": rest, "_geboekt": geboekt,
            "_laatste_week": weken[min(laatste_week_idx, H - 1)],
            "_laatste_real": laatste_real,
            "_risico": float(rng.uniform(0.82, 1.55)),
            "_vertraging": int(rng.choice([0, 0, 0, 1, 2, 3, 5],
                                          p=[.34, .20, .14, .12, .08, .07, .05])),
        })

    # 4) Begroting, overschrijding en fase — met een handvol echte overschrijders.
    n_over = max(2, int(round(0.15 * n_proj)))
    kandidaten = sorted([r for r in records if r["_geboekt"] > 100],
                        key=lambda r: r["_geboekt"], reverse=True)
    overschrijders = {r["project_key"] for r in kandidaten[:n_over]}

    proj_rijen, prog_rijen = [], []
    for t_idx, r in enumerate(records):
        rest, geboekt = r["_rest"], r["_geboekt"]
        totaal_werk = rest + geboekt
        if r["project_key"] in overschrijders:
            begroot = geboekt * float(rng.uniform(0.72, 0.94))
        else:
            marge = float(np.clip(rng.normal(0.04, 0.10), -0.06, 0.30))
            begroot = totaal_werk * (1.0 + marge)
        begroot = float(max(40.0, round(begroot, 1)))
        overschrijding = float(max(0.0, round(geboekt - begroot, 1)))

        gereed = geboekt / totaal_werk if totaal_werk > 0 else 0.0
        if geboekt <= 0:
            fase = "Calculatie" if rng.random() < 0.35 else "Werkvoorbereiding"
        elif rest <= 0:
            fase = "Nazorg"
        elif gereed >= 0.88:
            fase = "Gereedmelden"
        elif gereed >= 0.05:
            fase = "In uitvoering"
        else:
            fase = "Werkvoorbereiding"

        # Projectleider hoort meestal bij een team, soms kruislings.
        pl = PROJECTLEIDERS[teams.index(r["team"]) % len(PROJECTLEIDERS)]
        if rng.random() < 0.20:
            pl = PROJECTLEIDERS[int(rng.integers(len(PROJECTLEIDERS)))]

        calculatie = 0.0 if rng.random() < 0.10 else round(begroot * float(rng.uniform(0.85, 1.15)), 1)

        # pct_gereed is handmatig onderhouden in het ERP → soms niet gevuld.
        if rng.random() < 0.15:
            pct_gereed = np.nan
        else:
            ruw = gereed * 100 + float(rng.normal(0, 6))
            pct_gereed = float(np.clip(round(ruw / 5.0) * 5.0, 0.0, 100.0))

        proj_rijen.append({
            "project_key": r["project_key"], "project": r["project"], "team": r["team"],
            "fase": fase, "projectleider": pl,
            "begroot": begroot, "geboekt": round(geboekt, 1),
            "nog_te_plannen": round(rest, 1), "overschrijding": overschrijding,
            "calculatie": calculatie, "pct_gereed": pct_gereed,
        })

        risico = r["_risico"]
        if rest <= 0:
            # Niets meer in te plannen: alleen nog opleveren/afronden. De verwachte
            # einddatum hangt dan aan de laatst geboekte week, niet aan de horizon.
            eind = r["_laatste_real"] + pd.Timedelta(days=4 + 7 * min(r["_vertraging"], 2))
            resterend = 0.0
            opm = OPMERKINGEN_AFROND[int(rng.integers(len(OPMERKINGEN_AFROND)))]
        else:
            # verwachte einddatum = vrijdag van de laatste geplande week + vertraging
            eind = r["_laatste_week"] + pd.Timedelta(days=4 + 7 * r["_vertraging"])
            resterend = round(rest * risico, 1)
            opm = (OPMERKINGEN_RISICO[int(rng.integers(len(OPMERKINGEN_RISICO)))]
                   if risico > 1.12 or r["_vertraging"] >= 2
                   else OPMERKINGEN_NEUTRAAL[int(rng.integers(len(OPMERKINGEN_NEUTRAAL)))])
        prog_rijen.append({
            "project_key": r["project_key"],
            "prognose_eind": eind,
            "resterend": resterend,
            "opmerking": opm,
        })

    vraag = pd.DataFrame(vraag_rijen, columns=["project_key", "project", "team",
                                               "week_start", "uren"])
    realisatie = pd.DataFrame(real_rijen, columns=["project_key", "week_start", "uren"])
    projecten = pd.DataFrame(proj_rijen)
    prognose = pd.DataFrame(prog_rijen)
    return vraag, realisatie, projecten, prognose


# ── Publieke API ────────────────────────────────────────────────────────────
def load(profile, params: sn.SeasonParams | None = None, seizoen: bool = True) -> PlanningData:
    """Genereer een volledige PlanningData-set volgens het klantprofiel.

    Blokken die in `profile.blocks` uit staan leveren een leeg frame, zodat de
    analyses netjes degraderen (precies zoals bij een klant zonder die bron).
    """
    rng = np.random.default_rng(int(getattr(profile, "seed", 42) or 42))
    H = int(max(6, getattr(profile, "horizon_weken", 26) or 26))
    n_teams = int(max(1, getattr(profile, "n_teams", 5) or 5))
    n_mdw = int(max(n_teams * 4, getattr(profile, "n_medewerkers", 40) or 40))
    n_proj = int(max(n_teams, getattr(profile, "n_projecten", 28) or 28))

    anchor = _week_van_vandaag()
    weken = pd.date_range(anchor, periods=H, freq="7D")
    hist = pd.date_range(anchor - pd.Timedelta(weeks=HIST_WEKEN),
                         periods=HIST_WEKEN + 1, freq="7D")

    teams = _teamnamen(n_teams)
    piek_start = int(np.clip(round(H * 0.30), 1, max(1, H - 4)))
    piek_len = 3 if H >= 12 else 2
    druk_teams = teams[: min(2, len(teams))]

    mdw = _medewerkers(rng, teams, n_mdw, H, piek_start, druk_teams)
    cap = _capaciteit(mdw, teams, weken)
    vraag, realisatie, projecten, prognose = _genereer(
        rng, teams, cap, weken, hist, n_proj, H, piek_start, piek_len, druk_teams)

    # ── seizoenscorrectie (rekenoptie) ──────────────────────────────────────
    sparams = _season_params(profile, params)
    seizoen_actief = bool(seizoen) and _blok_aan(profile, "seizoen")
    if seizoen_actief:
        cap = sn.pas_toe(cap, sparams)
    else:
        cap = cap.copy()
        cap["beschikbaar_uren"] = cap["contract_uren"]
        cap["season_factor"] = 1.0
    cap = cap[["team", "week_start", "contract_uren", "beschikbaar_uren",
               "n_mw", "season_factor"]]

    # ── medewerkers naar contractvorm (contract_uren = totaal over de horizon,
    #    zelfde definitie als de Megens-bron) ─────────────────────────────────
    medewerkers = mdw.copy()
    medewerkers["contract_uren"] = (medewerkers["_uren_week"]
                                    * (medewerkers["_actief_tot"] + 1)).round(1)
    medewerkers = medewerkers[["mdw_key", "medewerker", "team", "type",
                               "contract_uren", "in_planning"]]

    # ── blokken respecteren ─────────────────────────────────────────────────
    if not _blok_aan(profile, "vraag"):
        vraag = leeg("vraag")
    if not _blok_aan(profile, "capaciteit"):
        cap = _leeg_capaciteit()
    if not _blok_aan(profile, "realisatie"):
        realisatie = leeg("realisatie")
    if not _blok_aan(profile, "projecten"):
        projecten = leeg("projecten")
    if not _blok_aan(profile, "medewerkers"):
        medewerkers = leeg("medewerkers")
    if not _blok_aan(profile, "prognose"):
        prognose = leeg("prognose")

    caveats = [
        "Dit zijn **fictieve, gegenereerde cijfers** — geen klantdata. Ze dienen "
        "om de werking en de inrichting van de tool te laten zien; gebruik ze niet "
        "voor beslissingen.",
        f"De vraag is het **nog in te plannen werk** binnen de horizon van {H} weken, "
        "niet het totale werkpakket. Werk dat verder vooruit ligt zit er niet in.",
        "De capaciteit is **bruto** (contracturen per team per week). Zet de "
        "seizoenscorrectie aan om verlof, feestdagen, ziekte en opleiding mee te rekenen.",
        "`medewerkers.contract_uren` is het **totaal over de horizon** (dezelfde "
        "definitie als de Syntess-bron), niet het weekcontract.",
        "De generator bouwt bewust spanning in: een piekperiode waarin de vraag de "
        "capaciteit overschrijdt en enkele projecten met een overschrijding.",
    ]

    meta = SourceMeta(
        klant=str(getattr(profile, "name", "Voorbeeldklant")),
        bron_label="Synthetische demo-data (geen klantdata)",
        blokken=dict(getattr(profile, "blocks", {}) or {}),
        caveats=caveats,
        bronnen=BRONNEN,
        seizoen_toegepast=seizoen_actief,
        seizoen_uitleg=sn.toelichting(sparams) if seizoen_actief else "",
    )

    return PlanningData(
        vraag=vraag,
        capaciteit=cap,
        realisatie=realisatie,
        projecten=projecten,
        medewerkers=medewerkers,
        prognose=prognose,
        meta=meta,
    )
