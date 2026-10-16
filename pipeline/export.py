"""Export compact app data + a parity set (Python-computed probabilities on the shipped subset).

Usage: python3 -I export.py <Matches.csv> <EloRatings.csv> <outdir> [--recent recent.csv] [--fixtures fixtures.json] [--updated YYYY-MM-DD]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import engine as E  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("matches"); ap.add_argument("elo"); ap.add_argument("out")  # noqa: E702
ap.add_argument("--recent"); ap.add_argument("--fixtures"); ap.add_argument("--updated")  # noqa: E702
ap.add_argument("--players"); ap.add_argument("--cups"); ap.add_argument("--cup-aliases")  # noqa: E702
ap.add_argument("--fd"); ap.add_argument("--odds")  # noqa: E702
args = ap.parse_args()
mpath, epath, out = args.matches, args.elo, Path(args.out)
SHIP_FROM, EXTRA_FROM = "2015-07-01", "2024-07-01"

df = E.load(mpath)

def _dedupe_new(base, new):
    """Rows of `new` whose (division, home, away) has no match in `base` within 3 days."""
    keys = {}
    for r in base[base.MatchDate >= new.MatchDate.min() - pd.Timedelta(days=5)].itertuples():
        keys.setdefault((r.Division, r.HomeTeam, r.AwayTeam), []).append(r.MatchDate)
    keep = [not any(abs((d - r.MatchDate).days) <= 3 for d in keys.get((r.Division, r.HomeTeam, r.AwayTeam), []))
            for r in new.itertuples()]
    return new[keep]


# ---- football-data.co.uk season results (with corners/cards), verified by update.py
if args.fd and Path(args.fd).exists():
    fd = pd.read_csv(args.fd, dtype={"MatchTime": str})
    if len(fd):
        fd["MatchDate"] = pd.to_datetime(fd.MatchDate)
        fd["MatchTime"] = fd.MatchTime.fillna("12:00").map(lambda t: t + ":00" if len(str(t)) == 5 else "12:00:00")
        fd = _dedupe_new(df, fd)
        df = pd.concat([df, fd], ignore_index=True)
        df = df.sort_values(["MatchDate", "MatchTime", "Division", "HomeTeam"]).reset_index(drop=True)
        df["mid"] = np.arange(len(df))
        print(f"football-data results appended: {len(fd)}")

# ---- results newer than the dataset (goals only), verified by update.py
if args.recent and Path(args.recent).exists():
    rec = pd.read_csv(args.recent, dtype={"time": str})
    if len(rec):
        add = pd.DataFrame({"Division": rec["div"], "MatchDate": pd.to_datetime(rec.date),
                            "MatchTime": rec.time.fillna("").map(lambda t: (t + ":00") if len(t) == 5 else "12:00:00"),
                            "HomeTeam": rec.home, "AwayTeam": rec.away,
                            "FTHome": rec.fh.astype(float), "FTAway": rec.fa.astype(float)})
        add = _dedupe_new(df, add)
        df = pd.concat([df, add], ignore_index=True)
        df = df.sort_values(["MatchDate", "MatchTime", "Division", "HomeTeam"]).reset_index(drop=True)
        df["mid"] = np.arange(len(df))
        print(f"recent results appended: {len(add)}")
fixtures = json.load(open(args.fixtures)) if args.fixtures and Path(args.fixtures).exists() else []

keep = (df.Division.isin(E.APP_DIVS) & (df.MatchDate >= SHIP_FROM)) | \
       (df.Division.isin(E.APP_EXTRA_DIVS) & (df.MatchDate >= EXTRA_FROM))
S = df[keep].reset_index(drop=True)
S["mid"] = np.arange(len(S))
divs = list(E.APP_DIVS) + E.APP_EXTRA_DIVS
teams = sorted(set(S.HomeTeam) | set(S.AwayTeam))
# European cups: teams without competition data in the shipped set are added (no matches, Elo only)
cups = json.load(open(args.cups)) if args.cups and Path(args.cups).exists() else {}
cup_alias = json.load(open(args.cup_aliases)) if args.cup_aliases and Path(args.cup_aliases).exists() else {}
foreign = {v["team"]: v.get("elo") for v in cup_alias.values() if v.get("foreign")}
teams += sorted(t for t in foreign if t not in set(teams))
tix = {t: i for i, t in enumerate(teams)}
epoch = np.datetime64("2000-01-01")


def iv(s):
    return [int(x) if pd.notna(x) else -1 for x in s]


data = {
    "dataEnd": str(S.MatchDate.max().date()),
    "divs": divs, "divNames": E.APP_DIVS, "teams": teams,
    "m": {
        "d": ((S.MatchDate.to_numpy() - epoch) // np.timedelta64(1, "D")).astype(int).tolist(),
        "v": [divs.index(x) for x in S.Division], "h": [tix[t] for t in S.HomeTeam],
        "a": [tix[t] for t in S.AwayTeam], "fh": iv(S.FTHome), "fa": iv(S.FTAway),
        "hh": iv(S.HTHome), "ha": iv(S.HTAway), "ch": iv(S.HomeCorners), "ca": iv(S.AwayCorners),
        "kh": iv(S.HomeYellow + S.HomeRed), "ka": iv(S.AwayYellow + S.AwayRed),
        "sh": iv(S.HomeShots), "sa": iv(S.AwayShots), "th": iv(S.HomeTarget), "ta": iv(S.AwayTarget),
    },
}

# current-season teams per app league (season of the league's latest match)
cur = {}
for dv in E.APP_DIVS:
    D = S[S.Division == dv]
    last = D.MatchDate.max()
    season_start = pd.Timestamp(year=last.year if last.month >= 7 else last.year - 1, month=7, day=1)
    T = D[D.MatchDate >= season_start]
    cur[dv] = sorted(set(T.HomeTeam) | set(T.AwayTeam), key=str.lower)
data["leagueTeams"] = cur
def _end(x):
    x = x.MatchDate.max()
    return None if pd.isna(x) else str(x.date())


data["leagueEnd"] = {dv: {"goals": _end(S[S.Division == dv]),
                          "stats": _end(S[(S.Division == dv) & S.HomeCorners.notna()])}
                     for dv in E.APP_DIVS}
cup_teams = {}
for f in fixtures:
    if f["div"] in cups:
        cup_teams.setdefault(f["div"], set()).update([f["h"], f["a"]])
data["cups"] = {c: {"name": v["name"], "teams": sorted(cup_teams.get(c, []), key=str.lower)} for c, v in cups.items()}
ok_fx = [f for f in fixtures if (f["div"] in cur and f["h"] in cur[f["div"]] and f["a"] in cur[f["div"]])
         or (f["div"] in cups and f["h"] in tix and f["a"] in tix)]
if len(ok_fx) != len(fixtures):
    print("dropped fixtures with unknown teams:", [f for f in fixtures if f not in ok_fx])
data["fixtures"] = sorted(ok_fx, key=lambda f: (f["when"], f["div"]))
data["updated"] = args.updated or data["dataEnd"]

el = pd.read_csv(epath)
el["date"] = pd.to_datetime(el.date)
el = el[el.date <= S.MatchDate.max()].sort_values("date").groupby("club").tail(1)
snap = el.date.max()
elo_all = {r.club: float(r.elo) for r in el.itertuples()}
# bring Elo up to date with every result after the snapshot (World Football Elo style)
ELO_K, ELO_HFA = 20.0, 65.0
upd = S[(S.MatchDate >= snap) & S.FTHome.notna() & S.FTAway.notna()]
n_upd = 0
for r in upd.itertuples():
    if r.HomeTeam not in elo_all or r.AwayTeam not in elo_all:
        continue
    rh, ra = elo_all[r.HomeTeam], elo_all[r.AwayTeam]
    exp_h = 1 / (1 + 10 ** (-(rh + ELO_HFA - ra) / 400))
    gd = abs(int(r.FTHome) - int(r.FTAway))
    g = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
    w = 1.0 if r.FTHome > r.FTAway else 0.5 if r.FTHome == r.FTAway else 0.0
    d = ELO_K * g * (w - exp_h)
    elo_all[r.HomeTeam], elo_all[r.AwayTeam] = rh + d, ra - d
    n_upd += 1
print(f"elo updated with {n_upd} results since {snap.date()}")
data["elo"] = {c: round(v, 1) for c, v in elo_all.items() if c in tix}
for t, ename in foreign.items():
    if ename and ename in elo_all:
        data["elo"][t] = round(elo_all[ename], 1)
data["foreign"] = sorted(foreign)
data["eloDate"] = str(upd.MatchDate.max().date()) if n_upd else str(snap.date())
data["players"] = json.load(open(args.players)) if args.players and Path(args.players).exists() else {}
data["odds"] = json.load(open(args.odds)) if args.odds and Path(args.odds).exists() else {}

json.dump(data, open(out / "app_data.json", "w"), separators=(",", ":"))
print("shipped matches", len(S), "teams", len(teams), "elo", len(data["elo"]),
      "size KB", (out / "app_data.json").stat().st_size // 1024)

# ---- parity: Python features/probabilities on the shipped subset for recent matches
model = json.load(open(out / "model.json"))["models"]
L = E.long_table(S)
probe = S[(S.MatchDate >= S.MatchDate.max() - pd.Timedelta(days=45)) & S.Division.isin(["B1", "E0", "SP1", "USA"])].mid.tolist()
par = {}
for ev in E.MATCH_EVENTS + E.TEAM_EVENTS:
    kind = model[ev]["kind"]
    F = E.match_features(L, ev) if kind == "match" else E.team_features(L, ev)
    X = E.X_match(F) if kind == "match" else E.X_team(F)
    z = model[ev]["intercept"] + X @ np.array(model[ev]["coef"])
    F["p"] = 1 / (1 + np.exp(-z))
    F = F[F.mid.isin(probe)]
    for r in F.itertuples():
        key = f"{r.mid}|{getattr(r, 'home', 1)}"
        par.setdefault(key, {})[ev] = None if not np.isfinite(r.p) else round(float(r.p), 6)
nz = lambda x: None if pd.isna(x) else float(x)  # noqa: E731
meta = {int(r.mid): {"h": r.HomeTeam, "a": r.AwayTeam, "eh": nz(r.HomeElo), "ea": nz(r.AwayElo),
                     "div": r.Division, "date": str(r.MatchDate.date())}
        for r in S[S.mid.isin(probe)].itertuples()}
json.dump({"p": par, "meta": meta}, open(out / "parity.json", "w"))
print("parity rows", len(par))
