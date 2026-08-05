"""
config.py — Building-block registry + klantprofielen + configuratiemodus.

Dit is het configuratiehart. De hele tool wordt hierdoor gedreven:

  • BLOCKS   — de canonieke building blocks (databronnen + rekenopties)
  • ANALYSES — de vaste set analyses; elke analyse declareert welke blokken hij nodig heeft
  • CLIENTS  — per klant één profiel: welke blokken aan, welke parameters

Nieuwe klant uitrollen = één profiel toevoegen + de bron koppelen. Geen code aanpassen.
De **configuratiemodus** (intern, niet met de klant delen) laat je dit live schakelen —
dat is de inrichtingssessie per klant.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# ── Merk / kleuren ────────────────────────────────────────────────────────
NAVY = "#16136F"
NAVY2 = "#3636A2"
NAVY_LIGHT = "#A5B4FC"
GOLD = "#FBBA00"
GREEN = "#22C55E"
AMBER = "#F59E0B"
RED = "#EF4444"
GREY = "#E5E7EB"


# ── Building blocks ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class Block:
    key: str
    label: str
    soort: str            # "databron" of "rekenoptie"
    bron: str             # waar het in productie vandaan komt
    uitleg: str


BLOCKS: dict[str, Block] = {
    # Databronnen (canonieke frames)
    "vraag": Block("vraag", "Benodigde / begrote uren", "databron", "ERP (Syntess)",
                   "Het werk dat nog verricht moet worden, uitgezet over de weken."),
    "capaciteit": Block("capaciteit", "Beschikbaarheid / capaciteit", "databron", "ERP of HR (U-Serve)",
                        "Contracturen per medewerker/team per week."),
    "realisatie": Block("realisatie", "Werkelijk bestede uren", "databron", "ERP (Syntess)",
                        "Geboekte uren per project — de realisatie."),
    "projecten": Block("projecten", "Projectoverzicht", "databron", "ERP (Syntess)",
                       "Per project: begroot, geboekt, nog te plannen, overschrijding."),
    "medewerkers": Block("medewerkers", "Teams & medewerkers", "databron", "ERP of HR",
                         "Team-indeling en intern/extern (ingeleend)."),
    "prognose": Block("prognose", "Prognose project", "databron", "Invoer-applicatie",
                      "Menselijke bijsturing: verwachte resterende uren en herplanning."),
    # Rekenopties
    "seizoen": Block("seizoen", "Seizoenscorrectie (productiviteitsfactor)", "rekenoptie", "Model",
                     "Rekent bruto contracturen om naar effectief beschikbare uren: verlof "
                     "(zomerpiek), feestdagen, ziekte en opleiding. Zelfde rekenwijze als de "
                     "Directe-urencalculator in het leerportaal."),
    "efficiency": Block("efficiency", "Efficiency-factor per team", "rekenoptie", "Invoer-applicatie",
                        "Extra correctie per team van beschikbare naar productief inzetbare uren."),
}


# ── Analyses ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Analysis:
    key: str
    label: str
    requires: list[str]
    uitleg: str


ANALYSES: dict[str, Analysis] = {
    "balans": Analysis("balans", "Capaciteitsbalans", ["vraag", "capaciteit"],
                       "Past het openstaande werk in de bemensing? Per week en per team."),
    "teams": Analysis("teams", "Teambezetting", ["capaciteit", "medewerkers"],
                      "Wie is beschikbaar, welk team, intern vs. ingeleend."),
    "projecten": Analysis("projecten", "Projectvoortgang", ["projecten"],
                          "Begroot vs. geboekt vs. nog te plannen, met vooruitblik per project."),
    "controle": Analysis("controle", "Signalen & controle", ["projecten"],
                         "Datakwaliteit en uitschieters: wat vraagt aandacht vóór je erop stuurt."),
    "adviezen": Analysis("adviezen", "Adviezen", ["projecten"],
                         "Automatische signalen uit de gecombineerde bronnen."),
}


# ── Klantprofielen ────────────────────────────────────────────────────────
@dataclass
class ClientProfile:
    key: str
    name: str
    tagline: str
    data_mode: str                                   # "megens" | "synthetic"
    blocks: dict[str, bool] = field(default_factory=dict)
    analyses: dict[str, bool] = field(default_factory=dict)
    horizon_weken: int = 26
    streefbezetting: int = 90                        # % — signaleringsgrens
    # Seizoens-/productiviteitsparameters (per klant instelbaar)
    vakantiedagen: float = 25.0
    adv_dagen: float = 0.0
    ziekte_pct: float = 4.0
    opleiding_pct: float = 1.0
    uren_per_dag: float = 8.0
    default_efficiency: int = 85                     # % — als 'efficiency' aan staat
    # Alleen voor synthetische profielen
    seed: int = 42
    n_teams: int = 5
    n_medewerkers: int = 42
    n_projecten: int = 28


ALLE_BLOKKEN_AAN = {k: True for k in BLOCKS}
ALLE_ANALYSES_AAN = {k: True for k in ANALYSES}

CLIENTS: dict[str, ClientProfile] = {
    # Lanceerklant: echte Syntess-data via de Notifica Data API
    "megens": ClientProfile(
        key="megens", name="Megens (echte data)",
        tagline="Live Syntess-data (klant 1142) via de Notifica Data API",
        data_mode="megens",
        # seizoen AAN: de contracturen in Syntess staan onveranderd op 2.756 u per week --
        # ook in de bouwvak -- en alleen aangevraagd verlof wordt als aparte regel afgetrokken.
        # Zonder de correctie lijkt de zomer daardoor even ruim als november. Het model vult
        # alleen het verschil aan met wat al is aangevraagd, dus nooit dubbel.
        # Standaard tonen we dus exact hun cijfers; de correctie is een optie in de
        # configuratiemodus en wordt dan expliciet als Notifica-model gelabeld.
        blocks={**ALLE_BLOKKEN_AAN, "prognose": False, "efficiency": False,
                "seizoen": True},
        analyses=dict(ALLE_ANALYSES_AAN),
    ),
    # Volledige inrichting op synthetische data (alle blokken, incl. prognose)
    "volledig": ClientProfile(
        key="volledig", name="Voorbeeld: volledige inrichting",
        tagline="Alle building blocks actief — inclusief prognose en efficiency",
        data_mode="synthetic",
        blocks=dict(ALLE_BLOKKEN_AAN), analyses=dict(ALLE_ANALYSES_AAN),
        seed=7, n_teams=6, n_medewerkers=58, n_projecten=34,
    ),
    # Instapmodel: geen HR-koppeling
    "instap": ClientProfile(
        key="instap", name="Voorbeeld: instapmodel",
        tagline="Zonder HR-koppeling — alleen vraag & projectvoortgang",
        data_mode="synthetic",
        blocks={"vraag": True, "capaciteit": False, "realisatie": True, "projecten": True,
                "medewerkers": False, "prognose": True, "seizoen": False, "efficiency": False},
        analyses={"balans": False, "teams": False, "projecten": True,
                  "controle": True, "adviezen": True},
        horizon_weken=20, seed=13, n_teams=3, n_medewerkers=22, n_projecten=18,
    ),
}

DEFAULT_CLIENT = "megens"
