# Planning Intelligence Tool — prototype

Schaalbare capaciteits- & projectplanning voor installatiebedrijven (Notifica).
Config-gedreven **building blocks** die je per klant aan/uit zet. Zie
[PLAN.md](PLAN.md) voor architectuur, aanpak en de vervolginvestering.

> **Prototype op synthetische data** — bedoeld om te pitchen. Geen echte
> klantdata; niet voor productiebeslissingen.

## Lokaal draaien

```bash
pip install -r requirements.txt
streamlit run app.py
```

De app opent op http://localhost:8501.

## Pitchen — wat te laten zien

1. **Klantprofiel** (zijbalk) — wissel tussen ERCO / Projectvoortgang-light /
   Capaciteitssturing en laat zien hoe de tool zich herconfigureert.
2. **Building blocks** (zijbalk) — zet live een databron aan/uit; de dashboards
   passen zich direct aan. Dit is het schaalbaarheidsverhaal.
3. **Dashboards** — Management, Team/medewerker, Project-analyse, AI-adviezen.
4. **Inrichting-tab** — legt de bouwstenen visueel uit.

## Structuur

| Bestand | Rol |
|---------|-----|
| `config.py` | building blocks, dashboards, klantprofielen (het configuratiehart) |
| `data_gen.py` | prototype-connector: synthetische data |
| `engine.py` | capaciteits- en projectanalyse |
| `views.py` | de 4 dashboards + Inrichting |
| `theme.py` | Notifica-huisstijl |
| `app.py` | schil met klantkiezer + schakelaars |

## Optioneel: live AI-samenvatting

Zet `ANTHROPIC_API_KEY` in `.env` (zie `.env.example`) voor een live
Claude-samenvatting in het AI-adviezen-dashboard. Zonder sleutel draait alles op
regelgebaseerde adviezen.

## Productie

Alle data is nu synthetisch. In productie wisselen alleen de connectoren naar
Syntess (Notifica Data API), U-Serve en de invoer-applicatie (PostgreSQL op VPS3).
Engine en views blijven ongewijzigd. Details in [PLAN.md](PLAN.md) §3 en §5.
