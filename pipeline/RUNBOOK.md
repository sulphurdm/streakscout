# StreakScout – dagelijkse update

Dit artifact bewaart zijn eigen pipeline (`pipeline/`) en toestand (`state/`) als gepubliceerde bestanden.
Een geplande taak draait elke ochtend deze stappen in een verse sessie.

1. Datum van vandaag (Europe/Brussels) bepalen.
2. `Artifact read` op de artifact-URL met alle paden uit `pipeline/` en `state/`.
3. Werkmap `R` maken met `data/`, `fetched/`, `state/`; `state/*` erin kopiëren;
   `git clone --depth 1 https://github.com/xgabora/Club-Football-Match-Data-2000-2025.git R/data/cfmd`.
4. `python3 -I pipeline/update.py --root R --today YYYY-MM-DD --plan` geeft de lijst bestanden die vandaag opgehaald moeten worden:
   12 Europese competities, de MLS-speeldagen die nu lopen (`USA_rN.txt`), de speeldagen van de Europese bekers die binnen [vandaag−1, vandaag+9] vallen, en de topschutters van de Jupiler Pro League.
5. Elk bestand ophalen met WebFetch (URL-tabel en prompts staan in de geplande taak) en letterlijk wegschrijven naar `R/fetched/<bestand>`.
5b. Spelersdata van API-Football: `API_FOOTBALL_KEY=… python3 -I pipeline/af.py --state R/state --today YYYY-MM-DD --max-seconds 540`.
   Draait opnieuw zolang de uitvoer `"more": true` zegt (het gratis plan mag 10 verzoeken per minuut). Zie "API-Football" hieronder.
6. `python3 -I pipeline/update.py --root R --today YYYY-MM-DD`
   - controleert elke opgehaalde uitslag tegen wat al bekend is (dataset of vorige ophalingen);
   - een competitie met meerdere afwijkende scores of een onbekende ploegnaam blijft op de vorige stand;
     één losse afwijking t.o.v. de dataset (bronnen verschillen soms) wordt gemeld maar blokkeert niet;
   - een afwijking t.o.v. een vorige ophaling wordt pas aangepast als twee ophalingen na elkaar akkoord gaan;
   - Europese bekers: enkel het speelschema; data van speeldagen zonder gekende datum worden geleerd (`state/cup_dates.json`);
   - spelers: topschutters (goals, minuten, penalty's) met een controle dat een ploeg niet meer goals van spelers heeft dan in totaal;
   - MLS: per speeldag opgehaald (voetbalkrant heeft geen seizoenspagina die volledig doorkomt); speeldagen die in [vandaag−4, vandaag+9] beginnen,
     speeldagen met een uitgestelde match die nu valt, en de volgende speeldag zolang de datum nog niet gekend is. Eerste en laatste datum per speeldag staan in `state/cup_dates.json` (`USA`, `USA@last`);
   - Elo: na 1 september bijgewerkt met de Elo-formule op elke nieuwe uitslag (geen Elo voor de MLS);
   - superbet van de dag (`state/superbet.json`): één bet per match van vandaag en morgen over alle competities; exacte zoektocht naar de combinatie met de grootste totale kans die ±20x haalt (per match wordt winst vs 1X/X2 enz. afgewogen op odd tegen risico);
   - bouwt `R/dist/streakscout.html` en controleert dat JS en Python dezelfde kansen geven.
7. Publiceren naar dezelfde URL met de nieuwe pagina en alle `state/`-bestanden.

API-Football (spelersbets)
- `af.py` haalt op en bewaart meteen na elk verzoek (hervat waar het stopte). Budget = wat /status vandaag nog toelaat (gratis: 100/dag).
  Volgorde: competities en seizoenen (1×/week), speelschema per competitie (alles op dag 1, daarna de 4 oudste per dag),
  blessures/schorsingen voor de matchen van vandaag en morgen (`/injuries?ids=`, 20 per verzoek), dan spelersstatistieken van
  gespeelde matchen (`/fixtures?ids=`, 20 matchen per verzoek): eerst de laatste matchen van ploegen die binnenkort spelen in de
  belangrijkste competities (CL, Jupiler Pro League, Premier League, …), dan oudere.
- Bestanden in `state/`: `af_state.json` (plan, competitie-id's, seizoenen, toegang), `af_fixtures.json`, `af_players.csv`
  (per speler per match: minuten, basis, goals, assists, schoten, op doel, geel, rood, penalty's, rating), `af_names.json`,
  `af_teamstats.csv` (schoten, corners, kaarten, balbezit, xG per ploeg per match; nog niet gebruikt), `af_injuries.json`,
  `af_map.json` (API-ploeg-id → datasetnaam), `af_report.json`, `player_predictions.csv` (trackrecord spelers).
- `players_af.py` koppelt API-ploegen aan onze namen via de matchen (zelfde competitie, datum ±1 dag, zelfde score, namen lijken),
  en rekent per speler: cijfer per 90 minuten (goals, assists, op doel, schoten, geel; recente matchen zwaarder, halveringstijd
  6 matchen; naar het positiegemiddelde getrokken), verwachte minuten en kans dat hij speelt (laatste 5 ploegmatchen).
  Per ploeg: wat tegenstanders tegen hen scoren, schieten en aan kaarten krijgen.
- `players.js` (pagina én `superbet_cli.js`): kans per match = cijfer × verwachte minuten × matchfactor
  (goals/assists: verwachte goals van de ploeg volgens het model t.o.v. hun gemiddelde; schoten/kaarten: tegenstander en thuis/uit).
  Kans geldt als de speler meespeelt (bookmakers schrappen spelersbets zonder deelname). Afwezigen (blessure/schorsing) vallen weg.
- Verkeerde ploegkoppeling → `pipeline/af_aliases.json`: {"API-naam of id": "datasetnaam"}.
- Gratis plan geeft mogelijk geen toegang tot het lopende seizoen: dan staat in `af_report.json` per competitie
  "plan: Free plans do not have access to this season…" en is Pro nodig (7.500/dag; alles in één run).
- Zonder API-Football-data toont de pagina de topschutters van voetbalkrant (Jupiler Pro League, Champions League, Europa League; enkel scoren).

Onderhoud
- Nieuwe ploeg (promotie, nieuw seizoen) → naam toevoegen aan `pipeline/aliases.json` (voetbalkrant-naam → datasetnaam).
- Nieuw seizoen → URL's in `pipeline/leagues.json` en `pipeline/cups.json` aanpassen (2026-2027 → 2027-2028), nieuwe Europese deelnemers in `pipeline/cup_aliases.json`.
- Knock-outrondes van de bekers (vanaf februari) hebben andere paginanamen dan `speeldag-N` en moeten nog toegevoegd worden.
- MLS: de uitslagen van 2026 (speeldag 1–29) zijn eenmalig per speeldag ingeladen en nagekeken tegen de officiële stand (alle 30 ploegen identiek op gespeeld/W/G/V/doelpunten/punten).
  Het seizoen 2025 ontbreekt (dataset stopt eind 2024); zodra football-data.co.uk bereikbaar is, laadt `sources.fd_results` `new/USA.csv` (enkel goals).
  Nieuw MLS-seizoen (februari): niets aanpassen; op maandag wordt speeldag 1 opnieuw bekeken en bij een nieuwe datum worden de oude speeldagdata gewist. Nieuwe ploegen → `aliases.json` onder `USA`.
  MLS-play-offs zitten er niet in.
- Markten toevoegen: event in `engine.py` (long_table + MATCH_/TEAM_EVENTS) én `engine.js` (EV), label in `backtest.py`, markt in `markets.js` (MK);
  daarna `python3 -I engine.py Matches.csv EloRatings.csv OUT` en `python3 -I backtest.py OUT`, en `OUT/model.json` + `OUT/history.json` naar `pipeline/`. Bestaande modellen veranderen niet (training stopt in 2023).
