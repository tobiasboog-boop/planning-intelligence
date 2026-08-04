"""
an_balans.py — Analyse "Capaciteitsbalans".

De kernvraag voor de directie: **past het openstaande werk in de bemensing, en waar
en wanneer knelt het?** Deze analyse werkt uitsluitend op het canonieke contract
(contract.PlanningData) en heeft de blokken `vraag` en `capaciteit` nodig.

Opbouw:
  1. KPI-rij       — vraag, capaciteit, gemiddeld beslag, knelweken, knelteams
  2. Hoofdgrafiek  — vraag per week (kleur = beslag) tegen de capaciteitslijn
  3. Heatmap       — team x week beslag-%: de plannings-hotspotview
  4. Teamtabel     — vraag/capaciteit/beslag per team + krapste weken
  5. Caveats       — wat de cijfers wel en niet zeggen

Eerlijkheid boven mooie plaatjes:
  • De vraag is het **nog in te plannen** werk, niet het totale werkpakket.
  • De capaciteit is **bruto** tenzij de seizoenscorrectie aan staat.
  • Er wordt alleen capaciteit meegerekend van teams die in deze horizon ook
    daadwerkelijk vraag hebben (appels met appels).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from an_common import (PLOT, beslag_kleur, beslag_status, capaciteit_kolom,
                       caveat_box, fmt, guard, pct, per_week)
from config import AMBER, GOLD, GREY, NAVY, NAVY2, NAVY_LIGHT, RED, ClientProfile
from contract import PlanningData
from theme import MND, kpi_cards, pill

GRIJS_TXT = "#8A8DB0"
KPI_CLS = {"risico": "risk", "let_op": "warn", "goed": "ok", "neutraal": ""}

# Licht -> navy -> amber -> rood; zmax = 120% zodat 85% en 100% op hun grens vallen
HEAT_SCALE = [
    [0.00, "#F4F6FF"],
    [0.35, "#CDD3F4"],
    [0.55, NAVY_LIGHT],
    [0.66, NAVY2],
    [0.72, AMBER],
    [0.85, RED],
    [1.00, "#B91C1C"],
]


# ── kleine helpers ──────────────────────────────────────────────────────────
def _wk(w) -> str:
    """Weeklabel: 'wk 32'."""
    t = pd.Timestamp(w)
    return f"wk {int(t.isocalendar()[1])}"


def _datum(w) -> str:
    """Nederlandse korte datum: '11 aug'."""
    t = pd.Timestamp(w)
    return f"{t.day} {MND[t.month]}"


def _venster(data: PlanningData, horizon: int) -> pd.DatetimeIndex:
    """Week-as voor de vooruitblik.

    Basis is de canonieke `data.weken()`. Ligt de huidige week binnen de databereik,
    dan start het venster bij deze week — een capaciteitsbalans die op historie begint
    is niet planbaar. Werk dat vóór het venster valt tonen we als achterstand.
    """
    weken = data.weken(horizon)
    laatste = None
    for f in ("vraag", "capaciteit"):
        df = getattr(data, f)
        if len(df):
            m = pd.to_datetime(df["week_start"]).max()
            laatste = m if laatste is None else max(laatste, m)
    maandag = pd.Timestamp.today().normalize()
    maandag -= pd.Timedelta(days=maandag.weekday())
    if laatste is not None and maandag <= laatste and maandag > weken[0]:
        return pd.date_range(start=maandag, periods=horizon, freq="7D")
    return weken


def _prep(df: pd.DataFrame, weken: pd.DatetimeIndex) -> pd.DataFrame:
    """Normaliseer week_start en beperk tot het venster."""
    if not len(df):
        return df.copy()
    out = df.copy()
    out["week_start"] = pd.to_datetime(out["week_start"]).dt.normalize()
    if "team" in out.columns:
        out["team"] = out["team"].fillna("Onbekend").replace("", "Onbekend")
    return out[out["week_start"].isin(weken)]


def _beslag(vraag: pd.Series | pd.DataFrame, cap: pd.Series | pd.DataFrame):
    """Beslag in % — deling door nul via NaN (nooit np.where: ZeroDivisionError)."""
    return vraag / cap.replace(0, np.nan) * 100.0


# ── hoofdrender ─────────────────────────────────────────────────────────────
def render(data: PlanningData, profile: ClientProfile, opts: dict) -> None:
    if guard(data, "vraag", "capaciteit"):
        return

    horizon = int(opts.get("horizon") or getattr(profile, "horizon_weken", 26))
    streef = int(opts.get("streef") or getattr(profile, "streefbezetting", 90))
    seizoen = bool(opts.get("seizoen", False))
    eff_aan = bool(opts.get("efficiency", False))
    eff_pct = int(opts.get("efficiency_pct") or getattr(profile, "default_efficiency", 100))
    eff = (eff_pct / 100.0) if eff_aan else 1.0

    capkol = capaciteit_kolom(data, seizoen)
    weken = _venster(data, horizon)

    vraag_all = _prep(data.vraag, weken)
    cap_all = _prep(data.capaciteit, weken)
    if not len(vraag_all):
        st.markdown('<div class="note">In de gekozen horizon staat geen openstaand werk '
                    'gepland. Verruim de horizon of controleer de vraag-bron.</div>',
                    unsafe_allow_html=True)
        caveat_box(data)
        return

    # ── appels met appels: alleen capaciteit van teams die vraag hebben ──────
    teams_vraag = sorted(vraag_all["team"].dropna().unique().tolist())
    teams_cap = sorted(cap_all["team"].dropna().unique().tolist())
    teams_match = [t for t in teams_vraag if t in teams_cap]
    teams_zonder_cap = [t for t in teams_vraag if t not in teams_cap]
    teams_zonder_vraag = [t for t in teams_cap if t not in teams_vraag]

    fallback_alle_teams = not teams_match
    cap_h = cap_all if fallback_alle_teams else cap_all[cap_all["team"].isin(teams_match)]

    # ── weekreeksen ─────────────────────────────────────────────────────────
    vraag_w = per_week(vraag_all, "uren", weken)
    cap_w = per_week(cap_h, capkol, weken) * eff
    bruto_w = per_week(cap_h, "contract_uren", weken) * eff
    beslag_w = _beslag(vraag_w, cap_w)
    tekort_w = (vraag_w - cap_w).clip(lower=0)

    vraag_tot = float(vraag_w.sum())
    cap_tot = float(cap_w.sum())
    beslag_gem = (vraag_tot / cap_tot * 100.0) if cap_tot > 0 else np.nan  # Σ/Σ, geen mean(ratio)
    weken_over = int((beslag_w > 100).sum())
    weken_streef = int((beslag_w > streef).sum())

    # ── team x week matrix ──────────────────────────────────────────────────
    vm = (vraag_all.pivot_table(index="team", columns="week_start", values="uren",
                                aggfunc="sum")
          .reindex(columns=weken).fillna(0.0))
    cm = (cap_h.pivot_table(index="team", columns="week_start", values=capkol,
                            aggfunc="sum")
          .reindex(columns=weken).fillna(0.0) * eff)
    if fallback_alle_teams:
        # teamlabels matchen niet: verdeel de totale capaciteit niet, laat leeg
        cm = cm.reindex(index=vm.index).fillna(0.0) * 0.0
    else:
        cm = cm.reindex(index=vm.index).fillna(0.0)
    bm = _beslag(vm, cm)

    knel_per_team = (bm > 100).sum(axis=1)
    teams_knel = int((knel_per_team > 0).sum())

    # ── 1. KPI-rij ──────────────────────────────────────────────────────────
    cap_label = "seizoensgecorrigeerd" if capkol == "beschikbaar_uren" else "bruto contracturen"
    if eff_aan:
        cap_label += f" × {eff_pct}% efficiency"
    piek_idx = beslag_w.idxmax() if beslag_w.notna().any() else None
    piek_sub = (f"krapste: {_wk(piek_idx)} ({pct(beslag_w.max())})"
                if piek_idx is not None else "geen beslag te bepalen")
    knel_namen = [str(t) for t in knel_per_team[knel_per_team > 0].index]
    knel_sub = ", ".join(knel_namen[:3]) + ("…" if len(knel_namen) > 3 else "")

    kpi_cards([
        {"lbl": "Nog in te plannen", "val": f"{fmt(vraag_tot)} u",
         "sub": f"{horizon} weken · {len(teams_vraag)} team(s)", "cls": "accent"},
        {"lbl": "Beschikbare capaciteit", "val": f"{fmt(cap_tot)} u",
         "sub": f"{len(teams_match) if teams_match else len(teams_cap)} team(s) met werk · {cap_label}"},
        {"lbl": "Gemiddeld beslag", "val": pct(beslag_gem),
         "sub": "vraag ÷ capaciteit over de horizon",
         "cls": KPI_CLS[beslag_status(beslag_gem)]},
        {"lbl": "Weken boven 100%", "val": f"{weken_over} van {horizon}",
         "sub": piek_sub, "cls": "risk" if weken_over else "ok"},
        {"lbl": "Teams met knelpunt", "val": f"{teams_knel} van {len(vm.index)}",
         "sub": (knel_sub if teams_knel else
                 f"geen week boven 100% · {weken_streef} wk boven streef {streef}%"),
         "cls": "risk" if teams_knel else "ok"},
    ])

    if fallback_alle_teams:
        st.markdown('<div class="note">De teamnamen in de vraag komen niet overeen met die in '
                    'de capaciteit. De capaciteitslijn is daarom de <b>totale</b> capaciteit; '
                    'de beslag-percentages per team zijn niet te berekenen. Controleer de '
                    'team-/afdelingsmapping in de bron.</div>', unsafe_allow_html=True)

    # achterstand: vraag die vóór het venster ligt
    vr_all = data.vraag.copy()
    vr_all["week_start"] = pd.to_datetime(vr_all["week_start"]).dt.normalize()
    achter = float(vr_all.loc[vr_all["week_start"] < weken[0], "uren"].sum())
    na_horizon = float(vr_all.loc[vr_all["week_start"] > weken[-1], "uren"].sum())

    # ── 2. Hoofdgrafiek ─────────────────────────────────────────────────────
    st.markdown("#### Nog in te plannen werk tegen de beschikbare capaciteit")
    labels = [_wk(w) for w in weken]
    kleuren = [beslag_kleur(p) for p in beslag_w]
    hover = [
        f"<b>{_wk(w)} · {_datum(w)}</b><br>"
        f"Nog in te plannen: {fmt(v)} u<br>"
        f"Capaciteit: {fmt(c)} u<br>"
        f"Beslag: {pct(b)}"
        + (f"<br>Tekort: {fmt(t)} u" if t > 0 else "")
        for w, v, c, b, t in zip(weken, vraag_w, cap_w, beslag_w, tekort_w)
    ]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=vraag_w.values, name="Nog in te plannen werk",
        marker=dict(color=kleuren, line=dict(width=0)),
        hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=cap_w.values, name=f"Beschikbare capaciteit ({cap_label})",
        mode="lines", line=dict(color=NAVY2, width=2.6),
        fill="tozeroy", fillcolor="rgba(54,54,162,0.09)",
        hoverinfo="skip",   # capaciteit + beslag staan al in de balk-hover
    ))
    if capkol == "beschikbaar_uren" and float(np.nansum(np.abs(bruto_w - cap_w))) > 1:
        fig.add_trace(go.Scatter(
            x=labels, y=bruto_w.values, name="Bruto contracturen (ongecorrigeerd)",
            mode="lines", line=dict(color=NAVY_LIGHT, width=1.4, dash="dot"),
            hoverinfo="skip",
        ))
    if streef != 100:
        fig.add_trace(go.Scatter(
            x=labels, y=(cap_w * streef / 100.0).values,
            name=f"Streefbezetting {streef}%",
            mode="lines", line=dict(color=GRIJS_TXT, width=1.2, dash="dash"),
            hoverinfo="skip",
        ))

    # "deze week"-markering: op een category-as via de positie-index (nooit een datum)
    nu = pd.Timestamp.today().normalize()
    nu -= pd.Timedelta(days=nu.weekday())
    if nu in set(weken):
        i = int(list(weken).index(nu))
        fig.add_shape(type="line", x0=i, x1=i, xref="x", y0=0, y1=1, yref="paper",
                      line=dict(color=NAVY, width=1, dash="dot"))
        fig.add_annotation(x=i, y=1.02, xref="x", yref="paper", text="deze week",
                           showarrow=False, font=dict(size=10, color=NAVY))

    stap = 1 if len(labels) <= 16 else 2
    fig.update_layout(
        **PLOT, height=350, bargap=0.28, hovermode="x unified",
        xaxis=dict(type="category", tickmode="array",
                   tickvals=[labels[i] for i in range(0, len(labels), stap)],
                   ticktext=[f"{labels[i]}<br><span style='font-size:9px;color:{GRIJS_TXT}'>"
                             f"{_datum(weken[i])}</span>" for i in range(0, len(labels), stap)],
                   tickfont=dict(size=10), showgrid=False),
        yaxis=dict(title="Uren per week", gridcolor="#EEF0FB", zerolinecolor="#E7E9F5"),
    )
    st.plotly_chart(fig, width="stretch")

    cap_scope = ("de totale capaciteit (teamnamen matchen niet)" if fallback_alle_teams else
                 f"alleen de {len(teams_match)} team(s) met openstaand werk in deze horizon")
    cap_txt = (f"Balkkleur = beslag: geel tot {85}%, oranje {85}–100%, rood boven 100%. "
               f"Capaciteit = {cap_scope} — appels met appels. "
               f"Horizon: {_datum(weken[0])} t/m {_datum(weken[-1])} ({horizon} weken).")
    if teams_zonder_cap:
        cap_txt += (" Zonder bekende capaciteit: " + ", ".join(map(str, teams_zonder_cap[:6]))
                    + ("…" if len(teams_zonder_cap) > 6 else "") + ".")
    if teams_zonder_vraag:
        cap_txt += (f" Buiten beeld ({len(teams_zonder_vraag)} team(s) zonder openstaand werk): "
                    + ", ".join(map(str, teams_zonder_vraag[:6]))
                    + ("…" if len(teams_zonder_vraag) > 6 else "") + ".")
    if achter > 0:
        cap_txt += (f" Let op: {fmt(achter)} u openstaand werk staat gepland vóór "
                    f"{_datum(weken[0])} — achterstand, niet in de grafiek meegenomen.")
    if na_horizon > 0:
        cap_txt += f" Na de horizon volgt nog {fmt(na_horizon)} u."
    st.caption(cap_txt)

    # ── 3. Heatmap team x week ──────────────────────────────────────────────
    st.markdown("#### Waar knelt het: beslag per team per week")
    z = bm.copy()
    rij_orde = z.mean(axis=1).sort_values(ascending=False).index
    z = z.reindex(rij_orde)
    vm_o, cm_o = vm.reindex(rij_orde), cm.reindex(rij_orde)

    hov = np.empty(z.shape, dtype=object)
    for r, team in enumerate(z.index):
        for c, w in enumerate(z.columns):
            b = z.iat[r, c]
            hov[r, c] = (f"<b>{team}</b><br>{_wk(w)} · {_datum(w)}<br>"
                         f"Vraag: {fmt(vm_o.iat[r, c])} u<br>"
                         f"Capaciteit: {fmt(cm_o.iat[r, c])} u<br>"
                         f"Beslag: {pct(b)}")

    hm = go.Figure(go.Heatmap(
        z=z.values, x=labels, y=[str(t) for t in z.index],
        zmin=0, zmax=120, colorscale=HEAT_SCALE,
        xgap=2, ygap=2, customdata=hov,
        hovertemplate="%{customdata}<extra></extra>",
        texttemplate="%{z:.0f}" if len(labels) <= 14 else None,
        textfont=dict(size=9),
        colorbar=dict(title=dict(text="Beslag %", font=dict(size=10)), thickness=10,
                      tickvals=[0, 50, 85, 100, 120],
                      ticktext=["0", "50", "85", "100", "120+"], tickfont=dict(size=9)),
    ))
    hm_h = int(min(420, max(200, 34 * max(1, len(z.index)) + 110)))
    hm.update_layout(
        **{k: v for k, v in PLOT.items() if k != "legend"}, height=hm_h,
        xaxis=dict(type="category", tickmode="array",
                   tickvals=[labels[i] for i in range(0, len(labels), stap)],
                   tickfont=dict(size=10), showgrid=False, side="bottom"),
        yaxis=dict(tickfont=dict(size=11), autorange="reversed"),
    )
    st.plotly_chart(hm, width="stretch")
    st.caption("Elke cel is een planbaar knelpunt: wit/blauw = ruimte, oranje = bijna vol "
               "(boven 85%), rood = meer werk dan uren (boven 100%). Grijze/lege cel = "
               "geen capaciteit van dat team in de bron voor die week.")

    # ── 4. Teamtabel + krapste weken ────────────────────────────────────────
    kol_l, kol_r = st.columns([1.45, 1])

    with kol_l:
        st.markdown("#### Per team over de horizon")
        vraag_t = vm.sum(axis=1)
        cap_t = cm.sum(axis=1)
        beslag_t = _beslag(vraag_t, cap_t)
        tab = pd.DataFrame({
            "vraag": vraag_t, "cap": cap_t, "beslag": beslag_t,
            "knelweken": knel_per_team.reindex(vraag_t.index).fillna(0).astype(int),
        }).sort_values("beslag", ascending=False, na_position="last")

        html = ('<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
                '<tr style="color:#8A8DB0;font-size:10.5px;text-transform:uppercase;'
                'letter-spacing:.4px">'
                '<th style="text-align:left;padding:6px 8px">Team</th>'
                '<th style="text-align:right;padding:6px 8px">Nog in te plannen</th>'
                '<th style="text-align:right;padding:6px 8px">Capaciteit</th>'
                '<th style="text-align:right;padding:6px 8px">Beslag</th>'
                '<th style="text-align:right;padding:6px 8px">Knelweken</th>'
                '<th style="text-align:left;padding:6px 8px">Status</th></tr>')
        for team, r in tab.iterrows():
            b = r["beslag"]
            kleur = beslag_kleur(b)
            cap_cel = fmt(r["cap"]) if r["cap"] > 0 else "—"
            html += (
                f'<tr style="border-top:1px solid #F1F2FB">'
                f'<td style="padding:6px 8px;font-weight:600;color:{NAVY}">{team}</td>'
                f'<td style="padding:6px 8px;text-align:right">{fmt(r["vraag"])} u</td>'
                f'<td style="padding:6px 8px;text-align:right">{cap_cel} u</td>'
                f'<td style="padding:6px 8px;text-align:right;font-weight:700;color:{kleur}">'
                f'{pct(b)}</td>'
                f'<td style="padding:6px 8px;text-align:right">{int(r["knelweken"])}</td>'
                f'<td style="padding:6px 8px">{pill(beslag_status(b))}</td></tr>')
        html += "</table>"
        st.markdown(html, unsafe_allow_html=True)
        st.caption("Gesorteerd op beslag. Beslag = som van de vraag gedeeld door de som van "
                   "de capaciteit over de hele horizon; een team kan gemiddeld ruimte hebben "
                   "en in losse weken tóch vastlopen — zie de knelweken.")

    with kol_r:
        st.markdown("#### Krapste weken")
        rank = beslag_w.dropna().sort_values(ascending=False)
        top = rank[rank > 100].head(6)
        if not len(top):
            top = rank.head(3)
            st.markdown('<div style="font-size:12.5px;color:#4A4D74;margin-bottom:8px">'
                        'Geen week boven 100% beslag. Dit zijn de weken met het hoogste '
                        'beslag — daar zit je marge.</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div style="font-size:12.5px;color:#4A4D74;margin-bottom:8px">'
                        f'{weken_over} week/weken boven 100%, cumulatief '
                        f'<b>{fmt(tekort_w.sum())} u</b> meer werk dan uren.</div>',
                        unsafe_allow_html=True)
        rows = ""
        for w, b in top.items():
            tek = float(tekort_w.get(w, 0.0))
            kleur = beslag_kleur(b)
            extra = (f'<span style="color:{GRIJS_TXT}">tekort {fmt(tek)} u</span>'
                     if tek > 0 else
                     f'<span style="color:{GRIJS_TXT}">ruimte {fmt(cap_w.get(w, 0) - vraag_w.get(w, 0))} u</span>')
            teams_w = bm[w].dropna() if w in bm.columns else pd.Series(dtype=float)
            piek_team = teams_w.idxmax() if len(teams_w) else None
            wie = (f'<span style="color:{GRIJS_TXT}">· zwaarst: {piek_team} '
                   f'({pct(teams_w.max())})</span>' if piek_team is not None else "")
            rows += (
                f'<div style="display:flex;align-items:baseline;gap:8px;padding:6px 0;'
                f'border-top:1px solid #F1F2FB;font-size:12.5px">'
                f'<span style="font-weight:700;color:{NAVY};min-width:74px">{_wk(w)} '
                f'<span style="font-weight:400;color:{GRIJS_TXT};font-size:11px">'
                f'{_datum(w)}</span></span>'
                f'<span style="font-weight:800;color:{kleur};min-width:52px;text-align:right">'
                f'{pct(b)}</span>{extra} {wie}</div>')
        st.markdown(rows, unsafe_allow_html=True)

    # ── 5. Caveats ──────────────────────────────────────────────────────────
    caveat_box(data)
