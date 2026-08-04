"""
config.py — Building-block registry + per-klant profielen.

Dit bestand is het HART van de schaalbaarheid: de hele tool wordt gedreven
door configuratie. Building blocks en views staan hier als registry; per
klant staat een profiel dat blocks/views aan- of uitzet en instellingen,
terminologie en databronnen configureert.

Nieuwe klant erbij = één profiel toevoegen. Geen code aanpassen.
Building block koppelen aan echte bron = één connector-key wijzigen.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

# ── Merk / kleuren ────────────────────────────────────────────────────────
NAVY = "#16136F"
NAVY2 = "#3636A2"
NAVY_LIGHT = "#A5B4FC"
GOLD = "#FBBA00"
GREEN = "#22C55E"
AMBER = "#F59E0B"
RED = "#EF4444"
GREY = "#E5E7EB"


# ── Building blocks (databronnen) ──────────────────────────────────────────
# Elk building block is een los, herbruikbaar databrok. Het declareert waar
# de data vandaan komt (source), welke velden het levert en of het per klant
# aan/uit staat. De prototype-connector = synthetische data; in productie
# wisselt alleen `connector` naar Syntess / U-Serve / Invoer-app / Data-API.

@dataclass(frozen=True)
class Block:
    key: str
    label: str
    source: str                 # bronsysteem (waar de data in productie vandaan komt)
    connector: str              # actieve connector (prototype: "synthetic")
    icon: str
    fields: list[str]
    description: str


BLOCKS: dict[str, Block] = {
    "demand": Block(
        key="demand",
        label="Benodigde uren",
        source="Syntess (ERP)",
        connector="synthetic",
        icon="hammer",
        fields=["Project", "Calculatie startdatum", "Calculatie einddatum",
                "Calculatie uren", "Werkelijk bestede uren"],
        description="De vraagkant: per project de gecalculeerde uren, planning en "
                    "wat er al werkelijk op geboekt is.",
    ),
    "availability": Block(
        key="availability",
        label="Beschikbaarheid uren",
        source="U-Serve (HR)",
        connector="synthetic",
        icon="user",
        fields=["Medewerker", "Start", "Einddatum", "Contracturen",
                "Verlof / ziekte / opleiding / feestdagen"],
        description="De aanbodkant: per medewerker de netto beschikbare uren na "
                    "verlof, ziekte, opleiding en feestdagen.",
    ),
    "forecast": Block(
        key="forecast",
        label="Prognose project",
        source="Invoer-applicatie",
        connector="synthetic",
        icon="target",
        fields=["Project", "Prognose startdatum", "Prognose einddatum",
                "Prognose resterende uren", "Opmerking"],
        description="Menselijke bijsturing op de kale calculatie: de verwachte "
                    "resterende uren en (her)planning per project.",
    ),
    "team_alloc": Block(
        key="team_alloc",
        label="Toewijzing capaciteit teams",
        source="Invoer-applicatie",
        connector="synthetic",
        icon="users",
        fields=["Medewerker", "Intern / extern", "Team", "Startdatum", "Einddatum"],
        description="Wie zit wanneer in welk team, en of het eigen personeel of "
                    "ingehuurd (extern) is.",
    ),
    "team_master": Block(
        key="team_master",
        label="Stamgegevens teams",
        source="Invoer-applicatie",
        connector="synthetic",
        icon="layers",
        fields=["Team", "Efficiency-factor"],
        description="Per team de efficiency-factor waarmee bruto uren naar "
                    "effectief inzetbare uren worden vertaald.",
    ),
}


# ── Views (dashboards) ─────────────────────────────────────────────────────
# Elke view declareert welke building blocks hij minimaal nodig heeft. Staat
# een benodigd block uit, dan degradeert de view netjes (melding i.p.v. crash).

@dataclass(frozen=True)
class View:
    key: str
    label: str
    icon: str
    requires: list[str]         # building-block keys die nodig zijn
    optional: list[str] = field(default_factory=list)


VIEWS: dict[str, View] = {
    "management": View(
        key="management", label="Management overzicht", icon="dashboard",
        requires=["demand", "availability"],
        optional=["forecast", "team_alloc", "team_master"],
    ),
    "team": View(
        key="team", label="Team / medewerker", icon="users",
        requires=["availability"],
        optional=["team_alloc", "team_master", "demand"],
    ),
    "project": View(
        key="project", label="Project-analyse", icon="clipboard",
        requires=["demand"],
        optional=["forecast"],
    ),
    "ai": View(
        key="ai", label="AI-adviezen", icon="lightning",
        requires=["demand", "availability"],
        optional=["forecast", "team_alloc", "team_master"],
    ),
}


# ── Klant-profielen ────────────────────────────────────────────────────────
# Eén profiel per klant. Zet building blocks en views aan/uit en configureert
# instellingen. Dit is alles wat je aanraakt om een nieuwe klant uit te rollen.

@dataclass
class ClientProfile:
    key: str
    name: str
    tagline: str
    blocks: dict[str, bool]                 # welke building blocks aan
    views: dict[str, bool]                  # welke views zichtbaar
    horizon_weeks: int = 26
    default_efficiency: float = 0.85        # gebruikt als team_master uit staat
    target_utilization: float = 0.90        # streefbezetting voor signalering
    seed: int = 42                          # reproduceerbare synthetische data
    n_teams: int = 5
    n_medewerkers: int = 42
    n_projecten: int = 28
    data_mode: str = "synthetic"            # "synthetic" (demo) of "megens" (echte Data API)


CLIENTS: dict[str, ClientProfile] = {
    # Megens — lanceerklant op ECHTE Syntess-data (klant 1142) via de Notifica Data API.
    "megens": ClientProfile(
        key="megens", name="Megens (echte data)",
        tagline="Live op Syntess-data (klant 1142) via de Notifica Data API",
        blocks={"demand": True, "availability": True, "forecast": True,
                "team_alloc": True, "team_master": True},
        views={"management": True, "team": True, "project": True, "ai": True},
        data_mode="megens",
    ),
    # ERCO — de pitch-klant: alle building blocks + alle views aan.
    "erco": ClientProfile(
        key="erco", name="ERCO",
        tagline="Volledige inrichting — alle building blocks actief",
        blocks={"demand": True, "availability": True, "forecast": True,
                "team_alloc": True, "team_master": True},
        views={"management": True, "team": True, "project": True, "ai": True},
        horizon_weeks=26, default_efficiency=0.85, target_utilization=0.90,
        seed=7, n_teams=6, n_medewerkers=58, n_projecten=34,
    ),
    # Voorbeeldklant B — start klein: alleen vraag + projectvoortgang.
    "demand_only": ClientProfile(
        key="demand_only", name="Voorbeeld: Projectvoortgang-light",
        tagline="Instapmodel — alleen vraag & project-analyse",
        blocks={"demand": True, "availability": False, "forecast": True,
                "team_alloc": False, "team_master": False},
        views={"management": False, "team": False, "project": True, "ai": False},
        horizon_weeks=20, seed=13, n_teams=3, n_medewerkers=22, n_projecten=18,
    ),
    # Voorbeeldklant C — capaciteitssturing zonder ERP-koppeling.
    "capacity_light": ClientProfile(
        key="capacity_light", name="Voorbeeld: Capaciteitssturing",
        tagline="Zonder ERP-koppeling — capaciteit & teams centraal",
        blocks={"demand": True, "availability": True, "forecast": False,
                "team_alloc": True, "team_master": True},
        views={"management": True, "team": True, "project": False, "ai": True},
        horizon_weeks=13, seed=21, n_teams=4, n_medewerkers=30, n_projecten=20,
    ),
}

DEFAULT_CLIENT = "megens"
