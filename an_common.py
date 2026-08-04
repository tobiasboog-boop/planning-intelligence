"""
an_common.py — Gedeelde helpers voor alle analyses.

Elke analyse-module (an_*.py) importeert hieruit, zodat opmaak en gedrag identiek zijn.
Analyses werken UITSLUITEND op het canonieke contract (contract.PlanningData) — nooit
op klant-specifieke kolommen.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st

from config import NAVY, NAVY2, NAVY_LIGHT, GOLD, GREEN, AMBER, RED, GREY
from contract import PlanningData
from theme import kpi_cards, pill, advice_card, STATUS

PLOT = dict(
    font=dict(family="Segoe UI, sans-serif", size=12, color="#3A3D63"),
    paper_bgcolor="white", plot_bgcolor="white",
    margin=dict(l=10, r=10, t=30, b=10),
    legend=dict(orientation="h", y=1.14, x=0),
)


def fmt(n) -> str:
    """Nederlandse duizendscheiding; '—' bij ontbrekende waarde."""
    try:
        v = float(n)
        if pd.isna(v):
            return "—"
        return f"{v:,.0f}".replace(",", ".")
    except Exception:
        return "—"


def pct(n, decimals: int = 0) -> str:
    try:
        v = float(n)
        if pd.isna(v):
            return "—"
        return f"{v:.{decimals}f}%"
    except Exception:
        return "—"


def guard(data: PlanningData, *frames: str) -> bool:
    """True (en toont melding) als een benodigd frame leeg is → analyse degradeert netjes."""
    mist = [f for f in frames if not data.heeft(f)]
    if mist:
        namen = {"vraag": "Benodigde uren", "capaciteit": "Beschikbaarheid",
                 "realisatie": "Werkelijk bestede uren", "projecten": "Projectoverzicht",
                 "medewerkers": "Teams & medewerkers", "prognose": "Prognose"}
        labels = ", ".join(namen.get(f, f) for f in mist)
        st.markdown(
            f'<div class="note">Deze analyse heeft de building block(s) <b>{labels}</b> nodig. '
            f'Deze bron levert die (nog) niet — activeer of koppel het blok in de '
            f'<b>configuratiemodus</b>.</div>', unsafe_allow_html=True)
        return True
    return False


def beslag_kleur(p) -> str:
    """Kleur op basis van beslag-% (semantisch, niet merk-navy)."""
    if p is None or pd.isna(p):
        return GREY
    if p > 100:
        return RED
    if p > 85:
        return AMBER
    return GOLD


def beslag_status(p) -> str:
    if p is None or pd.isna(p):
        return "neutraal"
    if p > 100:
        return "risico"
    if p > 85:
        return "let_op"
    return "goed"


def week_reeks(data: PlanningData, horizon: int) -> pd.DatetimeIndex:
    return data.weken(horizon)


def per_week(df: pd.DataFrame, kolom: str, weeks: pd.DatetimeIndex) -> pd.Series:
    """Aggregeer een frame naar de gemeenschappelijke week-as (0 waar geen data)."""
    if not len(df):
        return pd.Series(0.0, index=weeks)
    s = df.copy()
    s["week_start"] = pd.to_datetime(s["week_start"])
    return s.groupby("week_start")[kolom].sum().reindex(weeks, fill_value=0.0)


def caveat_box(data: PlanningData) -> None:
    """Toon de eerlijke kanttekeningen van de bron (klant-zichtbaar)."""
    if not data.meta.caveats:
        return
    with st.expander("Let op — wat deze cijfers wel en niet zeggen"):
        for c in data.meta.caveats:
            st.markdown(f"- {c}")
        if data.meta.seizoen_toegepast and data.meta.seizoen_uitleg:
            st.markdown(f"- **Seizoenscorrectie actief.** {data.meta.seizoen_uitleg}")


def capaciteit_kolom(data: PlanningData, gebruik_seizoen: bool) -> str:
    """Welke capaciteitskolom gebruiken: gecorrigeerd of bruto."""
    if gebruik_seizoen and "beschikbaar_uren" in data.capaciteit.columns:
        return "beschikbaar_uren"
    return "contract_uren"
