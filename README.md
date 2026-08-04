# Planning Intelligence Tool

Capaciteits- en projectplanning voor installatiebedrijven (Notifica). Config-gedreven:
één set analyses, per klant in te richten via building blocks.

> Zie [PLAN.md](PLAN.md) voor het commerciële plan, [DATASPEC.md](DATASPEC.md) voor het
> Megens-datamodel en [WERKLOG.md](WERKLOG.md) voor beslissingen en lessen.

## Architectuur

```text
  source_megens.py     ┐
  source_synthetic.py  ┼──►  contract.PlanningData  ──►  an_*.py  (5 analyses)
  (nieuwe klant: 1 adapter)         ▲
                                    │
                            seasonality.py (productiviteitsfactor)
```

**De analyses raken nooit een klant-specifieke kolom.** Elke bron levert dezelfde zes
canonieke frames; daardoor maakt het voor de visuals niet uit of de data van Megens,
van ERCO of synthetisch is.

| Frame | Inhoud |
| ------- | -------- |
| `vraag` | nog te verrichten werk per project per week |
| `capaciteit` | per team per week: contracturen + effectief beschikbaar |
| `realisatie` | geboekte uren per project per week |
| `projecten` | begroot, geboekt, nog te plannen, overschrijding, calculatie |
| `medewerkers` | team, intern/extern, contracturen |
| `prognose` | verwachte resterende uren + herplanning |

## De vijf analyses

| Analyse | Vraag die het beantwoordt |
| --------- | --------------------------- |
| **Capaciteitsbalans** | Past het openstaande werk in de bemensing — per week en per team? |
| **Teambezetting** | Wie is beschikbaar, welk team, intern vs. ingeleend? |
| **Projectvoortgang** | Blijven we binnen de begrote uren, en landt het restwerk tijdig? |
| **Signalen & controle** | Wat moet je nakijken vóór je op deze cijfers stuurt? |
| **Adviezen** | Welke signalen vragen nu actie? |

## Configuratiemodus (intern)

De schakelaar **Configuratiemodus** in de zijbalk is voor de inrichtingssessie — *niet*
om met de klant te delen. Daarin stel je per klant in:

- welke **databronnen** gekoppeld zijn
- welke **rekenopties** aan staan (seizoens-/productiviteitsfactor, efficiency per team)
- de **parameters**: verlofdagen, ADV, ziekte%, opleiding%, uren per dag, efficiency%
- welke **analyses** de klant ziet
- planningshorizon en streefbezetting

De tab **Inrichting** valideert live of de gekoppelde bron zich aan het contract houdt.

## Seizoenscorrectie

Bruto contracturen zeggen niets over wat er in juli écht beschikbaar is. `seasonality.py`
rekent om met de Nederlandse feestdagenkalender, de vakantieverdeling over het jaar
(zomerpiek), ziekte en opleiding — **dezelfde rekenwijze als de Directe-urencalculator in
het leerportaal**, zodat tool en site hetzelfde rekenen.

Effect (2026, 25 verlofdagen, 4% ziekte, 1% opleiding): jaargemiddeld **83,1%** beschikbaar;
augustus **62%**, juli **65%**, november **93%**.

## Lokaal draaien

```bash
pip install -r requirements.txt
cp .env.example .env      # vul NOTIFICA_DATA_KEY in voor de echte-data-profielen
streamlit run app.py
```

## Nieuwe klant toevoegen

1. Profiel toevoegen in `config.py` → `CLIENTS`
2. Bron-adapter schrijven die `contract.PlanningData` teruggeeft (zie `source_megens.py`)
3. Building blocks en parameters instellen in de configuratiemodus

Geen wijziging in de analyses nodig.

## Bestanden

| Bestand | Rol |
| --------- | ----- |
| `config.py` | building blocks, analyses, klantprofielen |
| `contract.py` | canoniek datacontract + validatie |
| `seasonality.py` | productiviteits-/seizoensfactor |
| `source_megens.py` | Megens (klant 1142) via de Notifica Data API |
| `megens_source.py` | de onderliggende, geverifieerde queries |
| `source_synthetic.py` | synthetische demo-data |
| `an_balans/teams/projecten/controle/adviezen.py` | de 5 analyses |
| `an_common.py` | gedeelde helpers (opmaak, degradatie, week-as) |
| `theme.py` | Notifica-huisstijl |
| `app.py` | schil: klantkiezer, configuratiemodus, routing |
