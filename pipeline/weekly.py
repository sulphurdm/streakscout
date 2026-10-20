"""Superbet of the week: settle its legs against the results and archive finished weeks.

state/superbet_week.json  the current week: {"week", "from", "to", "main": {...}, "second": {...} | null}
                          main = "Superbet of the week" (made once, at the first update of the week, never changed)
                          second = "Second chance" (made once when main is lost and enough matches remain)
state/superbet_weeks.json finished weeks (newest last), for the track record

state/betje.json          the daily "Betje van de dag" (±5x, value legs only), newest last; days without one have "none"

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


STATS_WAIT = 12  # days to wait for football-data's corners/cards/shots before a stats leg is voided


def _stats_idx(state):
    import pandas as pd
    p = Path(state) / "fd_results.csv"
    return track.stats_index(pd.read_csv(p)) if p.exists() else {}


def settle_bet(bet, idx, today, sidx=None):
    """Settle the legs of one bet (and of its BTTS option). Score markets settle from the results; corners,
    cards, shots and half-time markets from football-data's match statistics (which come in a few days later)."""
    sidx = sidx or {}
    for l in bet.get("legs", []):
        d = date.fromisoformat(l["when"][:10])
        if l.get("hit") == "void" and l["id"] in track.STATS_RULES and not l.get("void_checked") and (today - d).days <= STATS_WAIT:
            l["hit"] = None  # voided too early by an older version: try again
        if l.get("hit") in (0, 1, "void"):
            continue
        res = track.find_result(idx, l["div"], l["h"], l["a"], d)
        if res:
            l["score"] = f"{res[1]}-{res[2]}"
            h = track.outcome(l["id"], res[1], res[2])
            if h is not None:
                l["hit"] = h
                continue
        if l["id"] in track.STATS_RULES:
            h = track.stats_outcome(sidx, l["id"], l["div"], l["h"], l["a"], d)
            if h is not None:
                l["hit"] = h
            elif (today - d).days > STATS_WAIT:
                l["hit"], l["void_checked"] = "void", True
        elif not res and (today - d).days > 3:
            l["hit"] = "void"
        elif res:
            l["hit"] = "void"  # a market the score does not settle and that has no statistics rule
    bet["status"] = status(bet)
    bet["legs_hit"] = sum(1 for l in bet.get("legs", []) if l.get("hit") == 1)
    lost = [l for l in bet.get("legs", []) if l.get("hit") == 0]
    bet["lost_on"] = f"{lost[0]['h']} – {lost[0]['a']}: {lost[0]['label']}" if lost else None
    if bet.get("btts"):
        settle_bet(bet["btts"], idx, today, sidx)
    return bet


def run(state, today, idx):
    """Settle the current week's bets; archive the week once it is over. Returns the current week (or None)."""
    state = Path(state)
    wp, ap = state / "superbet_week.json", state / "superbet_weeks.json"
    W = json.load(open(wp)) if wp.exists() else None
    arch = json.load(open(ap)) if ap.exists() else []
    sidx = _stats_idx(state)
    if W:
        for k in ("main", "second"):
            if W.get(k):
                settle_bet(W[k], idx, today, sidx)
        wk, _mon, _sun = week_of(today)
        if W.get("week") != wk:
            # a new week has started: archive the old one (its last legs may still settle in later runs)
            arch = [x for x in arch if x.get("week") != W.get("week")] + [W]
            W = None
    # legs of archived weeks that were still open
    for x in arch[-3:]:
        for k in ("main", "second"):
            if x.get(k) and (x[k].get("status") == "bezig" or (x[k].get("btts") or {}).get("status") == "bezig"
                             or any(l.get("hit") == "void" and l["id"] in track.STATS_RULES and not l.get("void_checked") for l in x[k]["legs"])):
                settle_bet(x[k], idx, today, sidx)
    json.dump(arch[-26:], open(ap, "w"), ensure_ascii=False, indent=1)
    if W:
        json.dump(W, open(wp, "w"), ensure_ascii=False, indent=1)
    elif wp.exists():
        wp.unlink()
    return W


def run_betje(state, today, idx):
    """Settle the legs of the daily betjes (state/betje.json, newest last; made in superbet_cli.js)."""
    p = Path(state) / "betje.json"
    if not p.exists():
        return []
    B = json.load(open(p))
    sidx = _stats_idx(state)
    for b in B:
        if not b.get("none") and (b.get("status") in (None, "bezig") or (b.get("btts") or {}).get("status") == "bezig"):
            settle_bet(b, idx, today, sidx)
        for c in b.get("cl") or []:  # Champions League-avond combis
            if c.get("status") in (None, "bezig"):
                settle_bet(c, idx, today, sidx)
    json.dump(B, open(p, "w"), ensure_ascii=False, indent=1)
    return B


def betje_summary(state):
    p = Path(state) / "betje.json"
    B = json.load(open(p)) if p.exists() else []
    bets = [b for b in B if not b.get("none")]
    done = [b for b in bets if b.get("status") in ("gewonnen", "verloren")]

    def eff(b):
        o = 1.0
        for leg in b["legs"]:
            if leg.get("hit") != "void":
                o *= leg["odd"]
        return o
    profit = sum(eff(b) - 1 if b["status"] == "gewonnen" else -1 for b in done)
    bt = [b["btts"] for b in B if b.get("btts") and not b["btts"].get("none")]
    bdone = [b for b in bt if b.get("status") in ("gewonnen", "verloren")]
    bprofit = sum(eff(b) - 1 if b["status"] == "gewonnen" else -1 for b in bdone)
    by = {}
    for b in done:
        k = b.get("chosen_by") or "model"
        by.setdefault(k, {"settled": 0, "won": 0})
        by[k]["settled"] += 1
        by[k]["won"] += b["status"] == "gewonnen"
    cl = [c for b in B for c in (b.get("cl") or [])]
    cdone = [c for c in cl if c.get("status") in ("gewonnen", "verloren")]
    cprofit = sum(eff(c) - 1 if c["status"] == "gewonnen" else -1 for c in cdone)
    return {"cl": {"made": len(cl), "settled": len(cdone), "won": sum(1 for c in cdone if c["status"] == "gewonnen"),
                   "profit_units": round(cprofit, 2)},
            "days": len(B), "made": len(bets), "settled": len(done), "won": sum(1 for b in done if b["status"] == "gewonnen"),
            "profit_units": round(profit, 2), "legs_hit": sum(b.get("legs_hit", 0) for b in done),
            "legs": sum(len(b["legs"]) for b in done), "by_chooser": by,
            "btts": {"made": len(bt), "settled": len(bdone), "won": sum(1 for b in bdone if b["status"] == "gewonnen"),
                     "profit_units": round(bprofit, 2)}}


def summary(state):
    state = Path(state)
    ap, wp = state / "superbet_weeks.json", state / "superbet_week.json"
    arch = json.load(open(ap)) if ap.exists() else []
    cur = json.load(open(wp)) if wp.exists() else None
    weeks = arch + ([cur] if cur else [])
    done = [w for w in weeks if w.get("main") and w["main"].get("status") in ("gewonnen", "verloren")]
    sec = [w["second"] for w in weeks if w.get("second") and w["second"].get("status") in ("gewonnen", "verloren")]
    bt = [w[k]["btts"] for w in weeks for k in ("main", "second") if w.get(k) and (w[k].get("btts") or {}).get("status") in ("gewonnen", "verloren")]
    return {"weeks_settled": len(done), "won": sum(1 for w in done if w["main"]["status"] == "gewonnen"),
            "btts_settled": len(bt), "btts_won": sum(1 for b in bt if b["status"] == "gewonnen"),
            "second_chances_settled": len(sec), "second_won": sum(1 for s in sec if s["status"] == "gewonnen"),
            "current": None if not cur else {"week": cur.get("week"), "main": cur["main"].get("status"),
                                             "legs_hit": cur["main"].get("legs_hit"), "legs": len(cur["main"].get("legs", [])),
                                             "second": cur["second"].get("status") if cur.get("second") else None}}
