"""Superbet of the week: settle its legs against the results and archive finished weeks.

state/superbet_week.json  the current week: {"week", "from", "to", "main": {...}, "second": {...} | null}
                          main = "Superbet of the week" (made once, at the first update of the week, never changed)
                          second = "Second chance" (made once when main is lost and enough matches remain)
state/superbet_weeks.json finished weeks (newest last), for the track record

A leg gets "hit": 1 (won), 0 (lost) or "void" (no result 3 days after the match, e.g. postponed),
plus "score". Status per bet: "bezig", "gewonnen" or "verloren". New bets are made in superbet_cli.js.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import track


def week_of(d):
    mon = d - timedelta(days=d.weekday())
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}", mon, mon + timedelta(days=6)


def status(bet):
    hits = [l.get("hit") for l in bet.get("legs", [])]
    if any(h == 0 for h in hits):
        return "verloren"
    if hits and all(h in (1, "void") for h in hits):
        return "gewonnen" if any(h == 1 for h in hits) else "vervallen"
    return "bezig"


def settle_bet(bet, idx, today):
    for l in bet.get("legs", []):
        if l.get("hit") in (0, 1, "void"):
            continue
        d = date.fromisoformat(l["when"][:10])
        res = track.find_result(idx, l["div"], l["h"], l["a"], d)
        if res:
            l["score"] = f"{res[1]}-{res[2]}"
            h = track.outcome(l["id"], res[1], res[2])
            l["hit"] = h if h is not None else "void"
        elif (today - d).days > 3:
            l["hit"] = "void"
    bet["status"] = status(bet)
    bet["legs_hit"] = sum(1 for l in bet.get("legs", []) if l.get("hit") == 1)
    lost = [l for l in bet.get("legs", []) if l.get("hit") == 0]
    bet["lost_on"] = f"{lost[0]['h']} – {lost[0]['a']}: {lost[0]['label']}" if lost else None
    return bet


def run(state, today, idx):
    """Settle the current week's bets; archive the week once it is over. Returns the current week (or None)."""
    state = Path(state)
    wp, ap = state / "superbet_week.json", state / "superbet_weeks.json"
    W = json.load(open(wp)) if wp.exists() else None
    arch = json.load(open(ap)) if ap.exists() else []
    if W:
        for k in ("main", "second"):
            if W.get(k):
                settle_bet(W[k], idx, today)
        wk, _mon, _sun = week_of(today)
        if W.get("week") != wk:
            # a new week has started: archive the old one (its last legs may still settle in later runs)
            arch = [x for x in arch if x.get("week") != W.get("week")] + [W]
            W = None
    # legs of archived weeks that were still open
    for x in arch[-3:]:
        for k in ("main", "second"):
            if x.get(k) and x[k].get("status") == "bezig":
                settle_bet(x[k], idx, today)
    json.dump(arch[-26:], open(ap, "w"), ensure_ascii=False, indent=1)
    if W:
        json.dump(W, open(wp, "w"), ensure_ascii=False, indent=1)
    elif wp.exists():
        wp.unlink()
    return W


def summary(state):
    state = Path(state)
    ap, wp = state / "superbet_weeks.json", state / "superbet_week.json"
    arch = json.load(open(ap)) if ap.exists() else []
    cur = json.load(open(wp)) if wp.exists() else None
    weeks = arch + ([cur] if cur else [])
    done = [w for w in weeks if w.get("main") and w["main"].get("status") in ("gewonnen", "verloren")]
    sec = [w["second"] for w in weeks if w.get("second") and w["second"].get("status") in ("gewonnen", "verloren")]
    return {"weeks_settled": len(done), "won": sum(1 for w in done if w["main"]["status"] == "gewonnen"),
            "second_chances_settled": len(sec), "second_won": sum(1 for s in sec if s["status"] == "gewonnen"),
            "current": None if not cur else {"week": cur.get("week"), "main": cur["main"].get("status"),
                                             "legs_hit": cur["main"].get("legs_hit"), "legs": len(cur["main"].get("legs", [])),
                                             "second": cur["second"].get("status") if cur.get("second") else None}}
