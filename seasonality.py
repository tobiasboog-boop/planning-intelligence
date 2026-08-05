"""
seasonality.py — Seizoenscorrectie op capaciteit.

Bruto contracturen zeggen niets over wat er in juli écht beschikbaar is. Deze module
zet bruto contracturen om naar **effectief beschikbare uren per week**, met:

  • Nederlandse feestdagen per jaar (alleen die op werkdagen)
  • De vakantieverdeling over het jaar (CBS/werkgeversdata: juli/aug zijn piekmaanden)
  • Ziekte- en opleidingspercentage
  • Werkdagen per maand (SVB-kalender)

De cijfers en de formule zijn identiek aan de **Directe-urencalculator in het
Notifica-leerportaal**, zodat tool en site hetzelfde rekenen:

    beschikbaar = contracturen − verlof − feestdagen − ziekte% − opleiding%

Nodig omdat Syntess bij Megens geen verzuim-/verlofregistratie bevat
(`uren."Medewerkers verzuim"` = 0 rijen) en contracturen ook feestdagen negeren.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

# Werkdagen per maand (jan..dec) — centrale dataset uit notifica_site/_data/werkdagen.json
WERKDAGEN: dict[int, list[int]] = {
    2025: [23, 20, 21, 22, 22, 21, 23, 21, 22, 23, 20, 23],
    2026: [22, 20, 22, 22, 21, 22, 23, 21, 22, 22, 21, 23],
    2027: [21, 20, 23, 22, 21, 22, 22, 22, 22, 21, 22, 23],
}

# Feestdagen die op een werkdag vallen, per maand (jan..dec)
FEESTDAGEN: dict[int, list[int]] = {
    2025: [1, 0, 0, 1, 2, 1, 0, 0, 0, 0, 0, 2],   # 8 totaal
    2026: [1, 0, 0, 2, 2, 0, 0, 0, 0, 0, 0, 1],   # 6 totaal
    2027: [1, 0, 0, 1, 2, 0, 0, 0, 0, 0, 0, 2],   # 8 totaal
}

# Verdeling van vakantiedagen over het jaar (som = 1,0).
# Jul/aug = bouwvak + zomervakantie; mei = brugdagen; dec = kerst.
VAKANTIE_VERDELING = [0.02, 0.04, 0.03, 0.04, 0.08, 0.04,
                      0.28, 0.28, 0.03, 0.06, 0.02, 0.08]

MAAND_NL = ["jan", "feb", "mrt", "apr", "mei", "jun",
            "jul", "aug", "sep", "okt", "nov", "dec"]


@dataclass(frozen=True)
class SeasonParams:
    """Per klant instelbaar (building block 'seizoenscorrectie')."""
    vakantiedagen: float = 25.0      # CAO: wettelijk min. 20, veel CAO's 25
    adv_dagen: float = 0.0
    ziekte_pct: float = 0.04         # 4% verzuim
    opleiding_pct: float = 0.01      # 1% opleiding
    uren_per_dag: float = 8.0

    @property
    def verlofdagen(self) -> float:
        return self.vakantiedagen + self.adv_dagen


def _jaar_data(jaar: int) -> tuple[list[int], list[int]]:
    """Werkdagen + feestdagen per maand; valt terug op het dichtstbijzijnde bekende jaar."""
    if jaar in WERKDAGEN:
        return WERKDAGEN[jaar], FEESTDAGEN[jaar]
    dichtst = min(WERKDAGEN, key=lambda j: abs(j - jaar))
    return WERKDAGEN[dichtst], FEESTDAGEN[dichtst]


def maand_factoren(jaar: int, p: SeasonParams | None = None) -> pd.Series:
    """Beschikbaarheidsfactor per maand (0-1): effectief beschikbaar / bruto contract.

    Index = maandnummer 1..12. Zelfde formule als de leerportaal-calculator.
    """
    p = p or SeasonParams()
    werkdagen, feestdagen = _jaar_data(jaar)
    factoren = {}
    for i in range(12):
        bruto = werkdagen[i] * p.uren_per_dag
        if bruto <= 0:
            factoren[i + 1] = 0.0
            continue
        verlof_u = (p.vakantiedagen * VAKANTIE_VERDELING[i] + p.adv_dagen / 12) * p.uren_per_dag
        feest_u = feestdagen[i] * p.uren_per_dag
        ziekte_u = bruto * p.ziekte_pct
        opl_u = bruto * p.opleiding_pct
        beschikbaar = max(0.0, bruto - verlof_u - feest_u - ziekte_u - opl_u)
        factoren[i + 1] = beschikbaar / bruto
    return pd.Series(factoren, name="factor")


def week_factoren(weeks, p: SeasonParams | None = None) -> pd.Series:
    """Beschikbaarheidsfactor per week. `weeks` = iterable van weekstart-datums (ma).

    Een week wordt toegekend aan de maand van haar donderdag (ISO-conventie), zodat
    een week die over de maandgrens valt bij de 'zwaartepunt'-maand hoort.
    """
    p = p or SeasonParams()
    idx = pd.DatetimeIndex(pd.to_datetime(list(weeks)))
    donderdag = idx + pd.Timedelta(days=3)
    cache: dict[int, pd.Series] = {}
    waarden = []
    for d in donderdag:
        if d.year not in cache:
            cache[d.year] = maand_factoren(d.year, p)
        waarden.append(float(cache[d.year].loc[d.month]))
    return pd.Series(waarden, index=idx, name="factor")


def pas_toe(capaciteit: pd.DataFrame, p: SeasonParams | None = None,
            kolom: str = "contract_uren", doel: str = "beschikbaar_uren") -> pd.DataFrame:
    """Voeg seizoensgecorrigeerde beschikbare uren toe aan een capaciteitsframe.

    Verwacht kolommen: week_start + `kolom`. Voegt `doel` en `season_factor` toe.
    """
    if not len(capaciteit):
        out = capaciteit.copy()
        out[doel] = []
        out["season_factor"] = []
        return out
    out = capaciteit.copy()
    f = week_factoren(sorted(out["week_start"].unique()), p)
    out["season_factor"] = out["week_start"].map(f)
    out["season_factor"] = out["season_factor"].fillna(float(np.nanmean(f.values)))
    out[doel] = out[kolom] * out["season_factor"]
    return out


def aanvulling(capaciteit: pd.DataFrame, p: SeasonParams | None = None) -> pd.DataFrame:
    """Vul de nog-niet-aangevraagde afwezigheid aan, zonder dubbel te tellen.

    Syntess registreert verlof, ADV, feestdagen en ziekte vooruit als indirecte taak —
    maar alleen wat al is aangevraagd. Dichtbij is dat compleet, verder weg vrijwel niets
    (bij Megens: 1.202 u in de zomerweken, 140 u eind september). De urencalculator uit
    het leerportaal weet wat er normaal gesproken nog bij komt.

    Daarom: verwachte afwezigheid volgens het model MINUS wat al geregistreerd staat,
    afgekapt op nul. Nooit optellen bij wat de ERP al weet.

    Voegt toe: `verwacht_verlof`, `extra_verlof`, `vrij_gecorrigeerd`.
    """
    p = p or SeasonParams()
    out = capaciteit.copy()
    if not len(out):
        for k in ("verwacht_verlof", "extra_verlof", "vrij_gecorrigeerd"):
            out[k] = []
        return out

    f = week_factoren(sorted(out["week_start"].unique()), p)
    factor = out["week_start"].map(f).fillna(float(np.nanmean(f.values)))
    # verwachte afwezigheid = deel van de contracturen dat volgens het model wegvalt
    out["verwacht_verlof"] = out["contract_uren"] * (1.0 - factor)
    geregistreerd = out["verlof_uren"] if "verlof_uren" in out.columns else 0.0
    out["extra_verlof"] = (out["verwacht_verlof"] - geregistreerd).clip(lower=0)
    basis = out["ongepland_uren"] if "ongepland_uren" in out.columns else out["contract_uren"]
    out["vrij_gecorrigeerd"] = (basis - out["extra_verlof"]).clip(lower=0)
    return out


def toelichting(p: SeasonParams | None = None, jaar: int = 2026) -> str:
    """Korte, eerlijke uitleg voor in de UI."""
    p = p or SeasonParams()
    f = maand_factoren(jaar, p)
    laag = f.idxmin()
    return (f"Bruto contracturen gecorrigeerd voor {p.vakantiedagen:.0f} verlofdagen, "
            f"{sum(_jaar_data(jaar)[1])} feestdagen op werkdagen, {p.ziekte_pct*100:.0f}% ziekte "
            f"en {p.opleiding_pct*100:.0f}% opleiding. Laagste maand: "
            f"{MAAND_NL[laag-1]} ({f.loc[laag]*100:.0f}% beschikbaar), hoogste: "
            f"{MAAND_NL[f.idxmax()-1]} ({f.max()*100:.0f}%). Zelfde rekenwijze als de "
            f"Directe-urencalculator in het leerportaal.")
