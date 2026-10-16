# StreakScout

Een hulpje voor sportweddenschappen: per voetbalmatch een automatische analyse van de reeksen (algemeen, tegen deze tegenstander, wat tegen deze ploeg vaak gebeurt) en de bets waar die reeksen het meest over zeggen. Plus elke dag een superbet over alle competities.

Geen garantie op winst: bookmakers zijn gemiddeld beter dan de reeksen. Zet enkel in wat je kan missen.

## Wat zit erin

- `index.html` – de volledige site in één bestand (data, model en code zitten erin). Dit is wat GitHub Pages toont.
- `.nojekyll` – zegt tegen GitHub Pages dat het de bestanden ongewijzigd moet tonen.
- `pipeline/` – de code die de site bouwt en elke ochtend bijwerkt (zie `pipeline/RUNBOOK.md`).

## Online zetten met GitHub Pages

1. Maak een nieuwe repository, bijvoorbeeld `streakscout`.
2. Upload de inhoud van deze map (de bestanden zelf, niet de map erboven) naar de hoofdmap van de repository, met `.nojekyll` erbij.
3. Ga naar **Settings → Pages**, kies bij *Source* **Deploy from a branch**, branch **main** en map **/ (root)**, en klik **Save**.
4. Na een minuutje staat de site op `https://<jouw-gebruikersnaam>.github.io/streakscout/`.

Let op: een GitHub Pages-site is openbaar voor iedereen die de link heeft, ook als de repository privé is.

## Bijwerken

De dagelijkse update draait als geplande taak en werkt de versie op Claude bij. Deze `index.html` is een momentopname van vandaag. Om de GitHub-versie bij te werken, vervang je `index.html` door een nieuwe build:

```
python3 -I pipeline/gh_page.py <gebouwde streakscout.html> index.html
```
