"""Daily StreakScout update: merge freshly fetched results/fixtures, verify them, rebuild the page.

Layout of --root:
  data/cfmd/data/Matches.csv, EloRatings.csv   (git clone of Club-Football-Match-Data)
  fetched/<DIV>.txt                             (raw lines "speeldag;dd/mm;hh:mm;home;away;score")
  state/recent.csv, state/fixtures.json         (previous state, may be missing)
Outputs: state/recent.csv, state/fixtures.json, state/report.json, out/*, dist/streakscout.html

Usage: python3 -I update.py --root ROOT --today YYYY-MM-DD
"""
import argparse
import json
import re
import shutil
import os
import unicodedata
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as S  # noqa: E402

HERE = Path(__file__).resolve().parent
LINE = re.compile(r"^\s*(?:speeldag\s*)?(\d+)\s*;\s*(\d{1,2})/(\d{1,2})\s*;\s*([0-9]{1,2}:[0-9]{2})?\s*;(.+?);(.+?);\s*(\d{1,2})?\s*-?\s*(\d{1,2})?\s*$")
FIXTURE_DAYS = 9
INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"), None)


def clean(name):
    """Names copied from web pages can carry soft hyphens, zero-width characters or odd spacing."""
    return " ".join(unicodedata.normalize("NFC", name).translate(INVISIBLE).split())


def norm_aliases(a):
    return {clean(k): v for k, v in a.items()}


def season_year(today):
    return today.year if today.month >= 7 else today.year - 1


def parse(path, aliases, today):
    start = season_year(today)
    rows, unknown, bad = [], set(), []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("COUNT") or raw.lower().startswith("speeldag;"):
            continue
        m = LINE.match(re.sub(r"^\s*speeldag\s*", "", raw, flags=re.I))
        if not m:
            if ";" in raw:
                bad.append(raw)
            continue
        rnd, dd, mm, hhmm, h, a, fh, fa = m.groups()
        h, a = clean(h), clean(a)
        for n in (h, a):
            if n not in aliases:
                unknown.add(n)
        mm, dd = int(mm), int(dd)
        y = start if mm >= 7 else start + 1
        try:
            d = date(y, mm, dd)
        except ValueError:
            bad.append(raw)
            continue
        played = fh is not None and fa is not None
        rows.append({"round": int(rnd), "date": d, "time": hhmm or "", "h": aliases.get(h, h), "a": aliases.get(a, a),
                     "fh": int(fh) if played else None, "fa": int(fa) if played else None})
    return rows, unknown, bad


def find(known, h, a, d):
    """Known result for the same pairing within 3 days."""
    hit = known.get((h, a))
    if not hit:
        return None
    best = min(hit, key=lambda r: abs((r[0] - d).days))
    return best if abs((best[0] - d).days) <= 3 else None


def cup_rounds_to_fetch(cups, learned, today, back=1):
    """Round pages worth fetching today: rounds starting within [today-back, today+9],
    plus a round with an unknown date in the week after the previous round (to learn its date)."""
    out = []
    for code, c in cups.items():
        dates = {int(k): date.fromisoformat(v) for k, v in {**c.get("dates", {}), **learned.get(code, {})}.items()}
        if not dates:
            out.append((code, 1))  # nothing known yet: start with round 1
            continue
        for n in range(1, c["rounds"] + 1):
            d = dates.get(n)
            if d is not None:
                if today - timedelta(days=back) <= d <= today + timedelta(days=FIXTURE_DAYS):
                    out.append((code, n))
            else:
                prev = dates.get(n - 1)
                if prev is not None and today - timedelta(days=1) <= prev + timedelta(days=7) and prev <= today + timedelta(days=FIXTURE_DAYS):
                    out.append((code, n))
    return out


def league_rounds_to_fetch(leagues, learned, today):
    """Round-based leagues (MLS): recent and upcoming rounds; results of a round can come in up to 4 days
    after its first match. Between seasons, probe round 1 on Mondays to pick up the new season."""
    rl = {dv: m for dv, m in leagues.items() if "rounds_url" in m}
    out = cup_rounds_to_fetch(rl, learned, today, back=4)
    # rounds with a postponed match: also fetch while their last match is near
    for dv in rl:
        for k, v in learned.get(dv + "@last", {}).items():
            d = date.fromisoformat(v)
            if today - timedelta(days=4) <= d <= today + timedelta(days=FIXTURE_DAYS) and (dv, int(k)) not in out:
                out.append((dv, int(k)))
    for dv, m in rl.items():
        if any(c == dv for c, _ in out):
            continue
        known = [date.fromisoformat(v) for v in learned.get(dv, {}).values()]
        if known and max(known) < today - timedelta(days=30) and today.weekday() == 0:
            out.append((dv, 1))
    return out


def parse_players(path, aliases):
    rows, bad, unknown = [], [], set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        p = [x.strip() for x in raw.split(";")]
        if len(p) != 5 or raw.startswith("COUNT"):
            if raw.strip() and not raw.startswith(("COUNT", "```")):
                bad.append(raw)
            continue
        name, team, g, mins, pen = p
        name, team = clean(name), clean(team)
        if not (g.isdigit() and mins.isdigit() and (pen.isdigit() or pen == "")):
            bad.append(raw)
            continue
        if team not in aliases:
            unknown.add(team)
            continue
        rows.append([aliases[team], name, int(g), int(mins), int(pen or 0)])
    return rows, bad, unknown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--plan", action="store_true", help="only print which files to fetch today")
    ap.add_argument("--no-net", action="store_true", help="skip football-data.co.uk and The Odds API")
    ap.add_argument("--odds-key", default=os.environ.get("ODDS_API_KEY"), help="The Odds API key")
    args = ap.parse_args()
    root = Path(args.root)
    today = date.fromisoformat(args.today)
    leagues = json.load(open(HERE / "leagues.json"))
    aliases = {dv: norm_aliases(a) for dv, a in json.load(open(HERE / "aliases.json")).items()}
    cups = json.load(open(HERE / "cups.json"))
    cup_alias = norm_aliases(json.load(open(HERE / "cup_aliases.json")))
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    cdp = state / "cup_dates.json"
    learned = json.load(open(cdp)) if cdp.exists() else {}
    rounds = cup_rounds_to_fetch(cups, learned, today)
    lrounds = league_rounds_to_fetch(leagues, learned, today)
    if args.plan:
        files = [f"{dv}.txt" for dv, m in leagues.items() if "rounds_url" not in m] + \
                [f"{c}_r{n}.txt" for c, n in lrounds] + [f"{c}_r{n}.txt" for c, n in rounds] + ["PLAYERS_B1.txt"]
        print(json.dumps({"fetch": files}))
        return

    df = pd.read_csv(root / "data/cfmd/data/Matches.csv", low_memory=False, usecols=["Division", "MatchDate", "HomeTeam", "AwayTeam", "FTHome", "FTAway"])
    df = df.dropna(subset=["FTHome", "FTAway"]).copy()
    df["MatchDate"] = pd.to_datetime(df.MatchDate).dt.date
    report = {"today": str(today), "leagues": {}, "football_data": {}, "odds": {}}

    # ---- football-data.co.uk season results (same source as the dataset, with corners and cards)
    fdp = state / "fd_results.csv"
    fd_all = pd.read_csv(fdp, dtype={"MatchTime": str}) if fdp.exists() else pd.DataFrame()
    if len(fd_all):
        fd_all["MatchDate"] = pd.to_datetime(fd_all.MatchDate).dt.date
    if not args.no_net:
        parts = []
        all_names = set(df.HomeTeam) | set(df.AwayTeam)
        for dv in leagues:
            st = report["football_data"].setdefault(dv, {})
            res, err = S.fd_results(dv, today)
            old = fd_all[fd_all.Division == dv] if len(fd_all) else fd_all
            if err or res is None:
                st.update(status="niet bereikbaar", error=err)
                parts.append(old)
                continue
            # football-data can spell a club differently from the dataset (e.g. a new club): map close names
            names = all_names | set(aliases.get(dv, {}).values())
            ren, unk = {}, []
            for t in sorted(set(res.HomeTeam) | set(res.AwayTeam)):
                if t in names:
                    continue
                sc = {k: S.name_score(t, [k]) for k in names}
                k = max(sc, key=sc.get) if sc else None
                if k is not None and sc[k] >= 0.75 and k not in set(res.HomeTeam) | set(res.AwayTeam):
                    ren[t] = k
                else:
                    unk.append(t)
            if unk:
                st.update(status="fout", error="onbekende ploegnamen: " + ", ".join(unk))
                parts.append(old)
                continue
            if ren:
                res = res.replace({"HomeTeam": ren, "AwayTeam": ren})
                st["renamed"] = ren
            m = res.merge(df[df.Division == dv], on=["Division", "HomeTeam", "AwayTeam", "MatchDate"], suffixes=("", "_ds"))
            bad = m[(m.FTHome != m.FTHome_ds) | (m.FTAway != m.FTAway_ds)]
            if len(bad) > max(1, 0.05 * len(m)):
                st.update(status="fout", error=f"{len(bad)} van {len(m)} uitslagen wijken af van de dataset")
                parts.append(old)
                continue
            st.update(status="ok", rows=int(len(res)), checked=int(len(m)),
                      last=str(max(res.MatchDate)) if len(res) else None,
                      corners=int(res.HomeCorners.notna().sum()) if "HomeCorners" in res else 0)
            parts.append(res)
        fd_all = pd.concat([x for x in parts if len(x)], ignore_index=True) if any(len(x) for x in parts) else fd_all
        if len(fd_all):
            fd_all.to_csv(fdp, index=False)

    rp = state / "recent.csv"
    recent = pd.read_csv(rp, dtype={"time": str}) if rp.exists() else pd.DataFrame(columns=["div", "date", "time", "home", "away", "fh", "fa"])
    if len(recent):
        recent["date"] = pd.to_datetime(recent.date).dt.date
    fxp = state / "fixtures.json"
    old_fx = json.load(open(fxp)) if fxp.exists() else []

    cfp = state / "conflicts.json"
    conflicts = json.load(open(cfp)) if cfp.exists() else {}
    new_conflicts = {}
    new_recent = [recent]
    fixtures = []
    for dv, meta in leagues.items():
        st = {"name": meta["name"], "status": "ok", "added": 0, "overlap": 0, "fixtures": 0, "warnings": []}
        report["leagues"][dv] = st
        keep_old_fx = [x for x in old_fx if x["div"] == dv and x["when"][:10] >= str(today)]
        if "rounds_url" in meta:
            # one page per round: combine the rounds fetched today, learn their dates
            mine = sorted(n for c, n in lrounds if c == dv)
            files = [(n, root / "fetched" / f"{dv}_r{n}.txt") for n in mine]
            files = [(n, f) for n, f in files if f.exists()]
            if not files:
                st["status"] = "niets op te halen" if not mine else "niet opgehaald"
                fixtures += keep_old_fx
                continue
            rows, unknown, bad = [], set(), []
            for n, f in files:
                r_, u_, b_ = parse(f, aliases.get(dv, {}), today)
                r_ = [r for r in r_ if r["round"] == n]
                rows += r_
                unknown |= u_
                bad += b_
                if r_:
                    first = min(r["date"] for r in r_)
                    old1 = learned.get(dv, {}).get("1")
                    if n == 1 and old1 and first > date.fromisoformat(old1) + timedelta(days=60):
                        learned[dv], learned[dv + "@last"] = {}, {}  # a new season: forget last season's rounds
                        st["warnings"].append("nieuw seizoen gedetecteerd")
                    learned.setdefault(dv, {})[str(n)] = str(first)
                    learned.setdefault(dv + "@last", {})[str(n)] = str(max(r["date"] for r in r_))
            st["rounds"] = mine
        else:
            f = root / "fetched" / f"{dv}.txt"
            if not f.exists():
                st["status"] = "niet opgehaald"
                fixtures += keep_old_fx
                continue
            rows, unknown, bad = parse(f, aliases.get(dv, {}), today)
        errs = []
        if unknown:
            errs.append("onbekende ploegnamen: " + ", ".join(sorted(unknown)))
        if bad:
            st["warnings"].append(f"{len(bad)} onleesbare regels")
        if not rows:
            errs.append("geen wedstrijden gevonden in de opgehaalde tekst")
        known = {}
        D = df[df.Division == dv]
        for r in D.itertuples():
            known.setdefault((r.HomeTeam, r.AwayTeam), []).append((r.MatchDate, int(r.FTHome), int(r.FTAway), "dataset"))
        FDv = fd_all[fd_all.Division == dv] if len(fd_all) else fd_all
        for r in FDv.itertuples():
            known.setdefault((r.HomeTeam, r.AwayTeam), []).append((r.MatchDate, int(r.FTHome), int(r.FTAway), "dataset"))
        R = recent[recent["div"] == dv] if len(recent) else recent
        for r in R.itertuples():
            known.setdefault((r.home, r.away), []).append((r.date, int(r.fh), int(r.fa), "recent"))
        played = [r for r in rows if r["fh"] is not None and r["date"] <= today]
        seen = {}
        for r in played:
            for t in (r["h"], r["a"]):
                if (t, r["date"]) in seen:
                    errs.append(f"{t} speelt twee keer op {r['date']}")
                seen[(t, r["date"])] = 1
            if r["fh"] > 15 or r["fa"] > 15:
                errs.append(f"onwaarschijnlijke score {r['h']}-{r['a']} {r['fh']}-{r['fa']}")
        new, fixes, dmis = [], [], []
        for r in played:
            k = find(known, r["h"], r["a"], r["date"])
            if k is None:
                new.append(r)
                continue
            st["overlap"] += 1
            if (k[1], k[2]) == (r["fh"], r["fa"]):
                continue
            key = f"{dv}|{r['h']}|{r['a']}|{k[0]}"
            if k[3] == "dataset":
                dmis.append(f"score wijkt af van de dataset: {r['h']}-{r['a']} {r['date']} dataset {k[1]}-{k[2]}, voetbalkrant {r['fh']}-{r['fa']}")
            elif conflicts.get(key) == [r["fh"], r["fa"]]:
                fixes.append((r, k))  # two fetches in a row agree on the new score: correct our earlier row
            else:
                new_conflicts[key] = [r["fh"], r["fa"]]
                st["warnings"].append(f"score wijkt af van eerdere ophaling: {r['h']}-{r['a']} {k[1]}-{k[2]} vs {r['fh']}-{r['fa']} (wacht op bevestiging)")
        # one-off disagreements with the dataset happen between sources; many mean a bad fetch
        if dmis:
            if st["overlap"] >= 10 and len(dmis) <= max(1, 0.05 * st["overlap"]):
                st["warnings"] += [m + " (dataset behouden)" for m in dmis]
            else:
                errs += dmis
        if played:
            lo, hi = min(r["date"] for r in played), max(r["date"] for r in played)
            if "rounds_url" in meta:  # round pages can span months (postponed matches): only compare match days
                days = {r["date"] for r in played}
                expect = sum(1 for v in known.values() for k in v if k[0] in days)
            else:
                expect = sum(1 for v in known.values() for k in v if lo <= k[0] <= hi)
            if expect >= 3 and st["overlap"] / expect < 0.6:
                errs.append(f"onvolledig: {st['overlap']} van {expect} bekende matchen in het venster teruggevonden")
        if errs:
            st["status"] = "fout"
            st["errors"] = errs[:10]
            fixtures += keep_old_fx
            continue
        if new:
            new_recent.append(pd.DataFrame([{"div": dv, "date": r["date"], "time": r["time"], "home": r["h"],
                                             "away": r["a"], "fh": r["fh"], "fa": r["fa"]} for r in new]))
        for r, k in fixes:
            m = (recent["div"] == dv) & (recent.home == r["h"]) & (recent.away == r["a"]) & (recent.date == k[0])
            recent.loc[m, ["fh", "fa"]] = [r["fh"], r["fa"]]
            st["warnings"].append(f"score gecorrigeerd: {r['h']}-{r['a']} {k[1]}-{k[2]} -> {r['fh']}-{r['fa']}")
        st["added"] = len(new)
        fx = [r for r in rows if r["fh"] is None and today <= r["date"] <= today + timedelta(days=FIXTURE_DAYS)]
        fixtures += [{"div": dv, "round": f"Speeldag {r['round']}", "when": f"{r['date']} {r['time']}".strip(),
                      "h": r["h"], "a": r["a"]} for r in sorted(fx, key=lambda r: (r["date"], r["time"]))]
        st["fixtures"] = len(fx)

    # ---- European cups: fixtures only (results are not used as features)
    cup_fx_files = {(c, n) for c, n in rounds}
    for code, c in cups.items():
        st = {"name": c["name"], "status": "ok", "fixtures": 0, "warnings": []}
        report["leagues"][code] = st
        fetched_any = False
        for (cc, n) in sorted(cup_fx_files):
            if cc != code:
                continue
            f = root / "fetched" / f"{code}_r{n}.txt"
            if not f.exists():
                st["warnings"].append(f"speeldag {n} niet opgehaald")
                continue
            fetched_any = True
            rows, unknown, bad = parse(f, {k: v["team"] for k, v in cup_alias.items()}, today)
            if unknown:
                st["warnings"].append("onbekende ploegen overgeslagen: " + ", ".join(sorted(unknown)))
            if rows:
                learned.setdefault(code, {})[str(n)] = str(min(r["date"] for r in rows))
            known = {v["team"] for v in cup_alias.values()}
            fx = [r for r in rows if r["fh"] is None and today <= r["date"] <= today + timedelta(days=FIXTURE_DAYS)
                  and r["h"] in known and r["a"] in known]
            fixtures += [{"div": code, "round": f"Speeldag {n}", "when": f"{r['date']} {r['time']}".strip(),
                          "h": r["h"], "a": r["a"]} for r in sorted(fx, key=lambda r: (r["date"], r["time"]))]
            st["fixtures"] += len(fx)
        if not fetched_any:
            keep = [x for x in old_fx if x["div"] == code and x["when"][:10] >= str(today)]
            fixtures += keep
            st["fixtures"] = len(keep)
            st["status"] = "niets op te halen" if not [r for r in rounds if r[0] == code] else "niet opgehaald"

    # ---- player goals (Jupiler Pro League only: the only league where the source publishes them)
    pp = state / "players.json"
    players = json.load(open(pp)) if pp.exists() else {}
    f = root / "fetched" / "PLAYERS_B1.txt"
    pst = {"name": "Spelers Jupiler Pro League", "status": "niet opgehaald", "warnings": []}
    report["leagues"]["PLAYERS_B1"] = pst
    if f.exists():
        prow, bad, unknown = parse_players(f, aliases["B1"])
        errs = []
        if unknown:
            errs.append("onbekende ploegen: " + ", ".join(sorted(unknown)))
        if len(prow) < 20:
            errs.append(f"maar {len(prow)} spelers gelezen")
        # sanity: a team's players cannot have scored more league goals than the team did this season
        allres = pd.concat([df[df.Division == "B1"].rename(columns={"MatchDate": "date", "HomeTeam": "home", "AwayTeam": "away", "FTHome": "fh", "FTAway": "fa"})[["date", "home", "away", "fh", "fa"]],
                            *[x[x["div"] == "B1"][["date", "home", "away", "fh", "fa"]] for x in new_recent if len(x)]])
        allres = allres[allres.date >= date(season_year(today), 7, 1)]
        tg = {}
        for r in allres.itertuples():
            tg[r.home] = tg.get(r.home, 0) + int(r.fh)
            tg[r.away] = tg.get(r.away, 0) + int(r.fa)
        per = {}
        for t, *_rest in prow:
            per[t] = per.get(t, 0) + _rest[1]
        for t, g in per.items():
            if g > tg.get(t, 0):
                errs.append(f"{t}: spelers {g} goals, ploeg maar {tg.get(t, 0)}")
        if errs:
            pst["status"] = "fout"
            pst["errors"] = errs[:10]
        else:
            players["B1"] = {"updated": str(today), "rows": prow}
            pst["status"] = "ok"
            pst["players"] = len(prow)
        if bad:
            pst["warnings"].append(f"{len(bad)} onleesbare regels")
    json.dump(players, open(pp, "w"), ensure_ascii=False)
    json.dump(learned, open(cdp, "w"))

    rec = pd.concat([x for x in new_recent if len(x)], ignore_index=True) if any(len(x) for x in new_recent) else recent
    # drop results the git dataset now contains itself (it carries corners/cards too)
    if len(rec):
        dkeys = {}
        for r in df[df.MatchDate >= today - timedelta(days=400)].itertuples():
            dkeys.setdefault((r.Division, r.HomeTeam, r.AwayTeam), []).append(r.MatchDate)
        for r in (fd_all.itertuples() if len(fd_all) else []):
            dkeys.setdefault((r.Division, r.HomeTeam, r.AwayTeam), []).append(r.MatchDate)
        keep = [not any(abs((d - r.date).days) <= 3 for d in dkeys.get((r.div, r.home, r.away), [])) for r in rec.itertuples()]
        report["pruned"] = int(len(rec) - sum(keep))
        rec = rec[keep].sort_values(["date", "div", "home"])
    rec.to_csv(rp, index=False)
    json.dump(fixtures, open(fxp, "w"), ensure_ascii=False, indent=0)
    json.dump(new_conflicts, open(cfp, "w"), ensure_ascii=False)

    # ---- bookmaker odds for upcoming fixtures
    op, omp = state / "odds.json", state / "odds_meta.json"
    odds = json.load(open(op)) if op.exists() else {}
    ometa = json.load(open(omp)) if omp.exists() else {}
    odds = {k: v for k, v in odds.items() if k.split("|")[3] >= str(today)}
    stamp = S.now_utc().strftime("%Y-%m-%dT%H:%MZ")
    fx_by = {}
    for f in fixtures:
        fx_by.setdefault((f["div"], f["h"], f["a"]), []).append(f)
    if not args.no_net:
        fo, err = S.fd_fixture_odds(list(leagues))
        n = 0
        for (dv, h, a, d), o in (fo or {}).items():
            for f in fx_by.get((dv, h, a), []):
                if abs((date.fromisoformat(f["when"][:10]) - date.fromisoformat(d)).days) <= 1:
                    S.merge_odds(odds, (dv, h, a, f["when"][:10]), o, stamp)
                    n += 1
        report["odds"]["football-data"] = {"status": "ok" if fo is not None else "niet bereikbaar", "matches": n, "error": err}
        if args.odds_key:
            known_names = {}
            for dv, al in aliases.items():
                for k, v in al.items():
                    known_names.setdefault(v, [v]).append(k)
            for k, v in cup_alias.items():
                known_names.setdefault(v["team"], [v["team"]]).append(k)
            soon = sorted({f["div"] for f in fixtures if str(today) <= f["when"][:10] <= str(today + timedelta(days=2))})
            remaining = ometa.get("remaining")
            for comp in soon:
                if comp not in S.ODDS_SPORTS or ometa.get("fetched", {}).get(comp) == str(today):
                    continue
                if remaining is not None and int(float(remaining)) < 30:
                    report["odds"]["the-odds-api"] = {"status": "gestopt", "reason": f"nog {remaining} credits over"}
                    break
                start = S.now_utc()
                events, hdr, err = S.odds_api_fetch(comp, args.odds_key, start, start + timedelta(days=4))
                remaining = hdr.get("x-requests-remaining", remaining)
                st = report["odds"].setdefault("the-odds-api", {}).setdefault(comp, {})
                if err:
                    st.update(status="fout", error=err[:200])
                    continue
                matched, unmatched = S.match_events(events, [f for f in fixtures if f["div"] == comp], known_names)
                for key_, (ev, score) in matched.items():
                    S.merge_odds(odds, key_, S.odds_from_event(ev), stamp)
                st.update(status="ok", events=len(events), matched=len(matched), unmatched=unmatched[:10])
                ometa.setdefault("fetched", {})[comp] = str(today)
            ometa["remaining"] = remaining
    json.dump(odds, open(op, "w"), ensure_ascii=False)
    json.dump(ometa, open(omp, "w"))
    report["odds"]["fixtures_with_odds"] = len(odds)

    out = root / "out"
    out.mkdir(exist_ok=True)
    for fn in ("model.json", "history.json"):
        shutil.copy(HERE / fn, out / fn)
    d = root / "data/cfmd/data"
    run = lambda *c: subprocess.run(c, check=True)  # noqa: E731
    run(sys.executable, "-I", str(HERE / "export.py"), str(d / "Matches.csv"), str(d / "EloRatings.csv"), str(out),
        "--recent", str(rp), "--fixtures", str(fxp), "--updated", str(today),
        "--players", str(pp), "--cups", str(HERE / "cups.json"), "--cup-aliases", str(HERE / "cup_aliases.json"),
        "--fd", str(fdp), "--odds", str(op))
    run("node", str(HERE / "parity_test.js"), str(out))
    sbp = state / "superbet.json"
    run("node", str(HERE / "superbet_cli.js"), str(out), str(today), str(sbp))
    sb = json.load(open(sbp))
    report["superbet"] = {"legs": len(sb["legs"]), "odds": sb["odds"], "p": round(sb["p"], 4),
                          "bets": [f"{x['when']} {x['h']}-{x['a']}: {x['label']} ({x['odd']})" for x in sb["legs"]]}
    try:  # track record (predictions vs results); never blocks the update
        import track
        tr = track.run(root, today, out)
        report["track"] = {k: tr.get(k) for k in ("settled", "open", "all", "superbet")}
    except Exception as e:  # noqa: BLE001
        report["track"] = {"error": str(e)}
    run(sys.executable, "-I", str(HERE / "build.py"), str(out), str(root / "dist" / "streakscout.html"))
    report["recent_rows"] = int(len(rec))
    report["fixtures_total"] = len(fixtures)
    json.dump(report, open(state / "report.json", "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
