"""
data_gen.py — Deterministische synthetische data per klant-profiel.

Dit is de PROTOTYPE-connector. Elk building block wordt hier gevuld met
realistische, reproduceerbare demo-data (vaste seed → zelfde plaatje bij elke
pitch). In productie vervangt een echte connector (Syntess / U-Serve /
Invoer-app / Notifica Data API) deze module — de rest van de tool blijft gelijk.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import numpy as np
import pandas as pd

from config import ClientProfile

# Maandag van de eerste planningsweek (vast anker → stabiele demo).
ANCHOR = date(2026, 7, 27)

TEAM_POOL = [
    ("Elektra", 0.82), ("Werktuigbouw", 0.80), ("Service & Onderhoud", 0.75),
    ("Data & Netwerken", 0.88), ("Beveiliging", 0.85), ("Duurzaam / Zon", 0.83),
]
VOORNAMEN = ["Jan", "Kees", "Ahmed", "Pieter", "Youssef", "Bart", "Dennis", "Rick",
             "Marco", "Sander", "Tim", "Wesley", "Ruben", "Joris", "Hakan", "Milan",
             "Dave", "Erik", "Ferry", "Gijs", "Hidde", "Ivo", "Jesse", "Koen",
             "Lars", "Mo", "Niels", "Onno", "Peer", "Quinten", "Roel", "Stef",
             "Teun", "Ugur", "Vince", "Wouter", "Xander", "Yorick", "Zeb", "Bram",
             "Cor", "Daan", "Emre", "Freek", "Guus", "Henk", "Ismail", "Jelle",
             "Kevin", "Luuk", "Mika", "Nout", "Otis", "Pim", "Rens", "Sem",
             "Thijs", "Vince2"]
PROJ_TYPES = ["Nieuwbouw", "Renovatie", "Utiliteit", "Onderhoudscontract",
              "Woningbouw", "Datacenter", "Zonnepark", "Verduurzaming",
              "Laadinfra", "Beveiligingsinstallatie"]
PROJ_PLAATS = ["Eindhoven", "Tilburg", "Breda", "Venlo", "Helmond", "Weert",
               "Roermond", "Den Bosch", "Nijmegen", "Uden", "Boxtel", "Oss",
               "Valkenswaard", "Geldrop", "Veldhoven", "Best", "Waalre"]


@dataclass
class DataBundle:
    teams: pd.DataFrame
    medewerkers: pd.DataFrame
    beschikbaarheid_mdw: pd.DataFrame       # BB2 raw (per medewerker)
    beschikbaarheid_week: pd.DataFrame      # BB2 afgeleid (per mdw per week)
    projecten: pd.DataFrame                 # BB1
    prognose: pd.DataFrame                  # BB3
    demand_week: pd.DataFrame               # afgeleid (per project per week)
    weeks: list[date]
    week_labels: list[str]


def _weeks(n: int) -> tuple[list[date], list[str]]:
    ws = [ANCHOR + timedelta(weeks=i) for i in range(n)]
    labels = [f"wk {w.isocalendar().week}" for w in ws]
    return ws, labels


def generate(profile: ClientProfile) -> DataBundle:
    rng = np.random.default_rng(profile.seed)
    H = profile.horizon_weeks
    weeks, week_labels = _weeks(H)

    # ── Teams (BB5) ────────────────────────────────────────────────────────
    pool = TEAM_POOL[: profile.n_teams]
    teams = pd.DataFrame(
        {"team": [t[0] for t in pool],
         "efficiency": [t[1] for t in pool]}
    )

    # ── Medewerkers (BB4 toewijzing + identiteit) ───────────────────────────
    n = profile.n_medewerkers
    names = list(VOORNAMEN)
    rng.shuffle(names)
    naam = [f"{names[i % len(names)]} {chr(65 + (i // len(names)))}." for i in range(n)]
    team = rng.choice(teams["team"], size=n)
    intern_extern = np.where(rng.random(n) < 0.18, "Extern", "Intern")
    contract = rng.choice([32, 36, 38, 40], size=n, p=[0.10, 0.30, 0.15, 0.45])
    mdw = pd.DataFrame({
        "medewerker_id": [f"M{100 + i}" for i in range(n)],
        "medewerker": naam,
        "team": team,
        "intern_extern": intern_extern,
        "contracturen": contract,        # per week
        "start": ANCHOR - timedelta(weeks=int(rng.integers(20, 300))),
        "einddatum": pd.NaT,
    })

    # ── Beschikbaarheid per medewerker per week (BB2 afgeleid) ──────────────
    rows = []
    for _, m in mdw.iterrows():
        base = m["contracturen"]
        # jaarlijkse verlof-/ziekte-/opleidingskans per week
        for i, w in enumerate(weeks):
            verlof = ziekte = opleiding = feest = 0.0
            r = rng.random()
            if r < 0.09:                       # verlofweek
                verlof = base * rng.choice([0.5, 1.0])
            if rng.random() < 0.05:            # ziek
                ziekte = base * rng.choice([0.4, 1.0])
            if rng.random() < 0.06:            # opleiding
                opleiding = min(base, rng.choice([4, 8]))
            if w.isocalendar().week in (52, 1, 17):  # feestdagen/kerst/koningsdag
                feest = rng.choice([4, 8])
            afwezig = min(base, verlof + ziekte + opleiding + feest)
            rows.append({
                "medewerker_id": m["medewerker_id"], "medewerker": m["medewerker"],
                "team": m["team"], "intern_extern": m["intern_extern"],
                "week_idx": i, "week_start": w, "contracturen": base,
                "verlof": verlof, "ziekte": ziekte, "opleiding": opleiding,
                "feestdag": feest, "beschikbaar": max(0.0, base - afwezig),
            })
    besch_week = pd.DataFrame(rows)

    besch_mdw = (besch_week.groupby(
        ["medewerker_id", "medewerker", "team", "intern_extern", "contracturen"],
        as_index=False)
        .agg(verlof=("verlof", "sum"), ziekte=("ziekte", "sum"),
             opleiding=("opleiding", "sum"), feestdag=("feestdag", "sum"),
             beschikbaar=("beschikbaar", "sum")))
    besch_mdw["bruto_horizon"] = besch_mdw["contracturen"] * H
    besch_mdw["start"] = ANCHOR - timedelta(weeks=60)
    besch_mdw["einddatum"] = pd.NaT

    # Effectieve capaciteit per team per week (voor het tunen van de vraag).
    eff_map = dict(zip(teams["team"], teams["efficiency"]))
    besch_week["effectief"] = besch_week.apply(
        lambda r: r["beschikbaar"] * eff_map.get(r["team"], profile.default_efficiency),
        axis=1)
    cap_team_week = (besch_week.groupby(["team", "week_idx"])["effectief"]
                     .sum().unstack(fill_value=0.0))
    cap_team_total = cap_team_week.sum(axis=1)   # per team over horizon

    # ── Projecten (BB1) + Prognose (BB3) ────────────────────────────────────
    P = profile.n_projecten
    proj_rows, prog_rows, demand_rows = [], [], []
    team_list = list(teams["team"])
    # richt totale vraag op ~0.98x capaciteit met een piek halverwege
    total_cap = float(cap_team_total.sum())
    target_demand = total_cap * 0.98

    # eerst voorlopige groottes bepalen, daarna schalen
    raw = rng.gamma(2.2, 1.0, size=P)
    raw = raw / raw.sum() * target_demand
    for j in range(P):
        pteam = team_list[j % len(team_list)]
        ptype = PROJ_TYPES[int(rng.integers(len(PROJ_TYPES)))]
        plaats = PROJ_PLAATS[int(rng.integers(len(PROJ_PLAATS)))]
        calc_uren = float(max(80, raw[j]))

        # start ergens tussen 10 weken terug en 8 weken vooruit
        start_wk = int(rng.integers(-10, 9))
        duur = int(np.clip(rng.normal(H * 0.45, H * 0.18), 4, H + 6))
        eind_wk = start_wk + duur
        cs = ANCHOR + timedelta(weeks=start_wk)
        ce = ANCHOR + timedelta(weeks=eind_wk)

        # voortgang: hoeveel van de looptijd is verstreken op wk0
        if start_wk >= 0:
            progress = 0.0
        else:
            progress = float(np.clip(-start_wk / max(duur, 1), 0, 0.98))

        # werkelijk bestede uren — soms al meer besteed dan pro-rata (uitloop)
        overspend = rng.normal(1.0, 0.14)
        werkelijk = float(max(0.0, calc_uren * progress * max(0.4, overspend)))

        # prognose resterend: kale rest + risico-opslag voor sommige projecten
        rest_kaal = max(0.0, calc_uren - werkelijk)
        risico = rng.normal(1.05, 0.22)
        prog_rest = float(max(0.0, rest_kaal * max(0.5, risico)))

        # vertraging: sommige projecten schuiven eind op
        vertraging = int(rng.choice([0, 0, 0, 1, 2, 3, 4], p=[.34, .2, .14, .12, .08, .07, .05]))
        prog_eind = ce + timedelta(weeks=vertraging)
        opm_opts = ["Loopt volgens plan.", "Meerwerk in behandeling.",
                    "Wacht op levering materiaal.", "Krappe bezetting komende weken.",
                    "Klant heeft scope uitgebreid.", "Onderaannemer ingepland.",
                    "Revisie tekening nodig."]
        opm = opm_opts[int(rng.integers(len(opm_opts)))]

        proj_rows.append({
            "project_id": f"P{2600 + j}",
            "project": f"{ptype} {plaats}",
            "team": pteam, "type": ptype,
            "calc_start": cs, "calc_eind": ce,
            "calc_uren": round(calc_uren, 1),
            "werkelijk_uren": round(werkelijk, 1),
            "calc_start_wk": start_wk, "calc_eind_wk": eind_wk,
        })
        prog_rows.append({
            "project_id": f"P{2600 + j}", "project": f"{ptype} {plaats}",
            "prog_start": max(cs, ANCHOR), "prog_eind": prog_eind,
            "prog_resterend": round(prog_rest, 1),
            "vertraging_wk": vertraging, "opmerking": opm,
        })

        # ── Weekvraag (afgeleid): resterend werk spreiden over toekomst ──────
        active = [i for i, w in enumerate(weeks)
                  if cs <= w <= prog_eind]
        active = [i for i in active if i >= 0]
        if prog_rest > 0 and active:
            # licht piekprofiel: iets meer in het midden van de looptijd
            k = len(active)
            wgt = np.array([1.0 + 0.5 * np.sin(np.pi * (t + 0.5) / k) for t in range(k)])
            wgt = wgt / wgt.sum()
            for pos, i in enumerate(active):
                demand_rows.append({
                    "project_id": f"P{2600 + j}", "project": f"{ptype} {plaats}",
                    "team": pteam, "week_idx": i, "week_start": weeks[i],
                    "benodigde_uren": float(prog_rest * wgt[pos]),
                })

    projecten = pd.DataFrame(proj_rows)
    prognose = pd.DataFrame(prog_rows)
    demand_week = pd.DataFrame(demand_rows)

    return DataBundle(
        teams=teams, medewerkers=mdw,
        beschikbaarheid_mdw=besch_mdw, beschikbaarheid_week=besch_week,
        projecten=projecten, prognose=prognose, demand_week=demand_week,
        weeks=weeks, week_labels=week_labels,
    )
