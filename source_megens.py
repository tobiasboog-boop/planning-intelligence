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
    ("Beschikbaarheid / capaciteit", 'planning."Geplande en contracturen medewerkers ATPlanning"',
     "contracturen, ingeplande en ongeplande uren per medewerker per week"),
    ("Werkelijk bestede uren", 'uren."Geboekte Uren"', "geboekt per project (definitief)"),
    ("Teams & medewerkers", 'stam."Afdelingen" + stam."Medewerkers"',
     "team-mapping + intern/extern"),
    ("Calculatie", 'projecten."Calculatieregels"', "gecalculeerde uren per project"),
]

CAVEATS = [
    "**De planning zit niet in de views die de Power BI-rapporten gebruiken.** In "
    "`planning.\"Geplande en contracturen medewerkers\"` stopt type Project/Werkbon in 2018 "
    "en in de SSM-variant ontbreken die regels volledig; daardoor is *Ongepland* daar "
    "altijd gelijk aan de contracturen en lijkt er niets ingepland. Deze tool leest "
    "`… ATPlanning`, waar de planning wél actueel in staat. Gemeld aan Mark en Dolf.",
    "**De planning dooft uit met de afstand.** Gemiddeld is 35% van de capaciteit belegd in "
    "de eerste 8 weken, 14% in week 9–26 en 2% daarna. Vrije ruimte verder vooruit is dus "
    "een bovengrens: er is niets vastgelegd, wat niet hetzelfde is als niets te doen.",
    "**Verlof wordt vooruit alleen geregistreerd als het is aangevraagd** — dichtbij vrijwel "
    "compleet, verderop leeg. De seizoenscorrectie vult daarom alleen het verschil aan "
    "tussen wat te verwachten is en wat al vastligt, afgekapt op nul. Nooit dubbel tellen.",
    "Het openstaande werk is de selectie die het rapport *Begrotingsuren per project* "
    "zelf toont (montagetaken, actuele hoofdprojecten in fase Opdracht, einddatum in de "
    "toekomst). Ons getal wijkt 3,9% af van Syntess' eigen \"Te plannen\".",
    "Onderhoud dat al als werkbon is ingepland zit zowel in *Onderhoud open* als in "
    "*Al ingepland*. De rapporten hebben geen gemeenschappelijke sleutel, dus dat is niet "
    "weg te rekenen; de omvang van die maximale dubbeltelling staat in de verantwoording.",
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

    # ── vraag: projectwerk + onderhoud, elk met zijn eigen rapportdefinitie ──
    vraag = demand.rename(columns={"afdeling": "team", "vraag_uren": "uren"})
    vraag = vraag[["project_key", "project", "team", "week_start", "uren"]].copy()
    vraag["soort"] = "Projecten"

    # De dagspreiding verdeelt de **volledige begroting** over de werkdagen (37.450 u), niet
    # het nog te plannen deel. Daarin zit dus ook de 2.496 u die al ingepland staat. Het
    # tijdpatroon van die spreiding is bruikbaar, het volume niet. Daarom per project
    # terugschalen naar Syntess' eigen measure "Te plannen" (= begroot - totaal gepland).
    # Uitkomst sluit daarmee exact aan op het rapport in plaats van 7% te hoog uit te komen.
    if len(budget) and "te_plannen_syntess" in budget.columns:
        b = budget.set_index("project_key")
        ratio = (pd.to_numeric(b["te_plannen_syntess"], errors="coerce")
                 / pd.to_numeric(b["begrotingsuren"], errors="coerce").replace(0, pd.NA))
        ratio = ratio.clip(lower=0, upper=1)
        vraag["uren"] = vraag["uren"] * vraag["project_key"].map(ratio).fillna(1.0).astype(float)

    onderhoud = ms.fetch_onderhoud_per_week(c)
    if len(onderhoud):
        oh = pd.DataFrame({
            "project_key": pd.NA, "project": "Onderhoudscontracten",
            "team": "Onderhoud (S&O)", "week_start": onderhoud["week_start"],
            "uren": onderhoud["uren"], "soort": "Onderhoud",
        })
        vraag = pd.concat([vraag, oh], ignore_index=True)

    # ── capaciteit (+ seizoenscorrectie) ─────────────────────────────────────
    capaciteit = cap.rename(columns={"afdeling": "team", "capaciteit_uren": "contract_uren"})
    capaciteit = capaciteit[["team", "week_start", "contract_uren", "ongepland_uren",
                             "ingepland_uren", "gepland_project_uren",
                             "gepland_werkbon_uren", "indirect_uren", "verlof_uren",
                             "n_mw", "n_dagen"]]
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

    # ── Gebruikt deze klant de planningsmodule? ──────────────────────────────
    # Zo ja, dan is 'Ongepland' het antwoord van het systeem zelf op "wat is nog vrij".
    # Zo nee, dan is Ongepland gelijk aan de contracturen en zegt het niets; dan valt de
    # tool terug op het realisatietempo. Zo werkt het model bij elke klant.
    vandaag = pd.Timestamp.today().normalize()
    vooruit_cap = capaciteit[pd.to_datetime(capaciteit["week_start"]) >= vandaag]
    ingepland_vooruit = float(vooruit_cap["ingepland_uren"].sum()) if len(vooruit_cap) else 0.0
    contract_vooruit = float(vooruit_cap["contract_uren"].sum()) if len(vooruit_cap) else 0.0
    aandeel = (ingepland_vooruit / contract_vooruit) if contract_vooruit else 0.0
    if aandeel >= 0.02:
        modus = "planning"
        modus_uitleg = (
            f"Deze klant gebruikt de planningsmodule: vooruit staat er "
            f"{ingepland_vooruit:,.0f} uur ingepland ({aandeel*100:.0f}% van de contracturen). "
            f"De vrije ruimte komt daarom uit Syntess zelf (type Ongepland), niet uit een model."
        ).replace(",", ".")
    else:
        modus = "tempo"
        modus_uitleg = (
            "Deze klant legt vooruit (vrijwel) geen planning vast in Syntess. Ongepland is dan "
            "gelijk aan de contracturen en zegt niets over vrije ruimte. De tool valt daarom "
            "terug op het werkelijke realisatietempo: wat er per week feitelijk doorheen gaat."
        )

    meta = SourceMeta(
        klant="Megens",
        bron_label="Live Syntess-data (klant 1142) via de Notifica Data API",
        blokken={"vraag": True, "capaciteit": True, "realisatie": True,
                 "projecten": True, "medewerkers": True, "prognose": False},
        caveats=CAVEATS,
        bronnen=BRONNEN,
        seizoen_toegepast=seizoen,
        seizoen_uitleg=sn.toelichting(params) if seizoen else "",
        capaciteit_modus=modus,
        capaciteit_uitleg=modus_uitleg,
    )

    return PlanningData(
        vraag=vraag,
        capaciteit=capaciteit,
        realisatie=leeg("realisatie"),   # per project op aanvraag (drilldown)
        projecten=projecten,
        medewerkers=medewerkers,
        prognose=leeg("prognose"),       # niet vooruit gevuld bij Megens
        meta=meta,
        tempo=ms.fetch_tempo_per_week(c),
    )


def realisatie_per_week(project_key: int) -> pd.DataFrame:
    """Geboekte uren per week voor één project (voor de drilldown)."""
    df = ms.fetch_booked_per_week_project(ms.get_client(), int(project_key))
    if not len(df):
        return leeg("realisatie")
    df = df.rename(columns={"geboekt": "uren"})
    df["project_key"] = int(project_key)
    return df[["project_key", "week_start", "uren"]]
