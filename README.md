# StreakScout

Een hulpje voor sportweddenschappen: per voetbalmatch een automatische analyse van de reeksen (algemeen, tegen deze tegenstander, wat tegen deze ploeg vaak gebeurt) en de bets waar die reeksen het meest over zeggen. Plus elke dag een superbet over alle competities.

Geen garantie op winst: bookmakers zijn gemiddeld beter dan de reeksen. Zet enkel in wat je kan missen.

## Wat zit erin

- `index.html` – de volledige site in één bestand (data, model en code zitten erin). Dit is wat GitHub Pages toont.
- `.nojekyll` – zegt tegen GitHub Pages dat het de bestanden ongewijzigd moet tonen.
- `pipeline/` – de code die de site bouwt en elke ochtend bijwerkt (zie `pipeline/RUNBOOK.md`).

## Online zetten met GitHub Pages

1. De bestanden staan al in deze repository.
2. Ga naar **Settings → Pages**, kies bij *Source* **Deploy from a branch**, branch **main** en map **/ (root)**, en klik **Save**.
3. Na een minuutje staat de site op `https://<jouw-gebruikersnaam>.github.io/streakscout/`.

Let op: een GitHub Pages-site is openbaar voor iedereen die de link heeft, ook als de repository privé is.

## Bijwerken

Elke ochtend werkt een geplande taak de site bij en zet ze de nieuwe `index.html` automatisch in deze repository, zodat GitHub Pages altijd de versie van die dag toont. Zelf een build omzetten kan ook:

```
python3 -I pipeline/gh_page.py <gebouwde streakscout.html> index.html
```
