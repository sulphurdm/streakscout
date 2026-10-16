"""Direct data sources (need network access to these hosts):
  - football-data.co.uk: season results with corners/cards, and fixtures.csv with bookmaker odds
  - The Odds API (api.the-odds-api.com): live pre-match odds of EU bookmakers (Unibet, Pinnacle, ...)
"""
import difflib
import io
import json
import re
import subprocess
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pandas as pd

FD = "https://www.football-data.co.uk"
ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_SPORTS = {
    "B1": "soccer_belgium_first_div", "E0": "soccer_epl", "E1": "soccer_efl_champ", "SP1": "soccer_spain_la_liga",
    "D1": "soccer_germany_bundesliga", "I1": "soccer_italy_serie_a", "F1": "soccer_france_ligue_one",
    "N1": "soccer_netherlands_eredivisie", "P1": "soccer_portugal_primeira_liga", "SC0": "soccer_spl",
    "T1": "soccer_turkey_super_league", "G1": "soccer_greece_super_league",
    "USA": "soccer_usa_mls", "CL": "soccer_uefa_champs_league", "EL": "soccer_uefa_europa_league", "ECL": "soccer_uefa_europa_conference_league",
}
# bookmakers shown by name in the app, in this order; the rest only count for "best odd"
SHOWN = ["unibet_nl", "unibet_fr", "unibet_se", "unibet_it", "pinnacle", "betfair_ex_eu", "williamhill", "sport888", "betsson"]


def http_get(url, timeout=60):
    """GET through curl (it uses the workspace proxy and CA bundle). Returns (status, headers, body bytes)."""
    p = subprocess.run(["curl", "-sS", "-L", "--max-time", str(timeout), "-D", "-", url], capture_output=True)
    if p.returncode != 0:
        return None, {}, p.stderr
    raw = p.stdout
    # split the last header block from the body
    parts = raw.split(b"\r\n\r\n")
    body_start = 0
    headers, status = {}, None
    i = 0
    while i < len(parts) and parts[i].startswith(b"HTTP/"):
        lines = parts[i].split(b"\r\n")
        status = int(lines[0].split()[1])
        headers = {ln.split(b":", 1)[0].decode().lower(): ln.split(b":", 1)[1].strip().decode()
                   for ln in lines[1:] if b":" in ln}
        body_start += len(parts[i]) + 4
        i += 1
    return status, headers, raw[body_start:]


# ---------------------------------------------------------------- football-data.co.uk
def season_code(today):
    y = today.year if today.month >= 7 else today.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def _read_fd_csv(body):
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(body), encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(body), encoding="latin-1", low_memory=False, on_bad_lines="skip")


def _fd_date(s):
    return pd.to_datetime(s, dayfirst=True, errors="coerce")


def fd_results_new(div, today, fname):
    """'New leagues' files (one CSV with all seasons, e.g. USA.csv): goals only. Keeps the last ~2 years."""
    st, _, body = http_get(f"{FD}/new/{fname}.csv")
    if st != 200:
        return None, f"football-data {div}: HTTP {st}"
    df = _read_fd_csv(body)
    df = df.dropna(subset=["Home", "Away", "HG", "AG"])
    d = _fd_date(df["Date"])
    df = df[d >= pd.Timestamp(today) - pd.Timedelta(days=730)]
    d = d[df.index]
    out = pd.DataFrame({"Division": div, "MatchDate": d.dt.date,
                        "MatchTime": df.get("Time", pd.Series(["12:00"] * len(df), index=df.index)).fillna("12:00").astype(str),
                        "HomeTeam": df.Home.str.strip(), "AwayTeam": df.Away.str.strip(), "FTHome": df.HG, "FTAway": df.AG})
    return out.dropna(subset=["MatchDate"]), None


NEW_LEAGUE_FILES = {"USA": "USA"}


def fd_results(div, today):
    """Season results for one league, in the dataset's column names. Returns (DataFrame|None, error|None)."""
    if div in NEW_LEAGUE_FILES:
        return fd_results_new(div, today, NEW_LEAGUE_FILES[div])
    st, _, body = http_get(f"{FD}/mmz4281/{season_code(today)}/{div}.csv")
    if st != 200:
        return None, f"football-data {div}: HTTP {st}"
    df = _read_fd_csv(body)
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    if df.empty:
        return pd.DataFrame(), None
    out = pd.DataFrame({
        "Division": div, "MatchDate": _fd_date(df["Date"]).dt.date,
        "MatchTime": df.get("Time", pd.Series(["12:00"] * len(df))).fillna("12:00").astype(str),
        "HomeTeam": df.HomeTeam.str.strip(), "AwayTeam": df.AwayTeam.str.strip(),
        "FTHome": df.FTHG, "FTAway": df.FTAG, "HTHome": df.get("HTHG"), "HTAway": df.get("HTAG"),
        "HomeCorners": df.get("HC"), "AwayCorners": df.get("AC"),
        "HomeYellow": df.get("HY"), "AwayYellow": df.get("AY"), "HomeRed": df.get("HR"), "AwayRed": df.get("AR"),
        "HomeShots": df.get("HS"), "AwayShots": df.get("AS"), "HomeTarget": df.get("HST"), "AwayTarget": df.get("AST"),
    })
    return out.dropna(subset=["MatchDate"]), None


def fd_fixture_odds(divs):
    """Bookmaker odds for upcoming matches from fixtures.csv: {(div, home, away, date): {...}}."""
    st, _, body = http_get(f"{FD}/fixtures.csv")
    if st != 200:
        return None, f"football-data fixtures: HTTP {st}"
    df = _read_fd_csv(body)
    df = df[df["Div"].isin(divs)]
    out = {}
    cols = lambda *c: all(x in df.columns for x in c)  # noqa: E731
    for r in df.to_dict("records"):
        d = _fd_date(r["Date"])
        if pd.isna(d):
            continue
        o = {"1x2": {}, "ou25": {}}
        for label, h, x, a in (("Bet365", "B365H", "B365D", "B365A"), ("Pinnacle", "PSH", "PSD", "PSA"),
                               ("William Hill", "WHH", "WHD", "WHA"), ("Gemiddelde markt", "AvgH", "AvgD", "AvgA"),
                               ("Hoogste markt", "MaxH", "MaxD", "MaxA")):
            if cols(h, x, a) and all(pd.notna(r.get(c)) for c in (h, x, a)):
                o["1x2"][label] = [float(r[h]), float(r[x]), float(r[a])]
        for label, ov, un in (("Bet365", "B365>2.5", "B365<2.5"), ("Pinnacle", "P>2.5", "P<2.5"),
                              ("Gemiddelde markt", "Avg>2.5", "Avg<2.5"), ("Hoogste markt", "Max>2.5", "Max<2.5")):
            if cols(ov, un) and pd.notna(r.get(ov)) and pd.notna(r.get(un)):
                o["ou25"][label] = [float(r[ov]), float(r[un])]
        if o["1x2"] or o["ou25"]:
            out[(r["Div"], str(r["HomeTeam"]).strip(), str(r["AwayTeam"]).strip(), str(d.date()))] = o
    return out, None


# ---------------------------------------------------------------- The Odds API
STOP = {"fc", "cf", "sc", "ac", "afc", "kv", "krc", "kaa", "rc", "sl", "cd", "fk", "sk", "vfb", "vfl", "tsg", "1", "the",
        "club", "de", "city", "town", "united", "utd", "calcio", "sv", "bv", "rsc", "royal", "k", "r", "as", "ss", "us",
        "ud", "rcd", "ca", "real", "sporting", "athletic", "stade", "olympique", "og", "ogc", "aj", "cs", "sd", "fk"}


def norm_name(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(w for w in s.split() if w not in STOP)


def name_score(api_name, known_names):
    a = norm_name(api_name)
    best = 0.0
    for k in known_names:
        b = norm_name(k)
        if not a or not b:
            continue
        if a == b:
            return 1.0
        ta, tb = set(a.split()), set(b.split())
        jac = len(ta & tb) / len(ta | tb)
        best = max(best, jac, difflib.SequenceMatcher(None, a, b).ratio())
    return best


def odds_api_fetch(comp, key, start, end):
    """Odds for one competition between start and end (UTC datetimes). Returns (events|None, headers, error)."""
    url = (f"{ODDS_API}/sports/{ODDS_SPORTS[comp]}/odds?apiKey={key}&regions=eu&markets=h2h,totals"
           f"&oddsFormat=decimal&dateFormat=iso&commenceTimeFrom={start:%Y-%m-%dT%H:%M:%SZ}&commenceTimeTo={end:%Y-%m-%dT%H:%M:%SZ}")
    st, hdr, body = http_get(url)
    if st != 200:
        msg = body[:200].decode(errors="replace") if isinstance(body, bytes) else str(body)
        return None, hdr, f"The Odds API {comp}: HTTP {st} {msg}"
    return json.loads(body), hdr, None


def odds_from_event(ev):
    """{'1x2': {bookmaker: [h, d, a]}, 'ou25': {bookmaker: [over, under]}} + best odds per outcome."""
    o = {"1x2": {}, "ou25": {}}
    for bm in ev.get("bookmakers", []):
        title = bm.get("title") or bm.get("key")
        for mk in bm.get("markets", []):
            oc = mk.get("outcomes", [])
            if mk.get("key") == "h2h":
                pr = {x["name"]: x["price"] for x in oc}
                if ev["home_team"] in pr and ev["away_team"] in pr and "Draw" in pr:
                    o["1x2"][title] = [pr[ev["home_team"]], pr["Draw"], pr[ev["away_team"]]]
            elif mk.get("key") == "totals":
                pr = {x["name"]: x["price"] for x in oc if abs(float(x.get("point", 0)) - 2.5) < 1e-9}
                if "Over" in pr and "Under" in pr:
                    o["ou25"][title] = [pr["Over"], pr["Under"]]
    return o


def match_events(events, fixtures, known_names, tz_offset_hours=2):
    """Map API events to our fixtures (same date +-1 day, best name match on both sides)."""
    out, unmatched = {}, []
    for ev in events:
        t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) + timedelta(hours=tz_offset_hours)
        cands = [f for f in fixtures if abs((date.fromisoformat(f["when"][:10]) - t.date()).days) <= 1]
        best, bs = None, 0.0
        for f in cands:
            sh = name_score(ev["home_team"], known_names.get(f["h"], [f["h"]]))
            sa = name_score(ev["away_team"], known_names.get(f["a"], [f["a"]]))
            s = min(sh, sa) + 0.25 * max(sh, sa)
            if s > bs:
                best, bs = f, s
        if best is not None and bs >= 0.75:
            out[(best["div"], best["h"], best["a"], best["when"][:10])] = (ev, round(bs, 2))
        else:
            unmatched.append(f"{ev['home_team']} - {ev['away_team']} ({ev['commence_time'][:10]})")
    return out, unmatched


def merge_odds(store, key, src_odds, stamp):
    cur = store.setdefault("|".join(key), {"1x2": {}, "ou25": {}})
    for mk in ("1x2", "ou25"):
        cur[mk].update(src_odds.get(mk, {}))
    cur["t"] = stamp
    return cur


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)
