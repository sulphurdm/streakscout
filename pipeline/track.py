"""Track record: store each day's predictions and superbet, settle them against results, summarise.

Used by update.py after the superbet step. Files in state/:
  predictions.csv       one row per predicted bet (league matches, markets settled by the final score)
  superbet_history.json each day's superbet with the outcome of its legs
  track.json            summary of the last 30 days, read by the daily improvement check
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

KEEP_DAYS = 90
COLS = ["made", "date", "div", "h", "a", "id", "g", "p", "base", "lvl", "odd", "priced", "fh", "fa", "hit"]


def outcome(mid, fh, fa):
    """1/0 for a market id given the final score, None if the market is not settled by the score."""
    t = fh + fa
    rules = {
        "win@H": fh > fa, "win@A": fa > fh, "draw": fh == fa, "nolose@H": fh >= fa, "nolose@A": fa >= fh, "12": fh != fa,
        "o15": t >= 2, "o25": t >= 3, "u25": t <= 2, "o35": t >= 4, "u35": t <= 3,
        "btts": fh > 0 and fa > 0, "bttsno": not (fh > 0 and fa > 0),
        "sc1@H": fh >= 1, "sc1@A": fa >= 1, "sc2@H": fh >= 2, "sc2@A": fa >= 2, "cs@H": fa == 0, "cs@A": fh == 0,
    }
    v = rules.get(mid)
    return None if v is None else int(v)


def results_index(recent, fd=None):
    """(div, home, away) -> list of (date, fh, fa) from the verified recent results (and football-data)."""
    idx = {}
    for df, cols in ((recent, ("div", "home", "away", "date", "fh", "fa")),
                     (fd, ("Division", "HomeTeam", "AwayTeam", "MatchDate", "FTHome", "FTAway"))):
        if df is None or not len(df):
            continue
        for r in df[list(cols)].itertuples(index=False):
            dv, h, a, d, fh, fa = r
            if pd.isna(fh) or pd.isna(fa):
                continue
            idx.setdefault((dv, h, a), []).append((pd.Timestamp(d).date(), int(fh), int(fa)))
    return idx


def find_result(idx, dv, h, a, d):
    best = None
    for rd, fh, fa in idx.get((dv, h, a), []):
        if abs((rd - d).days) <= 1 and (best is None or abs((rd - d).days) < abs((best[0] - d).days)):
            best = (rd, fh, fa)
    return best


def summarise(P, today):
    S = P[P.hit.notna() & (pd.to_datetime(P.date) >= pd.Timestamp(today - timedelta(days=30)))]
    out = {"window_days": 30, "settled": int(len(S)), "open": int(P.hit.isna().sum())}
    if not len(S):
        return out

    def grp(df):
        return {"n": int(len(df)), "hit": round(float(df.hit.mean()), 3), "p": round(float(df.p.mean()), 3),
                "diff": round(float(df.hit.mean() - df.p.mean()), 3)}
    out["all"] = grp(S)
    out["by_p"] = {f"{lo:.1f}-{hi:.1f}": grp(S[(S.p >= lo) & (S.p < hi)]) for lo, hi in ((0.6, 0.7), (0.7, 0.8), (0.8, 1.01))
                   if ((S.p >= lo) & (S.p < hi)).any()}
    out["by_group"] = {g: grp(d) for g, d in S.groupby("g")}
    out["by_signal"] = {str(int(k)): grp(d) for k, d in S.groupby("lvl")}
    out["by_league"] = {k: grp(d) for k, d in S.groupby("div") if len(d) >= 15}
    mk = {k: grp(d) for k, d in S.groupby("id") if len(d) >= 15}
    out["markets_worst"] = dict(sorted(mk.items(), key=lambda kv: kv[1]["diff"])[:4])
    out["markets_best"] = dict(sorted(mk.items(), key=lambda kv: -kv[1]["diff"])[:4])
    roi = S.apply(lambda r: (r.odd - 1) if r.hit == 1 else -1.0, axis=1)
    out["flat_stake_roi"] = round(float(roi.mean()), 3)
    out["note"] = "odds are mostly estimates (0.95/p) until bookmaker odds are available; ROI is then not meaningful"
    return out


def run(root, today, out_dir):
    root, state = Path(root), Path(root) / "state"
    rec = pd.read_csv(state / "recent.csv") if (state / "recent.csv").exists() else pd.DataFrame()
    fdp = state / "fd_results.csv"
    fd = pd.read_csv(fdp) if fdp.exists() else None
    idx = results_index(rec, fd)

    # predictions: add today's (latest prediction for a match/market wins), settle, prune
    pp = state / "predictions.csv"
    P = pd.read_csv(pp, dtype={"made": str, "date": str}) if pp.exists() else pd.DataFrame(columns=COLS)
    newp = Path(out_dir) / "predictions.json"
    if newp.exists():
        N = pd.DataFrame(json.load(open(newp)))
        if len(N):
            N["fh"] = N["fa"] = N["hit"] = None
            P = pd.concat([P, N[COLS]], ignore_index=True)
            P = P.drop_duplicates(subset=["date", "div", "h", "a", "id"], keep="last")
    for i, r in P[P.hit.isna()].iterrows():
        res = find_result(idx, r["div"], r["h"], r["a"], date.fromisoformat(str(r["date"])))
        if res:
            P.loc[i, ["fh", "fa", "hit"]] = [res[1], res[2], outcome(r["id"], res[1], res[2])]
    P = P[pd.to_datetime(P.date) >= pd.Timestamp(today - timedelta(days=KEEP_DAYS))]
    P = P[~(P.hit.isna() & (pd.to_datetime(P.date) < pd.Timestamp(today - timedelta(days=7))))]  # never settled
    P.to_csv(pp, index=False)

    # superbet history
    hp = state / "superbet_history.json"
    hist = json.load(open(hp)) if hp.exists() else []
    sbp = state / "superbet.json"
    if sbp.exists():
        sb = json.load(open(sbp))
        hist = [x for x in hist if x.get("made") != sb.get("made")]
        hist.append({"made": sb.get("made"), "odds": sb.get("odds"), "p": round(sb.get("p", 0), 4),
                     "legs": [{k: l.get(k) for k in ("div", "when", "h", "a", "id", "label", "p", "odd")} for l in sb.get("legs", [])]})
    for day in hist:
        for l in day["legs"]:
            if l.get("hit") is None:
                res = find_result(idx, l["div"], l["h"], l["a"], date.fromisoformat(l["when"][:10]))
                if res:
                    l["score"] = f"{res[1]}-{res[2]}"
                    l["hit"] = outcome(l["id"], res[1], res[2])
        hits = [l.get("hit") for l in day["legs"]]
        day["settled"] = all(h is not None for h in hits) and bool(hits)
        day["won"] = (all(h == 1 for h in hits) if day["settled"] else None)
        day["legs_hit"] = sum(1 for h in hits if h == 1)
    hist = [x for x in hist if x.get("made", "9999") >= str(today - timedelta(days=KEEP_DAYS))]
    json.dump(hist, open(hp, "w"), ensure_ascii=False, indent=1)

    summary = summarise(P, today)
    done = [x for x in hist if x["settled"]]
    summary["superbet"] = {"days_settled": len(done), "won": sum(1 for x in done if x["won"]),
                           "legs_hit": sum(x["legs_hit"] for x in done), "legs": sum(len(x["legs"]) for x in done),
                           "last": [{"made": x["made"], "won": x["won"], "legs_hit": x["legs_hit"], "legs": len(x["legs"]),
                                     "odds": x["odds"]} for x in done[-7:]]}
    json.dump(summary, open(state / "track.json", "w"), ensure_ascii=False, indent=1)
    return summary
