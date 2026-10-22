"""History of all betjes: profit or loss (stake 1 unit per bet) and, for every lost bet, where it went wrong:
bad luck (a likely leg that missed by a hair) or a bad pick (an unlikely leg, or a match that went the other way).

Sources in state/: betje.json (betje van de dag; European nights cl/el/ecl/eusafe), superbet_week.json and
superbet_weeks.json (superbet of the week, second chance), superbet_history.json (the old daily superbet),
research.json (the deep-dive verdicts), fd_results.csv (corners/cards/shots for the margin of stats legs) and
analysis.json (Claude's own short analysis per lost bet, written after the deep dive: {bet id: text}).

build(state) returns the "betHistory" object for the page; report(h) a compact version for report.json.
"""
import json
from pathlib import Path

GROUPS = [("betje", "Betje van de dag"), ("cl", "Champions League-avond"), ("el", "Europa League-avond"),
          ("ecl", "Conference League-avond"), ("eusafe", "Europese safe combi"), ("week", "Superbet van de week"),
          ("second", "Second chance"), ("daily", "Superbet van de dag (oud)")]
GNAME = dict(GROUPS)

# family of a market, for "where did it go wrong"
FAMILY = [("nolose", "dubbele kans (1X/X2)"), ("win@", "winst"), ("12", "geen gelijkspel"), ("draw", "gelijkspel"),
          ("btts", "beide teams scoren"), ("o15", "over goals"), ("o25", "over goals"), ("o35", "over goals"),
          ("u25", "under goals"), ("u35k", "kaarten"), ("u35", "under goals"), ("sc1@", "ploeg scoort"), ("sc2@", "ploeg scoort 2+"),
          ("cs@", "de nul houden"), ("htwin", "rust"), ("htg", "rust"), ("mcorn", "corners"), ("c", "corners"),
          ("k", "kaarten"), ("sh", "schoten"), ("sot", "schoten"), ("msh", "schoten")]


def family(mid):
    for pre, name in FAMILY:
        if mid == pre or mid.startswith(pre):
            return name
    return mid


def _load(p, default):
    try:
        return json.load(open(p))
    except Exception:  # noqa: BLE001
        return default


def margin(leg, stats=None):
    """How far a lost leg was from winning: (number, unit). None when unknown."""
    mid, sc = leg["id"], leg.get("score")
    if sc and "-" in str(sc):
        try:
            fh, fa = (int(x) for x in str(sc).split("-")[:2])
        except ValueError:
            fh = fa = None
        if fh is not None:
            t = fh + fa
            g = {"win@H": fa - fh + 1, "win@A": fh - fa + 1, "nolose@H": fa - fh, "nolose@A": fh - fa, "12": 1,
                 "draw": abs(fh - fa), "o15": 2 - t, "o25": 3 - t, "o35": 4 - t, "u25": t - 2, "u35": t - 3,
                 "btts": (fh == 0) + (fa == 0), "bttsno": min(fh, fa), "sc1@H": 1 - fh, "sc1@A": 1 - fa,
                 "sc2@H": 2 - fh, "sc2@A": 2 - fa, "cs@H": fa, "cs@A": fh}.get(mid)
            if g is not None and g > 0:
                return g, "goal"
    if not stats:
        return None
    s = stats
    tot = lambda a, b: (s.get(a) or 0) + (s.get(b) or 0) if s.get(a) is not None and s.get(b) is not None else None  # noqa: E731
    rules = {  # id: (value, threshold, kind) with kind ">=" or "<=" or ">" (home vs away), unit
        "c85": (tot("ch", "ca"), 9, ">=", "corner"), "c95": (tot("ch", "ca"), 10, ">=", "corner"),
        "c105": (tot("ch", "ca"), 11, ">=", "corner"), "u95": (tot("ch", "ca"), 9, "<=", "corner"),
        "c45@H": (s.get("ch"), 5, ">=", "corner"), "c45@A": (s.get("ca"), 5, ">=", "corner"),
        "k35": (tot("kh", "ka"), 4, ">=", "kaart"), "k45": (tot("kh", "ka"), 5, ">=", "kaart"), "u35k": (tot("kh", "ka"), 3, "<=", "kaart"),
        "sh10@H": (s.get("sh"), 10, ">=", "schot"), "sh10@A": (s.get("sa"), 10, ">=", "schot"),
        "sh13@H": (s.get("sh"), 13, ">=", "schot"), "sh13@A": (s.get("sa"), 13, ">=", "schot"),
        "sot3@H": (s.get("th"), 3, ">=", "schot op doel"), "sot3@A": (s.get("ta"), 3, ">=", "schot op doel"),
        "sot5@H": (s.get("th"), 5, ">=", "schot op doel"), "sot5@A": (s.get("ta"), 5, ">=", "schot op doel"),
    }
    cmp = {"mcorn@H": ("ch", "ca", "corner"), "mcorn@A": ("ca", "ch", "corner"), "msh@H": ("sh", "sa", "schot"), "msh@A": ("sa", "sh", "schot")}
    if mid in rules:
        v, t, kind, unit = rules[mid]
        if v is None:
            return None
        m = t - v if kind == ">=" else v - t
        return (int(m), unit) if m > 0 else None
    if mid in cmp:
        a, b, unit = cmp[mid]
        if s.get(a) is None or s.get(b) is None:
            return None
        m = s[b] - s[a] + 1
        return (int(m), unit) if m > 0 else None
    return None


UNIT_PL = {"goal": "goals", "corner": "corners", "kaart": "kaarten", "schot": "schoten", "schot op doel": "schoten op doel"}


def _mtxt(m):
    if not m:
        return ""
    n, unit = m
    return f"het scheelde één {unit}" if n == 1 else f"het scheelde {n} {UNIT_PL.get(unit, unit)}"


def _legp(l):
    return l.get("pb") if l.get("pb") is not None else l.get("p")


def _key(l):
    return f"{l['when'][:10]}|{l['div']}|{l['h']}|{l['a']}"


def judge(bet, research, sidx):
    """Automatic verdict for a lost bet: label and a short Dutch explanation."""
    legs = bet["legs"]
    lost = [l for l in legs if l.get("hit") == 0]
    if not lost:
        return None
    rows, worst = [], []
    for l in lost:
        st = None
        if sidx is not None:
            try:
                import track  # noqa: PLC0415
                from datetime import date  # noqa: PLC0415
                d = date.fromisoformat(l["when"][:10])
                for rd, s in sidx.get((l["div"], l["h"], l["a"]), []):
                    if abs((rd - d).days) <= 1:
                        st = s
                        break
            except Exception:  # noqa: BLE001
                st = None
        m = margin(l, st)
        p = _legp(l)
        r = research.get(_key(l)) or {}
        v = next((x.get("verdict") for x in r.get("markets", []) if x.get("id") == l["id"]), None)
        rows.append({"l": l, "m": m, "p": p, "v": v})
    one = len(rows) == 1
    close = all(r["m"] and r["m"][0] == 1 for r in rows)
    likely = all((r["p"] or 0) >= 0.65 for r in rows)
    risky = any((r["p"] or 1) < 0.6 or r["v"] in ("riskant", "afgeraden") for r in rows)
    wide = any(r["m"] and r["m"][0] >= 3 for r in rows)
    if one and close and likely:
        label = "Pech"
    elif one and likely and not wide:
        label = "Ongelukkig"
    elif risky:
        label = "Te riskant"
    elif close:
        label = "Krap mis"
    elif wide:
        label = "Verkeerd ingeschat"
    elif len(rows) >= 2:
        label = "Slechte avond"
    else:
        label = "Gemengd"
    parts = []
    for r in rows:
        l, p = r["l"], r["p"]
        s = f"{l['h']} – {l['a']}: {l['label']}" + (f" ({l['score']})" if l.get("score") else "")
        bits = []
        if r["m"]:
            bits.append(_mtxt(r["m"]))
        if p:
            bits.append(f"kans {round(p * 100)}%, dus 1 op {max(1, round(1 / max(1e-9, 1 - p)))} dat dit misloopt")
        if r["v"]:
            bits.append(f"onderzoek: {r['v']}")
        parts.append(s + (f" — {'; '.join(bits)}" if bits else ""))
    ok = sum(1 for l in legs if l.get("hit") == 1)
    why = {"Pech": "Eén bet met een grote kans miste op een haar; de rest zat goed.",
           "Ongelukkig": "Eén bet met een behoorlijke kans miste; de rest zat goed.",
           "Te riskant": "Er zat een bet in met weinig kans (onder 60%) of die het onderzoek al riskant vond: daar liep het mis.",
           "Krap mis": "Elke bet die miste, miste op een haar; met zoveel bets in de combi is dat vaak genoeg om te verliezen.",
           "Verkeerd ingeschat": "Een match liep heel anders dan verwacht (ruim mis): de inschatting klopte niet.",
           "Slechte avond": "Meer dan één bet liep duidelijk mis.",
           "Gemengd": "Een mix van pech en een minder goede keuze."}[label]
    odds, pb = bet.get("odds") or 0, bet.get("p") or 0
    if odds >= 10 and pb:
        why += f" Een combi van {str(round(odds, 1)).replace('.', ',')}x wint maar in ±{max(1, round(pb * 100))}% van de gevallen, dus verliezen is hier het normale geval."
    return {"label": label, "why": why, "lost": parts, "legs_ok": ok, "legs": len(legs), "legs_lost": len(lost),
            "legs_open": sum(1 for l in legs if l.get("hit") is None)}


def _eff(b):
    o = 1.0
    for l in b.get("legs", []):
        if l.get("hit") != "void":
            o *= l.get("odd") or 1
    return o


def collect(state):
    state = Path(state)
    B = _load(state / "betje.json", [])
    out = []

    def add(group, bid, day, b):
        if not b or b.get("none") or not b.get("legs"):
            return
        out.append({"id": bid, "group": group, "type": GNAME[group], "name": b.get("name") or GNAME[group], "day": day,
                    "odds": round(b.get("odds") or 0, 2), "p": b.get("p"), "status": b.get("status") or "bezig",
                    "chosen_by": b.get("chosen_by"), "legs": [{k: l.get(k) for k in ("div", "when", "h", "a", "id", "label", "p", "pb", "odd", "score", "hit")}
                                                             for l in b["legs"]], "raw": b})
    for d in B:
        add("betje", f"{d['day']}|betje", d["day"], d)
        for g in ("cl", "el", "ecl", "eusafe"):
            for c in d.get(g) or []:
                add(g, f"{d['day']}|{g}|{c.get('name')}", d["day"], c)
    weeks = _load(state / "superbet_weeks.json", [])
    cur = _load(state / "superbet_week.json", None)
    for w in weeks + ([cur] if cur else []):
        for k, g in (("main", "week"), ("second", "second")):
            b = w.get(k)
            if b:
                add(g, f"{w.get('week')}|{k}", (b.get("made") or w.get("from") or "")[:10], b)
    for d in _load(state / "superbet_history.json", []):  # the old daily superbet (before the weekly one)
        if d.get("settled"):
            b = dict(d, status="gewonnen" if d.get("won") else "verloren", name=d.get("name") or "Superbet van de dag")
            add("daily", f"{d['made']}|daily", d["made"], b)
    # dedupe (a week can be in the archive and the current file)
    seen, res = set(), []
    for x in out:
        if x["id"] in seen:
            continue
        seen.add(x["id"])
        res.append(x)
    return res


def build(state):
    state = Path(state)
    research = _load(state / "research.json", {})
    notes = _load(state / "analysis.json", {})
    sidx = None
    try:
        import pandas as pd  # noqa: PLC0415
        import track  # noqa: PLC0415
        p = state / "fd_results.csv"
        sidx = track.stats_index(pd.read_csv(p)) if p.exists() else None
    except Exception:  # noqa: BLE001
        sidx = None
    bets = collect(state)
    done = [b for b in bets if b["status"] in ("gewonnen", "verloren", "vervallen")]
    for b in bets:
        st = b["status"]
        b["profit"] = round(_eff(b["raw"]) - 1, 2) if st == "gewonnen" else -1.0 if st == "verloren" else 0.0 if st == "vervallen" else None
        if st == "verloren":
            b["judge"] = judge(b["raw"], research, sidx)
        if notes.get(b["id"]):
            b["analysis"] = notes[b["id"]]
        del b["raw"]
    dec = [b for b in done if b["status"] in ("gewonnen", "verloren")]
    staked = len(dec)
    profit = round(sum(b["profit"] for b in dec), 2)
    exp_wins = round(sum(b["p"] or 0 for b in dec), 2)
    exp_profit = round(sum((b["p"] or 0) * b["odds"] for b in dec) - staked, 2)
    legs = [l for b in dec for l in b["legs"] if l.get("hit") in (0, 1)]
    leg_hit = sum(1 for l in legs if l["hit"] == 1)
    leg_p = sum((_legp(l) or 0) for l in legs)
    by = []
    for g, name in GROUPS:
        xs = [b for b in dec if b["group"] == g]
        op = [b for b in bets if b["group"] == g and b["status"] == "bezig"]
        if not xs and not op:
            continue
        by.append({"group": g, "type": name, "n": len(xs), "won": sum(1 for b in xs if b["status"] == "gewonnen"),
                   "profit": round(sum(b["profit"] for b in xs), 2), "open": len(op),
                   "exp_wins": round(sum(b["p"] or 0 for b in xs), 2)})
    fam = {}
    for l in legs:
        f = fam.setdefault(family(l["id"]), {"n": 0, "hit": 0, "p": 0.0})
        f["n"] += 1
        f["hit"] += l["hit"]
        f["p"] += _legp(l) or 0
    fams = sorted(({"family": k, "n": v["n"], "hit": v["hit"], "lost": v["n"] - v["hit"], "p": round(v["p"] / v["n"], 3)}
                   for k, v in fam.items()), key=lambda x: (-x["lost"], x["family"]))
    labels = {}
    for b in dec:
        if b.get("judge"):
            labels[b["judge"]["label"]] = labels.get(b["judge"]["label"], 0) + 1
    lost = [b for b in dec if b["status"] == "verloren"]
    one_off = sum(1 for b in lost if b.get("judge") and b["judge"]["legs_lost"] == 1)
    won = sum(1 for b in dec if b["status"] == "gewonnen")
    if not dec:
        verdict = "Nog geen afgerekende betjes."
    else:
        diff = won - exp_wins
        luck = ("ongeveer zoals verwacht" if abs(diff) < max(1.0, 0.25 * exp_wins) else
                "meer gewonnen dan verwacht: wat geluk" if diff > 0 else "minder gewonnen dan verwacht: wat pech of te optimistische kansen")
        lh = leg_hit / len(legs) if legs else 0
        lp = leg_p / len(legs) if legs else 0
        legtxt = (f" De afzonderlijke bets kwamen {round(lh * 100)}% uit tegen {round(lp * 100)}% voorspeld"
                  + (", dus de kansen kloppen ongeveer." if abs(lh - lp) < 0.05 else
                     ", dus de kansen waren te optimistisch." if lh < lp else ", dus de kansen waren eerder te voorzichtig.")) if legs else ""
        verdict = (f"{won} van de {len(dec)} afgerekende betjes gewonnen, volgens de kansen werd ±{str(exp_wins).replace('.', ',')} verwacht: "
                   f"{luck}.{legtxt} {one_off} van de {len(lost)} verloren betjes misten op maar één bet. "
                   "Combi's van ±5x verlies je meestal (kans ±15%), dus één verloren combi zegt weinig; het gaat om de reeks.")
    hist = {"total": {"settled": len(dec), "won": won, "lost": len(lost), "void": sum(1 for b in done if b["status"] == "vervallen"),
                      "open": sum(1 for b in bets if b["status"] == "bezig"), "staked": staked, "profit": profit,
                      "roi": round(profit / staked, 3) if staked else None, "exp_wins": exp_wins, "exp_profit": exp_profit,
                      "legs": len(legs), "legs_hit": leg_hit, "legs_p": round(leg_p / len(legs), 3) if legs else None,
                      "one_off": one_off, "labels": labels},
            "verdict": verdict, "by": by, "families": fams[:8],
            "bets": sorted(bets, key=lambda b: (b["day"], b["id"]), reverse=True)}
    return hist


def report(h):
    """Compact version for report.json, plus the lost bets that have no analysis by Claude yet."""
    t = h["total"]
    todo = [{"id": b["id"], "name": b["name"], "day": b["day"], "odds": b["odds"], "auto": (b.get("judge") or {}).get("label"),
             "lost_on": (b.get("judge") or {}).get("lost", [])} for b in h["bets"] if b["status"] == "verloren" and not b.get("analysis")][:12]
    return {"total": t, "verdict": h["verdict"], "by": h["by"], "to_analyse": todo}


def apply(state, out):
    """Add the history to out/app_data.json (for the page); returns the report section."""
    out = Path(out)
    h = build(state)
    p = out / "app_data.json"
    data = json.load(open(p))
    data["betHistory"] = h
    json.dump(data, open(p, "w"))
    return report(h)
