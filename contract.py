"""
contract.py — Het canonieke datacontract.

Dit is de kern van de schaalbaarheid: de analyses praten NOOIT met Syntess-kolommen,
maar altijd met deze zes frames. Elke bron (Megens live, ERCO-export, synthetisch)
levert dezelfde structuur. Eén set analyses bedient daarmee elke klant.

    bron (source_*.py)  ──►  PlanningData  ──►  analyses.py

Ontbreekt een bron bij een klant, dan blijft dat frame leeg en degraderen de
analyses netjes (melding i.p.v. crash). Dat is het building-block-principe op
metriek-niveau.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd

# ── Kolom-contract per frame ────────────────────────────────────────────────
SCHEMA = {
    # Vraag: nog te verrichten werk, uitgezet over de tijd
    "vraag": ["project_key", "project", "team", "week_start", "uren"],
    # Aanbod: capaciteit per team per week (bruto + seizoensgecorrigeerd)
    "capaciteit": ["team", "week_start", "contract_uren", "beschikbaar_uren", "n_mw"],
    # Realisatie: geboekte uren per project per week
    "realisatie": ["project_key", "week_start", "uren"],
    # Projectoverzicht: één rij per project met de kern-metrieken
    "projecten": ["project_key", "project", "team", "fase", "projectleider",
                  "begroot", "geboekt", "nog_te_plannen", "overschrijding",
                  "calculatie", "pct_gereed"],
    # Mensen: wie, welk team, intern/extern
    "medewerkers": ["mdw_key", "medewerker", "team", "type", "contract_uren", "in_planning"],
    # Prognose: menselijke bijsturing op de kale calculatie
    "prognose": ["project_key", "prognose_eind", "resterend", "opmerking"],
    # Realisatietempo: wat er per week daadwerkelijk doorheen gaat.
    # Nodig omdat planning niet altijd vooruit wordt vastgelegd — dan is het tempo
    # waarmee gewerkt wordt de enige eerlijke maat voor wat er nog bij kan.
    "tempo": ["week_start", "projecturen", "indirecte_uren"],
}


def leeg(frame: str) -> pd.DataFrame:
    """Leeg frame met de juiste kolommen (zodat analyses altijd kunnen rekenen)."""
    return pd.DataFrame(columns=SCHEMA[frame])


@dataclass
class SourceMeta:
    """Herkomst + eerlijke kanttekeningen, zichtbaar in de UI."""
    klant: str
    bron_label: str                                   # bv. "Live Syntess-data via Notifica Data API"
    blokken: dict[str, bool] = field(default_factory=dict)   # welke frames echt gevuld zijn
    caveats: list[str] = field(default_factory=list)         # wat de bron NIET levert
    bronnen: list[tuple[str, str, str]] = field(default_factory=list)  # (blok, bron, levert)
    seizoen_toegepast: bool = False
    seizoen_uitleg: str = ""
    # Welke basis voor vrije capaciteit gebruikt wordt. Automatisch bepaald, want niet
    # elke klant gebruikt de planningsmodule van Syntess:
    #   "planning" — er staat planning vooruit; Ongepland is de vrije ruimte
    #   "tempo"    — geen planning; vrije ruimte = capaciteit minus het realisatietempo
    capaciteit_modus: str = "planning"
    capaciteit_uitleg: str = ""


@dataclass
class PlanningData:
    vraag: pd.DataFrame
    capaciteit: pd.DataFrame
    realisatie: pd.DataFrame
    projecten: pd.DataFrame
    medewerkers: pd.DataFrame
    prognose: pd.DataFrame
    meta: SourceMeta
    tempo: pd.DataFrame = field(default_factory=lambda: leeg("tempo"))

    def tempo_per_week(self, weken: int = 12) -> dict:
        """Mediaan projecturen en indirecte uren per week over de laatste `weken`
        volledige weken. Mediaan i.p.v. gemiddelde: robuust tegen een bouwvakweek
        of een week die nog niet volledig geboekt is."""
        t = self.tempo
        if not len(t):
            return {}
        t = t.copy().sort_values("week_start").tail(weken)
        return {
            "projecturen": float(t["projecturen"].median()),
            "indirecte_uren": float(t["indirecte_uren"].median()),
            "totaal": float((t["projecturen"] + t["indirecte_uren"]).median()),
            "weken": len(t),
            "van": pd.to_datetime(t["week_start"]).min(),
            "tot": pd.to_datetime(t["week_start"]).max(),
        }

    def heeft(self, frame: str) -> bool:
        return len(getattr(self, frame, [])) > 0

    def weken(self, n: int = 26) -> pd.DatetimeIndex:
        """Gemeenschappelijke week-as: start bij de vroegste week uit vraag/capaciteit."""
        starts = []
        for f in ("capaciteit", "vraag"):
            df = getattr(self, f)
            if len(df):
                starts.append(pd.to_datetime(df["week_start"]).min())
        start = min(starts) if starts else pd.Timestamp.today().normalize()
        start = start - pd.Timedelta(days=start.weekday())     # naar maandag
        return pd.date_range(start=start, periods=n, freq="7D")


def valideer(pd_obj: PlanningData) -> list[str]:
    """Controleer of de bron zich aan het contract houdt. Geeft lijst met problemen."""
    problemen = []
    for frame, kolommen in SCHEMA.items():
        df = getattr(pd_obj, frame, None)
        if df is None:
            problemen.append(f"{frame}: frame ontbreekt")
            continue
        mist = [k for k in kolommen if k not in df.columns]
        if mist:
            problemen.append(f"{frame}: mist kolom(men) {', '.join(mist)}")
    return problemen
