"""API-Football (api-sports.io) for StreakScout: player stats per match and injuries.

Runs in the daily task before update.py:
    python3 -I af.py --state R/state --today YYYY-MM-DD [--max-seconds 500]
The key: preferably a network secret on the cloud environment (host v3.football.api-sports.io, header
x-apisports-key without prefix): the proxy adds it to each request and the key never enters the session.
Otherwise API_FOOTBALL_KEY in the environment (then v3.football.api-sports.io must be an allowed domain).
Works on any plan: it reads the plan and the requests left today from /status (free call) and spends
at most that. Free plan: 100 requests a day, 10 a minute. Everything is saved after each call, so a
run that stops (time limit, daily limit) continues where it left off on the next run.

Order of work (most useful first, so a small budget goes to the matches that matter):
 1. /status: plan and requests left today (does not count)
 2. /leagues?current=true, once a week: league id, current season and coverage per competition
 3. season fixture lists, one call per competition: all on the first day, then the oldest 4 a day
 4. injuries and suspensions for our fixtures of today and tomorrow (/injuries?ids=, 20 per call)
 5. player stats of finished matches (/fixtures?ids=, 20 matches per call, each with team statistics
    and per-player minutes, goals, assists, shots, cards): first the latest matches of teams that play
    soon in the most important competitions, then older ones.

State files (in --state): af_state.json, af_fixtures.json, af_players.csv, af_names.json,
af_teamstats.csv, af_injuries.json, af_report.json
"""
import argparse
import csv
import json
import os
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = "https://v3.football.api-sports.io"
TZ = "Europe/Brussels"
# our competition -> (API-Football league id, country, league name); ids are checked against /leagues
LEAGUES = {
    "B1": (144, "Belgium", "Jupiler Pro League"), "E0": (39, "England", "Premier League"),
    "E1": (40, "England", "Championship"), "SP1": (140, "Spain", "La Liga"), "D1": (78, "Germany", "Bundesliga"),
    "I1": (135, "Italy", "Serie A"), "F1": (61, "France", "Ligue 1"), "N1": (88, "Netherlands", "Eredivisie"),
    "P1": (94, "Portugal", "Primeira Liga"), "SC0": (179, "Scotland", "Premiership"), "T1": (203, "Turkey", "Süper Lig"),
    "G1": (197, "Greece", "Super League 1"), "USA": (253, "USA", "Major League Soccer"),
    "CL": (2, "World", "UEFA Champions League"), "EL": (3, "World", "UEFA Europa League"),
    "ECL": (848, "World", "UEFA Europa Conference League"),
}
# which competitions get their player data first when the budget is short (bigger = more important)
PRIORITY = {"CL": 10, "B1": 9.5, "E0": 9, "EL": 8, "SP1": 8, "I1": 7.5, "D1": 7.5, "F1": 7, "N1": 7, "ECL": 6,
            "P1": 6, "T1": 5, "SC0": 4.5, "E1": 4.5, "G1": 4, "USA": 4}
FINISHED = {"FT", "AET", "PEN"}
DEAD = {"PST", "CANC", "ABD", "AWD", "WO", "SUSP", "INT"}
PCOLS = ["fid", "tid", "pid", "pos", "min", "start", "g", "a", "sh", "sot", "y", "r", "pg", "pm", "rt"]
TCOLS = ["fid", "tid", "sh", "sot", "corners", "fouls", "y", "r", "poss", "xg"]
LIST_EVERY_DAYS = 3
LISTS_PER_DAY = 4
KEEP_DAYS = 330


class Stop(Exception):
    """No more calls possible this run (daily limit, key refused, time up)."""


def season_guess(div, today):
    if div == "USA":
        return today.year
    return today.year if today.month >= 7 else today.year - 1


def curl_get(url, key, timeout=60):
    """GET; a key from the environment goes in a header read from stdin, so it never appears in the command line
    or logs. Without one, the network secret on the environment adds the header on the way out."""
    cmd = ["curl", "-sS", "-L", "--max-time", str(timeout), "-D", "-"] + (["-H", "@-"] if key else []) + [url]
    p = subprocess.run(cmd, input=f"x-apisports-key: {key}\n".encode() if key else None, capture_output=True)
    if p.returncode != 0:
        return None, {}, p.stderr.decode(errors="replace")[:300]
    raw, headers, status = p.stdout, {}, None
    while raw.startswith(b"HTTP/"):
        head, _, raw = raw.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        status = int(lines[0].split()[1])
        headers = {ln.split(b":", 1)[0].decode().strip().lower(): ln.split(b":", 1)[1].strip().decode()
                   for ln in lines[1:] if b":" in ln}
    return status, headers, raw


TRANSPORT = curl_get  # tests swap this for a fake API


class Client:
    def __init__(self, key, budget, deadline, log):
        self.key, self.budget, self.deadline, self.log = key, budget, deadline, log
        self.used, self.recent, self.per_min = 0, [], None

    def get(self, path, **params):
        if self.used >= self.budget:
            raise Stop("budget")
        if time.time() > self.deadline:
            raise Stop("tijd")
        url = BASE + path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
        for attempt in range(2):
            self._pace()
            st, hdr, body = TRANSPORT(url, self.key)
            self.recent.append(time.time())
            counted = path != "/status"
            if counted:
                self.used += 1
            if "x-ratelimit-limit" in hdr:
                try:
                    self.per_min = int(hdr["x-ratelimit-limit"])
                except ValueError:
                    pass
            if st is None:
                self.log.append(f"{path}: geen verbinding ({body})")
                if attempt == 0:
                    continue
                raise Stop("geen verbinding met v3.football.api-sports.io: er staat geen network secret voor die website op de omgeving"
                           + (" en het domein staat niet bij de netwerktoegang" if self.key else "") + "")
            try:
                js = json.loads(body)
            except ValueError:
                raise Stop(f"{path}: HTTP {st}, geen JSON") from None
            err = js.get("errors") or {}
            if isinstance(err, list):
                err = {str(i): e for i, e in enumerate(err)}
            if err:
                txt = "; ".join(f"{k}: {v}" for k, v in err.items())
                low = txt.lower()
                if "ratelimit" in low or "too many requests" in low:
                    self.log.append(f"{path}: rate limit, 61 s wachten")
                    if time.time() + 61 > self.deadline:
                        raise Stop("tijd")
                    time.sleep(61)
                    continue
                if "token" in low or "application key" in low or "not subscribed" in low or "suspended" in low:
                    raise Stop(f"sleutel geweigerd of ontbreekt: {txt}")
                if "request limit" in low or ("requests" in err and "limit" in low):
                    raise Stop(f"daglimiet bereikt: {txt}")
                return None, txt
            if st != 200:
                return None, f"HTTP {st}"
            return js.get("response"), None
        return None, "rate limit"

    def _pace(self):
        lim = self.per_min or 10
        now = time.time()
        self.recent = [t for t in self.recent if now - t < 60]
        if len(self.recent) >= max(1, lim - 1):
            wait = 60.5 - (now - self.recent[0])
            if wait > 0:
                if time.time() + wait > self.deadline:
                    raise Stop("tijd")
                time.sleep(wait)


def local(dt):
    """'2026-10-18T14:30:00+02:00' -> '2026-10-18 14:30' (requested in Brussels time)."""
    return (dt or "")[:16].replace("T", " ")


def load_json(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def num(x, default=0):
    if x is None or x == "":
        return default
    try:
        return int(float(str(x).rstrip("%")))
    except ValueError:
        return default


def fnum(x):
    try:
        return round(float(str(x).rstrip("%")), 2)
    except (TypeError, ValueError):
        return ""


def parse_fixture_detail(fx, div):
    """One /fixtures?ids= item -> (fixture row, player rows, team stat rows, names {pid: name})."""
    f, t, g = fx.get("fixture", {}), fx.get("teams", {}), fx.get("goals", {})
    fid = f.get("id")
    hid, aid = (t.get("home") or {}).get("id"), (t.get("away") or {}).get("id")
    row = [div, local(f.get("date")), (f.get("status") or {}).get("short"), hid, aid, g.get("home"), g.get("away"),
           (fx.get("league") or {}).get("round", "")]
    prow, names = [], {}
    for side in fx.get("players") or []:
        tid = (side.get("team") or {}).get("id")
        for pl in side.get("players") or []:
            p = pl.get("player") or {}
            s = (pl.get("statistics") or [{}])[0]
            gm = s.get("games") or {}
            mins = num(gm.get("minutes"))
            if mins <= 0:
                continue  # unused substitute
            names[str(p.get("id"))] = p.get("name") or ""
            sh, gl, cd, pen = s.get("shots") or {}, s.get("goals") or {}, s.get("cards") or {}, s.get("penalty") or {}
            prow.append([fid, tid, p.get("id"), (gm.get("position") or "?")[:1], mins, 0 if gm.get("substitute") else 1,
                         num(gl.get("total")), num(gl.get("assists")), num(sh.get("total")), num(sh.get("on")),
                         num(cd.get("yellow")), num(cd.get("red")), num(pen.get("scored")), num(pen.get("missed")),
                         fnum(gm.get("rating"))])
    trow = []
    for side in fx.get("statistics") or []:
        st = {x.get("type"): x.get("value") for x in side.get("statistics") or []}
        trow.append([fid, (side.get("team") or {}).get("id"), num(st.get("Total Shots"), ""), num(st.get("Shots on Goal"), ""),
                     num(st.get("Corner Kicks"), ""), num(st.get("Fouls"), ""), num(st.get("Yellow Cards"), ""),
                     num(st.get("Red Cards"), ""), num(st.get("Ball Possession"), ""), fnum(st.get("expected_goals"))])
    return fid, row, prow, trow, names


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--max-seconds", type=float, default=500)
    ap.add_argument("--max-calls", type=int, default=None, help="extra cap on calls this run")
    ap.add_argument("--now", help="local time 'YYYY-MM-DD HH:MM' (tests only; default: the clock)")
    args = ap.parse_args(argv)
    state, today = Path(args.state), date.fromisoformat(args.today)
    state.mkdir(parents=True, exist_ok=True)
    now_local = datetime.now().astimezone()
    try:
        from zoneinfo import ZoneInfo
        now_local = datetime.now(ZoneInfo(TZ))
    except Exception:  # noqa: BLE001
        pass
    if args.now:
        now_local = datetime.strptime(args.now, "%Y-%m-%d %H:%M")
    rp = state / "af_report.json"
    S = load_json(state / "af_state.json", {})
    report = {"date": str(today), "status": "?", "calls": 0, "errors": [], "log": []}
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()  # empty: the environment's network secret adds it

    FX = load_json(state / "af_fixtures.json", {})
    names = load_json(state / "af_names.json", {})
    INJ = load_json(state / "af_injuries.json", {})
    pp, tp = state / "af_players.csv", state / "af_teamstats.csv"
    done = set()
    if pp.exists():
        with open(pp) as fh:
            done = {int(r["fid"]) for r in csv.DictReader(fh)}
    # forget old seasons: fixtures, player rows and injuries older than KEEP_DAYS
    cut = str(today - timedelta(days=KEEP_DAYS))
    old = {f for f, v in FX.items() if v[1][:10] < cut}
    if old:
        for f in old:
            FX.pop(f)
        for path in (pp, tp):
            if path.exists():
                with open(path) as fh:
                    rd = csv.reader(fh)
                    head = next(rd, None)
                    keep = [r for r in rd if r and r[0] not in old]
                with open(path, "w", newline="") as fh:
                    w = csv.writer(fh)
                    if head:
                        w.writerow(head)
                    w.writerows(keep)
        done -= {int(f) for f in old}
    INJ = {f: v for f, v in INJ.items() if f in FX and FX[f][1][:10] >= str(today - timedelta(days=2))}
    nodata = S.setdefault("nodata", {})
    for f in [f for f in nodata if f not in FX]:
        nodata.pop(f)
    access = S.setdefault("access", {})
    tnames = S.setdefault("tnames", {})
    deadline = time.time() + args.max_seconds
    log = report["log"]

    def save():
        json.dump(S, open(state / "af_state.json", "w"), ensure_ascii=False)
        json.dump(FX, open(state / "af_fixtures.json", "w"), separators=(",", ":"), ensure_ascii=False)
        json.dump(names, open(state / "af_names.json", "w"), separators=(",", ":"), ensure_ascii=False)
        json.dump(INJ, open(state / "af_injuries.json", "w"), separators=(",", ":"), ensure_ascii=False)

    def append(path, cols, rows):
        new = not path.exists()
        with open(path, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(cols)
            w.writerows(rows)

    more = False
    C = Client(key, budget=10**6, deadline=deadline, log=log)
    try:
        # 1. plan and budget
        resp, err = C.get("/status")
        if err or not isinstance(resp, dict):
            raise Stop(f"status: {err or 'onverwacht antwoord'}")
        sub, req = resp.get("subscription") or {}, resp.get("requests") or {}
        limit_day, current = num(req.get("limit_day"), 100), num(req.get("current"))
        C.budget = max(0, limit_day - current - 1)
        if args.max_calls is not None:
            C.budget = min(C.budget, args.max_calls)
        report.update(plan=sub.get("plan"), active=sub.get("active"), limit_day=limit_day, used_before=current)
        S["plan"] = sub.get("plan")
        if C.budget <= 0:
            raise Stop("daglimiet bereikt")

        # 2. league ids, current seasons, coverage
        L = S.setdefault("leagues", {})
        if S.get("leagues_checked", "") < str(today - timedelta(days=7)) or any(d not in L for d in LEAGUES):
            resp, err = C.get("/leagues", current="true")
            if err:
                report["errors"].append(f"leagues: {err}")
            else:
                by_id = {x["league"]["id"]: x for x in resp or [] if x.get("league")}
                for dv, (lid, country, lname) in LEAGUES.items():
                    x = by_id.get(lid)
                    ok = x and (x.get("country") or {}).get("name", "").lower() == country.lower()
                    if not ok:  # id moved: look the competition up by country and name
                        cand = [y for y in resp or [] if (y.get("country") or {}).get("name", "").lower() == country.lower()
                                and lname.lower().split()[-1] in (y["league"].get("name") or "").lower()]
                        x = cand[0] if len(cand) == 1 else x
                        if x:
                            log.append(f"{dv}: league-id {lid} -> {x['league']['id']} ({x['league']['name']})")
                    if not x:
                        report["errors"].append(f"{dv}: competitie niet gevonden bij API-Football")
                        continue
                    seas = [s for s in x.get("seasons") or [] if s.get("current")] or (x.get("seasons") or [])[-1:]
                    if not seas:
                        continue
                    s = seas[0]
                    cov = s.get("coverage") or {}
                    L[dv] = {"id": x["league"]["id"], "name": x["league"].get("name"), "season": s.get("year"),
                             "start": s.get("start"), "end": s.get("end"),
                             "players": bool((cov.get("fixtures") or {}).get("statistics_players", True)),
                             "injuries": bool(cov.get("injuries", True))}
                S["leagues_checked"] = str(today)
                save()
        for dv, (lid, _c, lname) in LEAGUES.items():
            L.setdefault(dv, {"id": lid, "name": lname, "season": season_guess(dv, today), "players": True, "injuries": True})

        # 3. season fixture lists
        lists = S.setdefault("lists", {})
        order = sorted(LEAGUES, key=lambda d: (lists.get(d, {}).get("season") == L[d]["season"], lists.get(d, {}).get("fetched", ""), -PRIORITY[d]))
        n_lists = 0
        for dv in order:
            meta = lists.get(dv, {})
            fresh = meta.get("season") == L[dv]["season"] and meta.get("fetched", "") > str(today - timedelta(days=LIST_EVERY_DAYS))
            first = meta.get("season") != L[dv]["season"]
            if fresh or (not first and n_lists >= LISTS_PER_DAY):
                continue
            resp, err = C.get("/fixtures", league=L[dv]["id"], season=L[dv]["season"], timezone=TZ)
            n_lists += 1
            if err:
                access[dv] = err
                report["errors"].append(f"{dv} lijst: {err}")
                save()
                continue
            access[dv] = "ok"
            for fx in resp or []:
                f, t, g = fx.get("fixture", {}), fx.get("teams", {}), fx.get("goals", {})
                h, a = t.get("home") or {}, t.get("away") or {}
                if f.get("id") is None or h.get("id") is None:
                    continue
                tnames[str(h["id"])], tnames[str(a["id"])] = h.get("name"), a.get("name")
                FX[str(f["id"])] = [dv, local(f.get("date")), (f.get("status") or {}).get("short"), h["id"], a["id"],
                                    g.get("home"), g.get("away"), (fx.get("league") or {}).get("round", "")]
            lists[dv] = {"season": L[dv]["season"], "fetched": str(today), "n": len(resp or [])}
            save()
        ok_divs = {d for d in LEAGUES if access.get(d) == "ok"}
        if not ok_divs:
            raise Stop("geen enkele competitie toegankelijk: " + "; ".join(f"{d}: {access.get(d)}" for d in LEAGUES if access.get(d))[:400])

        # importance of each team: its next match within a week (competition weight, x1.5 within 2 days)
        today_s, soon2, soon7 = str(today), str(today + timedelta(days=1)), str(today + timedelta(days=7))
        imp = {}
        for fid, (dv, when, st, hid, aid, *_r) in FX.items():
            if st in FINISHED or st in DEAD or not (today_s <= when[:10] <= soon7):
                continue
            w = PRIORITY.get(dv, 3) * (1.5 if when[:10] <= soon2 else 1.0)
            for tid in (hid, aid):
                imp[tid] = max(imp.get(tid, 1.0), w)

        # 4. injuries for our fixtures today and tomorrow
        up = [fid for fid, (dv, when, st, *_r) in FX.items()
              if dv in ok_divs and L[dv].get("injuries", True) and today_s <= when[:10] <= soon2 and st not in FINISHED | DEAD
              and INJ.get(fid, {}).get("t") != today_s]
        up.sort(key=lambda f: (FX[f][1], -PRIORITY.get(FX[f][0], 3)))
        n_inj = 0
        for i in range(0, len(up), 20):
            batch = up[i:i + 20]
            resp, err = C.get("/injuries", ids="-".join(batch), timezone=TZ)
            if err:
                report["errors"].append(f"blessures: {err}")
                break
            rows = {f: [] for f in batch}
            for x in resp or []:
                fid = str((x.get("fixture") or {}).get("id"))
                p = x.get("player") or {}
                if fid in rows:
                    rows[fid].append([(x.get("team") or {}).get("id"), p.get("id"), p.get("name"), p.get("type"), p.get("reason")])
            for f, r in rows.items():
                INJ[f] = {"t": today_s, "rows": r}
            n_inj += len(batch)
            save()
        report["injuries_fixtures"] = n_inj

        # 5. player stats of finished matches, most useful first
        by_team = {}
        for fid, (dv, when, st, hid, aid, *_r) in FX.items():
            if dv not in ok_divs or not L[dv].get("players", True):
                continue
            played = st in FINISHED or (st not in DEAD and when < (now_local - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"))
            if not played:
                continue
            for tid in (hid, aid):
                by_team.setdefault(tid, []).append((when, fid))
        rank = {}
        for tid, lst in by_team.items():
            for r, (_w, fid) in enumerate(sorted(lst, reverse=True)):
                rank[fid] = min(rank.get(fid, 99), r)
        cutoff = str(today - timedelta(days=KEEP_DAYS))

        def score(fid):
            dv, when, st, hid, aid, *_r = FX[fid]
            r = rank.get(fid, 99)
            rw = 1.0 if r < 5 else 0.7 if r < 10 else 0.4 if r < 15 else 0.1
            return max(imp.get(hid, 1.0), imp.get(aid, 1.0)) * rw + 0.01 * PRIORITY.get(dv, 3)

        todo = [fid for fid in rank if int(fid) not in done and nodata.get(fid, 0) < 3 and FX[fid][1] >= cutoff]
        todo.sort(key=score, reverse=True)
        report["todo_before"] = len(todo)
        got = 0
        for i in range(0, len(todo), 20):
            batch = todo[i:i + 20]
            resp, err = C.get("/fixtures", ids="-".join(batch), timezone=TZ)
            if err:
                report["errors"].append(f"spelersdata: {err}")
                for f in batch:
                    nodata[f] = nodata.get(f, 0) + 1
                save()
                continue
            P, T = [], []
            seen = set()
            for fx in resp or []:
                fid = str((fx.get("fixture") or {}).get("id"))
                if fid not in FX:
                    continue
                seen.add(fid)
                _fid, row, prow, trow, nm = parse_fixture_detail(fx, FX[fid][0])
                FX[fid] = row
                if row[2] in FINISHED and prow:
                    P += prow
                    T += trow
                    names.update(nm)
                    done.add(int(fid))
                    got += 1
                elif row[2] in FINISHED:
                    nodata[fid] = nodata.get(fid, 0) + 1  # finished but no player data (yet)
            for f in batch:
                if f not in seen:
                    nodata[f] = nodata.get(f, 0) + 1
            append(pp, PCOLS, P)
            append(tp, TCOLS, T)
            save()
        report["fixtures_fetched"] = got
        more = False
    except Stop as e:
        reason = str(e)
        if reason == "tijd":
            more = True
            report["status_detail"] = "tijdslimiet van deze run, de volgende run gaat verder"
        elif reason == "budget":
            report["status_detail"] = "dagbudget op"
        else:
            report["errors"].append(reason)
    finally:
        save()
        report["calls"] = C.used
        report["remaining"] = max(0, (report.get("limit_day") or 0) - (report.get("used_before") or 0) - C.used)
    # summary per competition
    per = {}
    for fid, (dv, when, st, *_r) in FX.items():
        x = per.setdefault(dv, {"fixtures": 0, "finished": 0, "with_players": 0})
        x["fixtures"] += 1
        if st in FINISHED:
            x["finished"] += 1
            if int(fid) in done:
                x["with_players"] += 1
    for dv in LEAGUES:
        per.setdefault(dv, {})["access"] = access.get(dv, "nog niet geprobeerd")
    report["per_comp"] = per
    fin = sum(x.get("finished", 0) for x in per.values())
    withp = sum(x.get("with_players", 0) for x in per.values())
    report["coverage"] = f"{withp}/{fin} gespeelde matchen met spelersdata"
    hard = [e for e in report["errors"] if e.startswith(("sleutel", "daglimiet", "geen verbinding", "geen enkele", "status"))]
    report["key_source"] = "omgevingsvariabele" if key else "network secret (of geen)"
    report["status"] = "fout" if hard else "ok"
    report["more"] = more
    json.dump(report, open(rp, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: report.get(k) for k in ("status", "plan", "calls", "remaining", "coverage", "more", "errors", "status_detail")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
