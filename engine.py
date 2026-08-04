"""
engine.py — Capaciteits- en projectanalyse.

Combineert de ingeschakelde building blocks tot de kern-inzichten:
  • vraag (benodigde uren) vs aanbod (effectieve capaciteit) per team per week
  • bezettingsgraad en tekort/overschot
  • projectvoortgang: calculatie vs werkelijk vs prognose, verwachte uitloop

Alle functies respecteren `profile.blocks`: staat een block uit, dan valt het
bijbehorende deel van de berekening weg (i.p.v. te crashen).
"""
from __future__ import annotations
from datetime import timedelta
import numpy as np
import pandas as pd

from config import ClientProfile
from data_gen import DataBundle


def _on(profile: ClientProfile, key: str) -> bool:
    return profile.blocks.get(key, False)


# ── Aanbod ──────────────────────────────────────────────────────────────────
def weekly_capacity(bundle: DataBundle, profile: ClientProfile) -> pd.DataFrame:
    """Effectieve capaciteit (uren) per team per week."""
    if not _on(profile, "availability"):
        return pd.DataFrame(columns=["team", "week_idx", "capaciteit"])
    bw = bundle.beschikbaarheid_week.copy()
    eff = dict(zip(bundle.teams["team"], bundle.teams["efficiency"]))
    if _on(profile, "team_master"):
        bw["cap"] = bw.apply(lambda r: r["beschikbaar"] * eff.get(r["team"], profile.default_efficiency), axis=1)
    else:
        bw["cap"] = bw["beschikbaar"] * profile.default_efficiency
    out = (bw.groupby(["team", "week_idx"], as_index=False)["cap"]
           .sum().rename(columns={"cap": "capaciteit"}))
    return out


# ── Vraag ─────────────────────────────────────────────────────────────────
def weekly_demand(bundle: DataBundle, profile: ClientProfile) -> pd.DataFrame:
    """Benodigde uren per team per week.

    forecast AAN  → resterend werk = prognose resterende uren, venster = prognoseperiode
    forecast UIT  → resterend werk = calculatie - werkelijk, venster = calculatieperiode
    """
    if not _on(profile, "demand"):
        return pd.DataFrame(columns=["team", "week_idx", "benodigde_uren"])

    H = profile.horizon_weeks
    use_fc = _on(profile, "forecast")
    prog = bundle.prognose.set_index("project_id") if use_fc else None
    rows = []
    for _, p in bundle.projecten.iterrows():
        pid = p["project_id"]
        if use_fc and prog is not None and pid in prog.index:
            rest = float(prog.loc[pid, "prog_resterend"])
            eind_wk = p["calc_eind_wk"] + int(prog.loc[pid, "vertraging_wk"])
        else:
            rest = float(max(0.0, p["calc_uren"] - p["werkelijk_uren"]))
            eind_wk = p["calc_eind_wk"]
        start_wk = max(0, p["calc_start_wk"])
        active = [i for i in range(start_wk, min(eind_wk, H - 1) + 1) if 0 <= i < H]
        if rest <= 0 or not active:
            continue
        k = len(active)
        wgt = np.array([1.0 + 0.4 * np.sin(np.pi * (t + 0.5) / k) for t in range(k)])
        wgt /= wgt.sum()
        for pos, i in enumerate(active):
            rows.append({"team": p["team"], "week_idx": i,
                         "benodigde_uren": rest * wgt[pos]})
    if not rows:
        return pd.DataFrame(columns=["team", "week_idx", "benodigde_uren"])
    return (pd.DataFrame(rows).groupby(["team", "week_idx"], as_index=False)["benodigde_uren"].sum())


# ── Vraag vs aanbod ─────────────────────────────────────────────────────────
def balance(bundle: DataBundle, profile: ClientProfile) -> pd.DataFrame:
    """Long-form: per team per week capaciteit, vraag, tekort en bezetting."""
    cap = weekly_capacity(bundle, profile)
    dem = weekly_demand(bundle, profile)
    teams = sorted(set(cap["team"]) | set(dem["team"]))
    grid = pd.MultiIndex.from_product(
        [teams, range(profile.horizon_weeks)], names=["team", "week_idx"]).to_frame(index=False)
    df = (grid.merge(cap, on=["team", "week_idx"], how="left")
              .merge(dem, on=["team", "week_idx"], how="left").fillna(0.0))
    df["tekort"] = (df["benodigde_uren"] - df["capaciteit"]).clip(lower=0)
    df["overschot"] = (df["capaciteit"] - df["benodigde_uren"]).clip(lower=0)
    df["bezetting"] = df["benodigde_uren"] / df["capaciteit"].replace(0, np.nan)
    df["week_start"] = df["week_idx"].map(lambda i: bundle.weeks[i])
    return df


def org_by_week(bal: pd.DataFrame) -> pd.DataFrame:
    """Organisatie-breed per week (som over teams)."""
    g = bal.groupby("week_idx", as_index=False).agg(
        capaciteit=("capaciteit", "sum"), benodigde_uren=("benodigde_uren", "sum"),
        tekort=("tekort", "sum"), overschot=("overschot", "sum"),
        week_start=("week_start", "first"))
    g["bezetting"] = g["benodigde_uren"] / g["capaciteit"].replace(0, np.nan)
    return g


def team_summary(bal: pd.DataFrame, profile: ClientProfile) -> pd.DataFrame:
    g = bal.groupby("team", as_index=False).agg(
        capaciteit=("capaciteit", "sum"), benodigde_uren=("benodigde_uren", "sum"),
        tekort=("tekort", "sum"), overschot=("overschot", "sum"),
        piek_bezetting=("bezetting", "max"))
    g["bezetting"] = g["benodigde_uren"] / g["capaciteit"].replace(0, np.nan)
    g = g.sort_values("bezetting", ascending=False)
    return g


# ── Projectanalyse ──────────────────────────────────────────────────────────
def project_analysis(bundle: DataBundle, profile: ClientProfile) -> pd.DataFrame:
    if not _on(profile, "demand"):
        return pd.DataFrame()
    use_fc = _on(profile, "forecast")
    p = bundle.projecten.copy()
    if use_fc:
        prog = bundle.prognose[["project_id", "prog_resterend", "prog_eind",
                                "vertraging_wk", "opmerking"]]
        p = p.merge(prog, on="project_id", how="left")
        p["prog_resterend"] = p["prog_resterend"].fillna(0.0)
        p["prognose_totaal"] = p["werkelijk_uren"] + p["prog_resterend"]
        p["vertraging_wk"] = p["vertraging_wk"].fillna(0).astype(int)
    else:
        p["prog_resterend"] = (p["calc_uren"] - p["werkelijk_uren"]).clip(lower=0)
        p["prognose_totaal"] = p[["werkelijk_uren", "calc_uren"]].max(axis=1)
        p["vertraging_wk"] = 0
        p["opmerking"] = ""
        p["prog_eind"] = p["calc_eind"]

    calc_safe = p["calc_uren"].replace(0, np.nan)
    p["besteed_pct"] = (p["werkelijk_uren"] / calc_safe).fillna(0)
    p["uitloop_uren"] = p["prognose_totaal"] - p["calc_uren"]
    p["uitloop_pct"] = (p["uitloop_uren"] / calc_safe).fillna(0)

    def status(r):
        if r["uitloop_pct"] > 0.10 or r["vertraging_wk"] >= 3:
            return "risico"
        if r["uitloop_pct"] > 0.03 or r["vertraging_wk"] >= 1:
            return "let_op"
        return "goed"
    p["status"] = p.apply(status, axis=1)
    return p.sort_values("uitloop_uren", ascending=False)


# ── Medewerker-overzicht ────────────────────────────────────────────────────
def medewerker_summary(bundle: DataBundle, profile: ClientProfile) -> pd.DataFrame:
    if not _on(profile, "availability"):
        return pd.DataFrame()
    m = bundle.beschikbaarheid_mdw.copy()
    m["afwezig"] = m[["verlof", "ziekte", "opleiding", "feestdag"]].sum(axis=1)
    m["beschikbaar_pct"] = m["beschikbaar"] / m["bruto_horizon"].replace(0, np.nan)
    return m


# ── Kern-KPI's ───────────────────────────────────────────────────────────────
def headline_kpis(bundle: DataBundle, profile: ClientProfile) -> dict:
    k = {}
    if _on(profile, "demand"):
        pa = project_analysis(bundle, profile)
        k["n_projecten"] = len(pa)
        k["projecten_risico"] = int((pa["status"] == "risico").sum()) if len(pa) else 0
        k["uitloop_totaal"] = float(pa["uitloop_uren"].clip(lower=0).sum()) if len(pa) else 0.0
        k["calc_totaal"] = float(pa["calc_uren"].sum()) if len(pa) else 0.0
    if _on(profile, "availability") and _on(profile, "demand"):
        bal = balance(bundle, profile)
        org = org_by_week(bal)
        k["cap_totaal"] = float(org["capaciteit"].sum())
        k["vraag_totaal"] = float(org["benodigde_uren"].sum())
        k["bezetting_gem"] = (k["vraag_totaal"] / k["cap_totaal"]) if k["cap_totaal"] else np.nan
        k["tekort_totaal"] = float(org["tekort"].sum())
        k["piekweken_tekort"] = int((org["tekort"] > 0).sum())
    elif _on(profile, "availability"):
        ms = medewerker_summary(bundle, profile)
        k["cap_totaal"] = float(ms["beschikbaar"].sum()) if len(ms) else 0.0
        k["n_medewerkers"] = len(ms)
    return k
