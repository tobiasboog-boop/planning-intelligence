"""
megens_source.py — Data-connector voor Megens (Syntess-klant 1142) via de Notifica Data API.

Read-only. Elke functie is een geverifieerde query (discovery-fan-out 2026-08-04) tegen de
Postgres-mirror in db 1142. Kolommen/aggregaties zijn empirisch gecontroleerd; zie de
docstrings voor de kernbeslissingen (methode 2 = realistische restvraag, contracturen = bruto
capaciteit want geen verzuimdata, team-mapping via AfdelingKey).

De tool praat NOOIT direct met de database — alles loopt via NotificaClient (X-Data-Key).
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "_sdk"))
from notifica_sdk import NotificaClient

KLANT = 1142


def get_client() -> NotificaClient:
    """Bouwt de NotificaClient. Accepteert zowel NOTIFICA_DATA_KEY als NOTIFICA_DWH_KEY
    (App Beheer-drafts zetten de Customer Data Key soms onder de laatste naam neer)."""
    data_key = os.getenv("NOTIFICA_DATA_KEY") or os.getenv("NOTIFICA_DWH_KEY") or os.getenv("NOTIFICA_APP_KEY")
    if not data_key:
        raise RuntimeError(
            "Geen Notifica Data-key gevonden in de omgeving. Zet in App Beheer -> deze draft -> "
            "Environment de variabele NOTIFICA_DATA_KEY (of NOTIFICA_DWH_KEY) op de Customer Data "
            "Key van klant 1142 en herstart de app."
        )
    return NotificaClient(data_key=data_key)


def _q(client: NotificaClient, sql: str) -> pd.DataFrame:
    """Stuur single-line SQL naar de Data API (validator eist 1 statement, geen newlines)."""
    return client.query(KLANT, " ".join(sql.split()))


# ── VRAAG (demand) ───────────────────────────────────────────────────────────
def fetch_demand_per_week(client) -> pd.DataFrame:
    """Benodigde uren per project/afdeling per week (voorwaarts).
    Bron: maatwerk.'Begrotinguren per werkdag' (dagspreiding, methode 2 = met plafond),
    gejoind naar Begrotingsuren (voor ProjectKey) en Projecten (afdeling/naam)."""
    sql = '''
        SELECT TRIM(p."Afdeling") AS afdeling, b."ProjectKey" AS project_key,
               TRIM(p."Project") AS project,
               date_trunc('week', w."plandatum")::date AS week_start,
               SUM(w."begrote uren per werkdag met plafond - methode 2") AS vraag_uren
        FROM maatwerk."Begrotinguren per werkdag" w
        JOIN maatwerk."Begrotingsuren" b
          ON w."ProjectWerkvoorbereidingRegelKey" = b."ProjectWerkvoorbereidingRegelKey"
        JOIN projecten."Projecten" p ON b."ProjectKey" = p."ProjectKey"
        GROUP BY 1,2,3,4 ORDER BY week_start
    '''
    df = _q(client, sql)
    df["week_start"] = pd.to_datetime(df["week_start"])
    df["vraag_uren"] = pd.to_numeric(df["vraag_uren"], errors="coerce").fillna(0.0)
    return df


# ── AANBOD (supply / capaciteit) ─────────────────────────────────────────────
def fetch_capacity_per_week(client) -> pd.DataFrame:
    """Contract- (bruto capaciteit) en vrije uren per afdeling per week.
    Bron: planning.'Geplande en contracturen medewerkers' Type=Contracturen/Ongepland,
    medewerker->afdeling via stam.Medewerkers.AfdelingKey -> stam.Afdelingen.
    NB: geen verzuim/verlof in de bron -> contract = bruto (licht overschat)."""
    sql = '''
        SELECT TRIM(a."Afdeling") AS afdeling,
               date_trunc('week', cu."Datum"::timestamp)::date AS week_start,
               SUM(CASE WHEN TRIM(cu."Type")='Contracturen' THEN cu."Aantal Uur"::numeric ELSE 0 END) AS capaciteit_uren,
               SUM(CASE WHEN TRIM(cu."Type")='Ongepland'    THEN cu."Aantal Uur"::numeric ELSE 0 END) AS vrije_uren,
               COUNT(DISTINCT cu."MedewerkerKey") AS n_mw
        FROM planning."Geplande en contracturen medewerkers" cu
        JOIN stam."Medewerkers" m ON m."MedewerkerKey" = cu."MedewerkerKey"
        JOIN stam."Afdelingen" a  ON a."AfdelingKey"   = m."AfdelingKey"
        WHERE TRIM(cu."Type") IN ('Contracturen','Ongepland') AND cu."Datum" IS NOT NULL
        GROUP BY 1,2 ORDER BY 2
    '''
    df = _q(client, sql)
    df["week_start"] = pd.to_datetime(df["week_start"])
    for c in ("capaciteit_uren", "vrije_uren", "n_mw"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def fetch_medewerkers(client) -> pd.DataFrame:
    """Per medewerker: afdeling, intern/extern, planning-vlag, contracturen over de horizon."""
    sql = '''
        SELECT m."MedewerkerKey" AS mdw_key,
               TRIM(m."Volledige naam") AS medewerker,
               TRIM(a."Afdeling") AS afdeling,
               CASE WHEN TRIM(m."Ingeleend (J/N)")='J' THEN 'Extern' ELSE 'Intern' END AS type,
               TRIM(m."Projectenplanning (J/N)") AS projectplanning,
               SUM(cu."Aantal Uur"::numeric) AS contract_uren
        FROM planning."Geplande en contracturen medewerkers" cu
        JOIN stam."Medewerkers" m ON m."MedewerkerKey" = cu."MedewerkerKey"
        JOIN stam."Afdelingen" a  ON a."AfdelingKey"   = m."AfdelingKey"
        WHERE TRIM(cu."Type")='Contracturen'
        GROUP BY 1,2,3,4,5 ORDER BY contract_uren DESC
    '''
    df = _q(client, sql)
    df["contract_uren"] = pd.to_numeric(df["contract_uren"], errors="coerce").fillna(0.0)
    return df


# ── PROJECT-OVERZICHT (Begrotingsuren per project) ───────────────────────────
def fetch_budget_per_project(client) -> pd.DataFrame:
    """Begroting/restvraag per project (methode 2)."""
    sql = '''
        SELECT b."ProjectKey" AS project_key,
               SUM(b."begrotingsuren - methode 2") AS begrotingsuren,
               SUM(b."Nog te plannen - methode 2") AS nog_te_plannen,
               SUM(b."Overschrijding boven behoefte uren - methode 2") AS overschrijding,
               SUM(b."Aantal uur") AS behoefte_uren
        FROM maatwerk."Begrotingsuren" b GROUP BY 1
    '''
    df = _q(client, sql)
    for c in ("begrotingsuren", "nog_te_plannen", "overschrijding", "behoefte_uren"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def fetch_booked_per_project(client) -> pd.DataFrame:
    """Geboekte (definitieve) uren per project (alleen projecturen; indirect = ProjectKey NULL)."""
    sql = '''
        SELECT u."ProjectKey" AS project_key, SUM(u."Aantal") AS geboekt
        FROM uren."Geboekte Uren" u
        WHERE u."ProjectKey" IS NOT NULL AND TRIM(u."Status")='Definitief'
        GROUP BY 1
    '''
    df = _q(client, sql)
    df["geboekt"] = pd.to_numeric(df["geboekt"], errors="coerce").fillna(0.0)
    return df


def fetch_projects_dim(client) -> pd.DataFrame:
    """Actieve project-dimensie (labels/filters)."""
    sql = '''
        SELECT "ProjectKey" AS project_key, TRIM("Project") AS project,
               TRIM("Soort") AS soort, TRIM("Projectfase") AS fase,
               "Projectleider" AS projectleider, TRIM("Werkgroep") AS werkgroep,
               TRIM("Afdeling") AS afdeling, "Percentage Gereed" AS pct_gereed, "Niveau" AS niveau
        FROM projecten."Projecten" WHERE TRIM("Status")='Actueel'
    '''
    df = _q(client, sql)
    df["pct_gereed"] = pd.to_numeric(df["pct_gereed"], errors="coerce")
    df["niveau"] = pd.to_numeric(df["niveau"], errors="coerce")
    return df


def fetch_calculatie_per_project(client) -> pd.DataFrame:
    """Gecalculeerde uren (status Opdracht) per project. Scant een grote fact — resilient."""
    sql = '''
        SELECT c."ProjectKey" AS project_key, SUM(round(c."Aantal"::numeric,1)) AS calculatie_uren
        FROM projecten."Calculatieregels" c
        WHERE TRIM(c."Calculatiestatus")='Opdracht' AND c."TaakKey" IS NOT NULL
        GROUP BY 1
    '''
    try:
        df = _q(client, sql)
        df["calculatie_uren"] = pd.to_numeric(df["calculatie_uren"], errors="coerce").fillna(0.0)
        return df
    except Exception:
        return pd.DataFrame(columns=["project_key", "calculatie_uren"])


def fetch_booked_per_week_project(client, project_key: int) -> pd.DataFrame:
    """Geboekte uren per week voor één project (drilldown)."""
    sql = f'''
        SELECT date_trunc('week', u."Uitvoeringsdatum")::date AS week_start, SUM(u."Aantal") AS geboekt
        FROM uren."Geboekte Uren" u
        WHERE u."ProjectKey" = {int(project_key)} AND TRIM(u."Status")='Definitief'
        GROUP BY 1 ORDER BY 1
    '''
    df = _q(client, sql)
    if len(df):
        df["week_start"] = pd.to_datetime(df["week_start"])
        df["geboekt"] = pd.to_numeric(df["geboekt"], errors="coerce").fillna(0.0)
    return df


# ── Samengesteld ─────────────────────────────────────────────────────────────
@dataclass
class MegensData:
    demand_week: pd.DataFrame        # afdeling, project_key, project, week_start, vraag_uren
    capacity_week: pd.DataFrame      # afdeling, week_start, capaciteit_uren, vrije_uren, n_mw
    medewerkers: pd.DataFrame        # mdw_key, medewerker, afdeling, type, projectplanning, contract_uren
    projecten: pd.DataFrame          # merged: project + begrotingsuren/geboekt/nog_te_plannen/overschrijding/calc


def build_project_overview(dim, budget, booked, calc) -> pd.DataFrame:
    """Merge project-dimensie met begroting, geboekt en calculatie tot het projectoverzicht."""
    df = dim.merge(budget, on="project_key", how="left") \
            .merge(booked, on="project_key", how="left") \
            .merge(calc, on="project_key", how="left")
    for c in ("begrotingsuren", "nog_te_plannen", "overschrijding", "behoefte_uren",
              "geboekt", "calculatie_uren"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        else:
            df[c] = 0.0
    # afgeleiden
    df["besteed_pct"] = df["geboekt"] / df["begrotingsuren"].replace(0, np.nan) * 100
    df["ratio_calc"] = df["geboekt"] / df["calculatie_uren"].replace(0, np.nan) * 100
    # alleen projecten met inhoud (begroting, geboekt of nog te plannen)
    df = df[(df["begrotingsuren"] > 0) | (df["geboekt"] > 0) | (df["nog_te_plannen"] > 0)].copy()
    return df.sort_values("nog_te_plannen", ascending=False)


def load_all(client=None) -> MegensData:
    c = client or get_client()
    demand = fetch_demand_per_week(c)
    capacity = fetch_capacity_per_week(c)
    mdw = fetch_medewerkers(c)
    dim = fetch_projects_dim(c)
    budget = fetch_budget_per_project(c)
    booked = fetch_booked_per_project(c)
    calc = fetch_calculatie_per_project(c)
    projecten = build_project_overview(dim, budget, booked, calc)
    return MegensData(demand_week=demand, capacity_week=capacity,
                      medewerkers=mdw, projecten=projecten)
