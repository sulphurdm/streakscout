"""Player features for the page from the API-Football data that af.py stores.

build(state, today, fixtures, results, known_names, overrides) -> (page_data, report)
  state        folder with af_fixtures.json, af_players.csv, af_names.json, af_injuries.json, af_state.json
  fixtures     our upcoming fixtures (fixtures.json rows: div, when, h, a) with dataset team names
  results      DataFrame div, date, home, away, fh, fa: this season's results with dataset names
  known_names  {dataset name: [name variants]} (aliases), to match API-Football team names
  overrides    {API-Football team name or id: dataset name}, manual fixes (pipeline/af_aliases.json)

Team names: an API-Football team is mapped to a dataset team by matching its fixtures to ours
(same competition, date within a day, same score when both are known, names alike); a team whose
opponent is already mapped is placed by that match too. The mapping is kept in state/af_map.json.

Per player (for his current team): rates per 90 minutes of goals, assists, shots on target, shots
and yellow cards, weighted to recent matches (half-life 6 team matches) and pulled to the average of
his position (as if he had played k extra matches at that average). Expected minutes and the chance
he plays come from the last 5 team matches. Per team: what its opponents score, shoot and get booked
for against it, for the opponent factor in the page.
"""
import csv
import json
from datetime import date, timedelta
from pathlib import Path

import sources as S

HALF_LIFE = 6.0
LAST = 5
LAST_W = [1.0, 0.85, 0.7, 0.55, 0.45]
PRIOR_K = {"g": 4.0, "a": 4.0, "sot": 3.0, "sh": 2.0, "y": 5.0}
STATS = ["g", "a", "sot", "sh", "y"]
FINISHED = {"FT", "AET", "PEN"}


def _load(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def map_teams(FX, tnames, fixtures, results, known_names, overrides, old):
    """{af team id (str): dataset name}."""
    recs = {}  # div -> list of (date, home, away, fh, fa)
    for r in results.itertuples():
        recs.setdefault(r.div, []).append((r.date, r.home, r.away, r.fh, r.fa))
    for f in fixtures:
        recs.setdefault(f["div"], []).append((date.fromisoformat(f["when"][:10]), f["h"], f["a"], None, None))
    variants = lambda n: known_names.get(n, [n])  # noqa: E731
    votes = {}

    def vote(tid, name, w=1.0):
        votes.setdefault(str(tid), {}).setdefault(name, 0.0)
        votes[str(tid)][name] += w

    sim_cache = {}

    def sim(tid, name):
        k = (tid, name)
        if k not in sim_cache:
            sim_cache[k] = S.name_score(tnames.get(str(tid), ""), variants(name))
        return sim_cache[k]

    pending = []
    for fid, (dv, when, st, hid, aid, gh, ga, *_r) in FX.items():
        try:
            d = date.fromisoformat(when[:10])
        except ValueError:
            continue
        cands = [c for c in recs.get(dv, []) if abs((c[0] - d).days) <= 1]
        if not cands:
            continue
        best, bs = None, 0.0
        for c in cands:
            sh, sa = sim(hid, c[1]), sim(aid, c[2])
            s = min(sh, sa) + 0.25 * max(sh, sa)
            if gh is not None and c[3] is not None:
                s += 0.4 if (int(gh), int(ga)) == (int(c[3]), int(c[4])) else -0.6
            if s > bs:
                best, bs = c, s
        if best is not None and bs >= 0.8:
            vote(hid, best[1])
            vote(aid, best[2])
        else:
            pending.append((dv, d, hid, aid, gh, ga, cands))

    def decide():
        m = {}
        for tid, v in votes.items():
            name, n = max(v.items(), key=lambda x: x[1])
            if n >= 2 * (sum(v.values()) - n) and n >= 1:
                m[tid] = name
        return m

    m = decide()
    # a team whose opponent is known: the fixture on that date with the known team on the same side
    for _ in range(2):
        for dv, d, hid, aid, gh, ga, cands in pending:
            for known_side, other_side, tk, to in ((1, 2, hid, aid), (2, 1, aid, hid)):
                if str(tk) in m and str(to) not in m:
                    hits = [c for c in cands if c[known_side] == m[str(tk)]
                            and (gh is None or c[3] is None or (int(gh), int(ga)) == (int(c[3]), int(c[4])))]
                    if len(hits) == 1:
                        vote(to, hits[0][other_side], 0.5)
        m = decide()
    out = dict(old)
    out.update(m)
    for k, v in (overrides or {}).items():
        if str(k) in tnames:
            out[str(k)] = v
        else:
            for tid, nm in tnames.items():
                if nm == k:
                    out[tid] = v
    # two API teams on one dataset name: keep the one with most votes
    by_name = {}
    for tid, nm in out.items():
        by_name.setdefault(nm, []).append(tid)
    for nm, tids in by_name.items():
        if len(tids) > 1:
            keep = max(tids, key=lambda t: votes.get(t, {}).get(nm, 0))
            for t in tids:
                if t != keep:
                    out.pop(t)
    return out


def build(state, today, fixtures, results, known_names, overrides=None):
    state = Path(state)
    rep = {"status": "geen data"}
    FX = _load(state / "af_fixtures.json", {})
    pp = state / "af_players.csv"
    if not FX or not pp.exists():
        return None, rep
    St = _load(state / "af_state.json", {})
    tnames = St.get("tnames", {})
    names = _load(state / "af_names.json", {})
    INJ = _load(state / "af_injuries.json", {})
    amap = map_teams(FX, tnames, fixtures, results, known_names, overrides, _load(state / "af_map.json", {}))
    json.dump(amap, open(state / "af_map.json", "w"), ensure_ascii=False)
    rev = {v: k for k, v in amap.items()}

    rows = {}
    with open(pp) as fh:
        for r in csv.DictReader(fh):
            try:
                fid = str(int(r["fid"]))
                x = {"tid": str(int(r["tid"])), "pid": str(int(r["pid"])), "pos": r["pos"] or "?", "min": int(r["min"]),
                     "start": int(r["start"]), "g": int(r["g"]), "a": int(r["a"]), "sh": int(r["sh"]), "sot": int(r["sot"]),
                     "y": int(r["y"]), "r": int(r["r"]), "pg": int(r["pg"]), "pm": int(r["pm"])}
            except (ValueError, KeyError, TypeError):
                continue
            rows.setdefault(fid, []).append(x)
    # team matches with player data, newest first
    team_fx = {}
    for fid, pl in rows.items():
        if fid not in FX:
            continue
        dv, when, st, hid, aid, gh, ga, *_r = FX[fid]
        for tid, opp, home, gf, gag in ((str(hid), str(aid), 1, gh, ga), (str(aid), str(hid), 0, ga, gh)):
            team_fx.setdefault(tid, []).append({"fid": fid, "when": when, "opp": opp, "home": home, "gf": gf, "ga": gag})
    for v in team_fx.values():
        v.sort(key=lambda x: x["when"], reverse=True)

    # per-team totals and conceded (what opponents did against it)
    tot = {}
    for fid, pl in rows.items():
        if fid not in FX:
            continue
        _dv, _w, _st, hid, aid, gh, ga, *_r = FX[fid]
        sums = {}
        for x in pl:
            s = sums.setdefault(x["tid"], {"g": 0, "sh": 0, "sot": 0, "y": 0})
            for k in ("g", "sh", "sot", "y"):
                s[k] += x[k]
        for tid, opp, home, gf in ((str(hid), str(aid), 1, gh), (str(aid), str(hid), 0, ga)):
            me, them = sums.get(tid), sums.get(opp)
            if me is None or them is None:
                continue
            t = tot.setdefault(tid, {"n": 0, "gf": 0, "c": [0, 0, 0, 0], "f": [0, 0, 0, 0], "home": [0, 0, 0, 0, 0], "away": [0, 0, 0, 0, 0]})
            t["n"] += 1
            t["gf"] += int(gf) if gf is not None else me["g"]
            for i, k in enumerate(("g", "sh", "sot", "y")):
                t["c"][i] += them[k]
                t["f"][i] += me[k]
            side = t["home"] if home else t["away"]
            side[0] += 1
            for i, k in enumerate(("g", "sh", "sot", "y")):
                side[i + 1] += me[k]
    N = sum(t["n"] for t in tot.values())
    if not N:
        return None, rep
    avg = {k: round(sum(t["f"][i] for t in tot.values()) / N, 3) for i, k in enumerate(("g", "sh", "sot", "y"))}
    hn, an = sum(t["home"][0] for t in tot.values()), sum(t["away"][0] for t in tot.values())
    vf = {}
    for i, k in enumerate(("g", "sh", "sot", "y")):
        h = sum(t["home"][i + 1] for t in tot.values()) / max(1, hn)
        a = sum(t["away"][i + 1] for t in tot.values()) / max(1, an)
        m = (h + a) / 2 or 1
        vf[k] = [round(h / m, 3), round(a / m, 3)]

    # position priors per 90 minutes
    pri = {}
    for pl in rows.values():
        for x in pl:
            p = pri.setdefault(x["pos"], {"min": 0, **{k: 0 for k in STATS}})
            p["min"] += x["min"]
            for k in STATS:
                p[k] += x[k]
    prior = {pos: {k: (v[k] / (v["min"] / 90)) if v["min"] else 0 for k in STATS} for pos, v in pri.items()}

    # teams in our upcoming fixtures
    want = {f["h"] for f in fixtures} | {f["a"] for f in fixtures}
    teams_out = {}
    nplayers = 0
    for tname in sorted(want):
        tid = rev.get(tname)
        if tid is None or tid not in team_fx:
            continue
        seq = team_fx[tid]
        by_p = {}
        for i, tf in enumerate(seq):
            for x in rows.get(tf["fid"], []):
                if x["tid"] == tid:
                    by_p.setdefault(x["pid"], {})[i] = x
        plist = []
        pens = {pid: sum(x["pg"] + x["pm"] for x in ms.values()) for pid, ms in by_p.items()}
        pen_taker = max(pens, key=pens.get) if pens and max(pens.values()) > 0 else None
        for pid, ms in by_p.items():
            last = [ms.get(i) for i in range(min(LAST, len(seq)))]
            wsum = sum(LAST_W[: len(last)])
            played = [(LAST_W[i], x) for i, x in enumerate(last) if x]
            p_play = sum(w for w, _ in played) / wsum if wsum else 0
            if p_play < 0.2:
                continue
            e_min = sum(w * x["min"] for w, x in played) / sum(w for w, _ in played)
            if e_min < 8:
                continue
            pos = max((x["pos"] for x in ms.values()), key=lambda q: sum(1 for x in ms.values() if x["pos"] == q))
            if pos == "G":
                continue
            pr = prior.get(pos) or prior.get("M") or {k: 0 for k in STATS}
            wmin = sum(0.5 ** (i / HALF_LIFE) * x["min"] / 90 for i, x in ms.items())
            rates = []
            for k in STATS:
                wx = sum(0.5 ** (i / HALF_LIFE) * x[k] for i, x in ms.items())
                rates.append(round((wx + PRIOR_K[k] * pr[k]) / (wmin + PRIOR_K[k]), 3))
            season = [len(ms), sum(x["min"] for x in ms.values())] + [sum(x[k] for x in ms.values()) for k in ("g", "a", "sot", "sh", "y")]
            form = []
            for i in range(min(LAST, len(seq)) - 1, -1, -1):  # oldest first
                x = ms.get(i)
                form.append([x["min"], x["g"], x["a"], x["sot"], x["sh"], x["y"]] if x else None)
            plist.append([pid, names.get(pid, "?"), pos, round(p_play, 2), round(e_min), *rates,
                          1 if pid == pen_taker else 0, sum(1 for x in last if x and x["start"]), season, form])
        plist.sort(key=lambda p: -p[3] * p[4])
        plist = plist[:18]
        nplayers += len(plist)
        t = tot.get(tid, {"n": 0, "gf": 0, "c": [0, 0, 0, 0]})
        n = max(1, t["n"])
        teams_out[tname] = {"n": t["n"], "gf": round(t["gf"] / n, 3), "c": [round(c / n, 3) for c in t["c"]], "pl": plist}

    # injuries / suspensions for our fixtures
    inj = {}
    for fid, v in INJ.items():
        if fid not in FX:
            continue
        dv, when, st, hid, aid, *_r = FX[fid]
        if when[:10] < str(today):
            continue
        h, a = amap.get(str(hid)), amap.get(str(aid))
        if not h or not a:
            continue
        sides = {"H": [], "A": []}
        for tid, pid, nm, typ, reason in v.get("rows", []):
            side = "H" if str(tid) == str(hid) else "A" if str(tid) == str(aid) else None
            if side:
                sides[side].append([str(pid), names.get(str(pid)) or nm, "uit" if typ == "Missing Fixture" else "twijfel", reason or ""])
        inj[f"{h}|{a}"] = {"d": when[:10], "t": v.get("t"), **sides}

    unmapped = sorted({tnames.get(str(t), str(t)) for f in FX.values() for t in (f[3], f[4])
                       if str(t) not in amap and f[1][:10] >= str(today - timedelta(days=60)) and f[1][:10] <= str(today + timedelta(days=9))})
    rep = {"status": "ok", "teams": len(teams_out), "players": nplayers, "fixtures_with_players": len(rows),
           "mapped_teams": len(amap), "teams_wanted": len(want), "teams_missing": sorted(want - set(teams_out))[:40],
           "unmapped_api_teams": unmapped[:40], "injury_fixtures": len(inj)}
    page = {"upd": str(today), "plan": St.get("plan"), "avg": avg, "vf": vf, "t": teams_out, "inj": inj}
    return page, rep


def settle(state, preds):
    """preds: list of dicts with date, h, a, pid, mk -> adds hit (1/0), void (player did not play) or None (no data yet)."""
    state = Path(state)
    FX = _load(state / "af_fixtures.json", {})
    amap = _load(state / "af_map.json", {})
    pp = state / "af_players.csv"
    if not FX or not pp.exists():
        return preds
    rows = {}
    with open(pp) as fh:
        for r in csv.DictReader(fh):
            rows.setdefault(r["fid"], {})[r["pid"]] = r
    idx = {}
    for fid, (dv, when, st, hid, aid, *_r) in FX.items():
        h, a = amap.get(str(hid)), amap.get(str(aid))
        if h and a:
            idx.setdefault((h, a), []).append((when[:10], fid))
    for p in preds:
        if p.get("hit") in (0, 1, "void"):
            continue
        fid = None
        for d, f in idx.get((p["h"], p["a"]), []):
            if abs((date.fromisoformat(d) - date.fromisoformat(p["date"])).days) <= 1:
                fid = f
        if fid is None or fid not in rows:
            continue
        x = rows[fid].get(str(p["pid"]))
        if x is None or int(x["min"]) <= 0:
            p["hit"] = "void"
            continue
        g, a, sot, sh, y = (int(x[k]) for k in ("g", "a", "sot", "sh", "y"))
        p["hit"] = int({"sc": g >= 1, "ga": g + a >= 1, "as": a >= 1, "sot": sot >= 1, "sh2": sh >= 2, "yc": y >= 1}[p["mk"]])
        p["min"] = int(x["min"])
    return preds
