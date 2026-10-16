"""Backtest streak beliefs and the model against real bookmaker odds.

Usage: python3 -I backtest.py <outdir with preds.pkl>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

out = Path(sys.argv[1])
P = pd.read_pickle(out / "preds.pkl")
preds, df = P["preds"], P["df"]
HIST_START, TEST_START = np.datetime64("2008-07-01"), np.datetime64("2023-07-01")

LABELS = {
    "o15": "Meer dan 1.5 goals", "o25": "Meer dan 2.5 goals", "o35": "Meer dan 3.5 goals",
    "btts": "Beide ploegen scoren", "draw": "Gelijkspel", "htg": "Goal in 1e helft",
    "c85": "Meer dan 8.5 corners", "c95": "Meer dan 9.5 corners", "c105": "Meer dan 10.5 corners",
    "k35": "Meer dan 3.5 kaarten", "k45": "Meer dan 4.5 kaarten",
    "win": "Ploeg wint", "nolose": "Ploeg verliest niet", "sc1": "Ploeg scoort",
    "sc2": "Ploeg scoort 2+", "cs": "Ploeg houdt de nul", "htwin": "Ploeg leidt bij rust",
    "mcorn": "Ploeg meer corners", "c45": "Ploeg 5+ corners",
    "sh10": "Ploeg 10+ schoten", "sh13": "Ploeg 13+ schoten", "sot3": "Ploeg 3+ schoten op doel",
    "sot5": "Ploeg 5+ schoten op doel", "msh": "Ploeg meer schoten",
}


def side_rows(ev):
    """(k_last10, n_last10, streak, y) per team appearance, plus base rate."""
    F = preds[ev]
    F = F[F.y.notna() & (F.date >= HIST_START) & np.isfinite(F.base)]
    if F.empty:
        return pd.DataFrame()
    if "H_k" in F:
        a = pd.DataFrame({"k": F.H_k, "n": F.H_n, "st": F.H_st, "y": F.y, "base": F.base})
        b = pd.DataFrame({"k": F.A_k, "n": F.A_n, "st": F.A_st, "y": F.y, "base": F.base})
        return pd.concat([a, b])
    return pd.DataFrame({"k": F.T_k, "n": F.T_n, "st": F.T_st, "y": F.y, "base": F.base})


reality = {}
for ev in LABELS:
    R = side_rows(ev)
    if R.empty:
        continue
    full = R[R.n == 10]
    by_k = []
    for k in range(11):
        m = full[full.k == k]
        if len(m) >= 200:
            by_k.append({"k": k, "naive": k / 10, "real": round(float(m.y.mean()), 4),
                         "base": round(float(m.base.mean()), 4), "n": int(len(m))})
    by_st = []
    for s in range(1, 16):
        m = R[R.st == s] if s < 10 else R[R.st >= s]
        if len(m) >= 200:
            by_st.append({"s": s, "real": round(float(m.y.mean()), 4), "n": int(len(m)), "plus": s >= 10})
        if s >= 10:
            break
    reality[ev] = {"label": LABELS[ev], "overall": round(float(R.y.mean()), 4),
                   "by_k": by_k, "by_streak": by_st}
    hi = [r for r in by_k if r["k"] >= 8]
    print(f"{ev:7s} overall={R.y.mean():.3f} | " + " ".join(f"{r['k']}/10->{r['real']:.2f}" for r in hi))

# --- both teams strong on a match-level market
both = {}
for ev in ["o15", "o25", "o35", "btts", "c95", "k35"]:
    F = preds[ev]
    F = F[F.y.notna() & (F.date >= HIST_START) & (F.H_n == 10) & (F.A_n == 10)]
    rows = []
    for t in (6, 7, 8, 9):
        m = F[(F.H_k >= t) & (F.A_k >= t)]
        if len(m) >= 100:
            rows.append({"t": t, "real": round(float(m.y.mean()), 4), "n": int(len(m))})
    both[ev] = {"overall": round(float(F.y.mean()), 4), "rows": rows}
    print("both", ev, both[ev])

# --- odds-based backtest on the test period
odds = df.set_index("mid")[["OddHome", "OddDraw", "OddAway", "MaxHome", "MaxDraw", "MaxAway",
                           "Over25", "Under25", "MaxOver25", "MaxUnder25", "Division"]]


def roi(y, o):
    prof = np.where(y == 1, o - 1, -1.0)
    n = len(prof)
    return {"n": int(n), "hit": round(float(y.mean()), 4) if n else None,
            "avg_odd": round(float(o.mean()), 3) if n else None,
            "roi": round(float(prof.mean()), 4) if n else None,
            "se": round(float(prof.std(ddof=1) / np.sqrt(n)), 4) if n > 1 else None}


def market_frame(name):
    if name in ("over25", "under25"):
        F = preds["o25"].copy()
        F = F[F.ok & (F.date >= TEST_START)].join(odds, on="mid")
        F = F.dropna(subset=["Over25", "Under25", "MaxOver25", "MaxUnder25", "p"])
        imp_o = (1 / F.Over25) / (1 / F.Over25 + 1 / F.Under25)
        if name == "over25":
            return pd.DataFrame({"y": F.y, "p": F.p, "book": imp_o, "o": F.Over25, "omax": F.MaxOver25,
                                 "streak": (F.H_n == 10) & (F.A_n == 10) & (F.H_k >= 7) & (F.A_k >= 7),
                                 "div": F.Division})
        return pd.DataFrame({"y": 1 - F.y, "p": 1 - F.p, "book": 1 - imp_o, "o": F.Under25, "omax": F.MaxUnder25,
                             "streak": (F.H_n == 10) & (F.A_n == 10) & (F.H_k <= 3) & (F.A_k <= 3),
                             "div": F.Division})
    if name in ("homewin", "awaywin"):
        F = preds["win"].copy()
        F = F[F.ok & (F.date >= TEST_START)].join(odds, on="mid")
        F = F[F.home == (1 if name == "homewin" else 0)]
        F = F.dropna(subset=["OddHome", "OddDraw", "OddAway", "MaxHome", "MaxAway", "p"])
        s = 1 / F.OddHome + 1 / F.OddDraw + 1 / F.OddAway
        if name == "homewin":
            o, om, imp = F.OddHome, F.MaxHome, (1 / F.OddHome) / s
        else:
            o, om, imp = F.OddAway, F.MaxAway, (1 / F.OddAway) / s
        return pd.DataFrame({"y": F.y, "p": F.p, "book": imp, "o": o, "omax": om,
                             "streak": F.T_st >= 4, "div": F.Division})
    if name == "draw":
        F = preds["draw"].copy()
        F = F[F.ok & (F.date >= TEST_START)].join(odds, on="mid")
        F = F.dropna(subset=["OddHome", "OddDraw", "OddAway", "MaxDraw", "p"])
        s = 1 / F.OddHome + 1 / F.OddDraw + 1 / F.OddAway
        return pd.DataFrame({"y": F.y, "p": F.p, "book": (1 / F.OddDraw) / s, "o": F.OddDraw, "omax": F.MaxDraw,
                             "streak": (F.H_n == 10) & (F.A_n == 10) & (F.H_k >= 4) & (F.A_k >= 4),
                             "div": F.Division})


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


STREAK_RULE = {
    "over25": "beide ploegen 7+/10 over 2.5", "under25": "beide ploegen 7+/10 under 2.5",
    "homewin": "thuisploeg 4+ zeges op rij", "awaywin": "uitploeg 4+ zeges op rij",
    "draw": "beide ploegen 4+/10 gelijk",
}
MKT_LABEL = {"over25": "Meer dan 2.5 goals", "under25": "Minder dan 2.5 goals",
             "homewin": "Thuisploeg wint", "awaywin": "Uitploeg wint", "draw": "Gelijkspel"}
bt = {}
for name in STREAK_RULE:
    D = market_frame(name)
    y = D.y.to_numpy()
    res = {"label": MKT_LABEL[name], "rule": STREAK_RULE[name], "n_matches": int(len(D)),
           "ll_model": round(ll(y, D.p.to_numpy()), 4), "ll_book": round(ll(y, D.book.to_numpy()), 4)}
    res["blind_b365"] = roi(y, D.o.to_numpy())
    res["blind_max"] = roi(y, D.omax.to_numpy())
    m = D.streak.to_numpy()
    res["streak_b365"] = roi(y[m], D.o.to_numpy()[m])
    res["streak_max"] = roi(y[m], D.omax.to_numpy()[m])
    v = (D.p * D.o >= 1.05).to_numpy()
    res["value_b365"] = roi(y[v], D.o.to_numpy()[v])
    vm = (D.p * D.omax >= 1.05).to_numpy()
    res["value_max"] = roi(y[vm], D.omax.to_numpy()[vm])
    bt[name] = res
    print(name, json.dumps(res))

json.dump({"reality": reality, "both": both, "backtest": bt,
           "test_from": "2023-07-01", "hist_from": "2008-07-01"},
          open(out / "history.json", "w"), indent=1)
