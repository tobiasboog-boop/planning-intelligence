"""
source_megens.py — Bron-adapter: Megens (Syntess-klant 1142) → canoniek contract.

Gebruikt de geverifieerde queries uit megens_source.py en mapt die op PlanningData.
Read-only via de Notifica Data API. Zie DATASPEC.md voor de datamodel-beslissingen.
"""
from __future__ import annotations
import pandas as pd

import megens_source as ms
import seasonality as sn
from contract import PlanningData, SourceMeta, leeg

BRONNEN = [
    ("Benodigde / begrote uren", 'maatwerk."Begrotingsuren" + "Begrotinguren per werkdag"',
     "vraag per project per week (methode 2)"),
    ("Beschikbaarheid / capaciteit", 'planning."Geplande en contracturen medewerkers"',
     "contracturen per medewerker per week"),
    ("Werkelijk bestede uren", 'uren."Geboekte Uren"', "geboekt per project (definitief)"),
    ("Teams & medewerkers", 'stam."Afdelingen" + stam."Medewerkers"',
     "team-mapping + intern/extern"),
    ("Calculatie", 'projecten."Calculatieregels"', "gecalculeerde uren per project"),
]

CAVEATS = [
    "Syntess bevat bij Megens **geen verzuim- of verlofregistratie** "
    "(`uren.\"Medewerkers verzuim\"` is leeg) en contracturen negeren feestdagen. "
    "Daarom wordt de capaciteit modelmatig gecorrigeerd (zie seizoenscorrectie).",
    "De vraag is het **nog niet ingeplande** werk uit de werkvoorbereiding "
    "(44 open regels, methode 2), niet het totale werkpakket — de planregel-kolommen "
    "zijn in de bronview nog niet gevuld.",
]


def load(params: sn.SeasonParams | None = None, seizoen: bool = True) -> PlanningData:
    c = ms.get_client()

    # ── ruwe frames via de geverifieerde queries ─────────────────────────────
    demand = ms.fetch_demand_per_week(c)
    cap = ms.fetch_capacity_per_week(c)
    mdw = ms.fetch_medewerkers(c)
    dim = ms.fetch_projects_dim(c)
    budget = ms.fetch_budget_per_project(c)
    booked = ms.fetch_booked_per_project(c)
    calc = ms.fetch_calculatie_per_project(c)
    overzicht = ms.build_project_overview(dim, budget, booked, calc)

    # ── vraag ────────────────────────────────────────────────────────────────
    vraag = demand.rename(columns={"afdeling": "team", "vraag_uren": "uren"})
    vraag = vraag[["project_key", "project", "team", "week_start", "uren"]]

    # ── capaciteit (+ seizoenscorrectie) ─────────────────────────────────────
    capaciteit = cap.rename(columns={"afdeling": "team", "capaciteit_uren": "contract_uren"})
    capaciteit = capaciteit[["team", "week_start", "contract_uren", "n_mw"]]
    if seizoen:
        capaciteit = sn.pas_toe(capaciteit, params)
    else:
        capaciteit["beschikbaar_uren"] = capaciteit["contract_uren"]
        capaciteit["season_factor"] = 1.0

    # ── projecten ────────────────────────────────────────────────────────────
    projecten = overzicht.rename(columns={
        "afdeling": "team", "begrotingsuren": "begroot", "calculatie_uren": "calculatie",
    })
    for k in ("project_key", "project", "team", "fase", "projectleider", "begroot",
              "geboekt", "nog_te_plannen", "overschrijding", "calculatie", "pct_gereed"):
        if k not in projecten.columns:
            projecten[k] = None
    projecten = projecten[["project_key", "project", "team", "fase", "projectleider",
                           "begroot", "geboekt", "nog_te_plannen", "overschrijding",
                           "calculatie", "pct_gereed"]]

    # ── medewerkers ──────────────────────────────────────────────────────────
    medewerkers = mdw.rename(columns={"afdeling": "team", "contract_uren": "contract_uren"})
    medewerkers["in_planning"] = medewerkers["projectplanning"].eq("J")
    medewerkers = medewerkers[["mdw_key", "medewerker", "team", "type",
                               "contract_uren", "in_planning"]]

    meta = SourceMeta(
        klant="Megens",
        bron_label="Live Syntess-data (klant 1142) via de Notifica Data API",
        blokken={"vraag": True, "capaciteit": True, "realisatie": True,
                 "projecten": True, "medewerkers": True, "prognose": False},
        caveats=CAVEATS,
        bronnen=BRONNEN,
        seizoen_toegepast=seizoen,
        seizoen_uitleg=sn.toelichting(params) if seizoen else "",
    )

    return PlanningData(
        vraag=vraag,
        capaciteit=capaciteit,
        realisatie=leeg("realisatie"),   # per project op aanvraag (drilldown)
        projecten=projecten,
        medewerkers=medewerkers,
        prognose=leeg("prognose"),       # bronkolommen nog NULL bij Megens
        meta=meta,
    )


def realisatie_per_week(project_key: int) -> pd.DataFrame:
    """Geboekte uren per week voor één project (voor de drilldown)."""
    df = ms.fetch_booked_per_week_project(ms.get_client(), int(project_key))
    if not len(df):
        return leeg("realisatie")
    df = df.rename(columns={"geboekt": "uren"})
    df["project_key"] = int(project_key)
    return df[["project_key", "week_start", "uren"]]
