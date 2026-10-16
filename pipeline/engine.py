"""StreakScout engine: streak features, per-market probability models, backtest, exports.

Usage: python3 -I engine.py <Matches.csv> <EloRatings.csv> <outdir>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

W_ALL, W_VEN, W_H2H, W_BASE, M_SHRINK, STREAK_CAP = 10, 6, 6, 600, 4.0, 15
TRAIN_END, TEST_START = "2023-06-30", "2023-07-01"
TRAIN_START = "2008-07-01"

EURO = ["B1", "D1", "D2", "E0", "E1", "E2", "E3", "EC", "F1", "F2", "G1", "I1", "I2",
        "N1", "P1", "SC0", "SC1", "SC2", "SC3", "SP1", "SP2", "T1"]
APP_DIVS = {
    "B1": "Jupiler Pro League", "E0": "Premier League", "E1": "Championship",
    "SP1": "La Liga", "D1": "Bundesliga", "I1": "Serie A", "F1": "Ligue 1",
    "N1": "Eredivisie", "P1": "Liga Portugal", "SC0": "Scottish Premiership",
    "T1": "Süper Lig", "G1": "Super League Griekenland", "USA": "MLS",
}
# second tiers shipped (recent only) so promoted teams keep their recent form
APP_EXTRA_DIVS = ["D2", "F2", "I2", "SP2", "SC1"]

MATCH_EVENTS = ["o15", "o25", "o35", "btts", "draw", "htg", "c85", "c95", "c105", "k35", "k45"]
TEAM_EVENTS = ["win", "nolose", "sc1", "sc2", "cs", "htwin", "mcorn", "c45", "sh10", "sh13", "sot3", "sot5", "msh"]


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def load(path):
    df = pd.read_csv(path, low_memory=False)
    df = df[df.Division.isin(EURO)].dropna(subset=["FTHome", "FTAway"]).copy()
    df["MatchDate"] = pd.to_datetime(df.MatchDate)
    df["MatchTime"] = df.MatchTime.fillna("00:00:00")
    df = df.sort_values(["MatchDate", "MatchTime", "Division", "HomeTeam"]).reset_index(drop=True)
    df["mid"] = np.arange(len(df))
    return df


def nanwhere(cond, *cols):
    out = cond.astype(float)
    bad = np.zeros(len(out), dtype=bool)
    for c in cols:
        bad |= pd.isna(c).to_numpy()
    out[bad] = np.nan
    return out


def long_table(df):
    def side(home):
        p, q = ("Home", "Away") if home else ("Away", "Home")
        return pd.DataFrame({
            "mid": df.mid, "date": df.MatchDate, "div": df.Division,
            "team": df[p + "Team"], "opp": df[q + "Team"], "home": int(home),
            "gf": df["FT" + p], "ga": df["FT" + q], "hgf": df["HT" + p], "hga": df["HT" + q],
            "cf": df[p + "Corners"], "ca": df[q + "Corners"],
            "kf": df[p + "Yellow"] + df[p + "Red"], "ka": df[q + "Yellow"] + df[q + "Red"],
            "elo": df[p + "Elo"], "eloo": df[q + "Elo"],
            "sf": df[p + "Shots"], "sa": df[q + "Shots"], "tf": df[p + "Target"], "ta": df[q + "Target"],
        })
    L = pd.concat([side(True), side(False)]).sort_values(["mid", "home"], ascending=[True, False])
    L = L.reset_index(drop=True)
    gf, ga, hgf, hga, cf, ca, kf, ka = (L[c] for c in ["gf", "ga", "hgf", "hga", "cf", "ca", "kf", "ka"])
    sf_, sa_, tf, ta = (L[c] for c in ["sf", "sa", "tf", "ta"])
    ev = {
        # match-level
        "o15": nanwhere(gf + ga >= 2, gf), "o25": nanwhere(gf + ga >= 3, gf),
        "o35": nanwhere(gf + ga >= 4, gf), "btts": nanwhere((gf > 0) & (ga > 0), gf),
        "draw": nanwhere(gf == ga, gf), "htg": nanwhere(hgf + hga >= 1, hgf, hga),
        "c85": nanwhere(cf + ca >= 9, cf, ca), "c95": nanwhere(cf + ca >= 10, cf, ca),
        "c105": nanwhere(cf + ca >= 11, cf, ca), "k35": nanwhere(kf + ka >= 4, kf, ka),
        "k45": nanwhere(kf + ka >= 5, kf, ka),
        # own (team perspective)
        "win": nanwhere(gf > ga, gf), "nolose": nanwhere(gf >= ga, gf), "sc1": nanwhere(gf >= 1, gf),
        "sc2": nanwhere(gf >= 2, gf), "cs": nanwhere(ga == 0, ga), "htwin": nanwhere(hgf > hga, hgf, hga),
        "mcorn": nanwhere(cf > ca, cf, ca), "c45": nanwhere(cf >= 5, cf),
        "sh10": nanwhere(sf_ >= 10, sf_), "sh13": nanwhere(sf_ >= 13, sf_),
        "sot3": nanwhere(tf >= 3, tf), "sot5": nanwhere(tf >= 5, tf), "msh": nanwhere(sf_ > sa_, sf_, sa_),
        # against (what the opponent achieves vs this team)
        "a_win": nanwhere(ga > gf, gf), "a_nolose": nanwhere(ga >= gf, gf), "a_sc1": nanwhere(ga >= 1, ga),
        "a_sc2": nanwhere(ga >= 2, ga), "a_cs": nanwhere(gf == 0, gf), "a_htwin": nanwhere(hga > hgf, hgf, hga),
        "a_mcorn": nanwhere(ca > cf, cf, ca), "a_c45": nanwhere(ca >= 5, ca),
        "a_sh10": nanwhere(sa_ >= 10, sa_), "a_sh13": nanwhere(sa_ >= 13, sa_),
        "a_sot3": nanwhere(ta >= 3, ta), "a_sot5": nanwhere(ta >= 5, ta), "a_msh": nanwhere(sa_ > sf_, sf_, sa_),
    }
    for k, v in ev.items():
        L["e_" + k] = v
    return L


def roll(L, col, keys, w):
    """Pre-match hits and count over the group's previous w rows (NaN rows count as rows, not as data)."""
    x = L[col].fillna(0.0)
    c = L[col].notna().astype(float)
    cs = x.groupby([L[k] for k in keys], sort=False).cumsum()
    cc = c.groupby([L[k] for k in keys], sort=False).cumsum()
    tmp = pd.DataFrame({"cs": cs, "cc": cc})
    for k in keys:
        tmp[k] = L[k]
    gg = tmp.groupby(keys, sort=False)
    s1, sw = gg.cs.shift(1).fillna(0), gg.cs.shift(w + 1).fillna(0)
    c1, cw = gg.cc.shift(1).fillna(0), gg.cc.shift(w + 1).fillna(0)
    return (s1 - sw).to_numpy(), (c1 - cw).to_numpy()


def streak(L, col):
    """Signed current streak before the match: +k = event happened in the last k data points, -k = missed k."""
    sub = L.loc[L[col].notna(), ["team", col]].copy()
    out = pd.Series(np.nan, index=L.index)
    sub["prev"] = sub.groupby("team")[col].shift(1)
    sub["new"] = (sub[col] != sub["prev"]).astype(int)
    sub["run"] = sub.groupby("team")["new"].cumsum()
    sub["len"] = sub.groupby(["team", "run"]).cumcount() + 1
    out.loc[sub.index] = np.where(sub[col] == 1, sub["len"], -sub["len"])
    after = out.groupby(L["team"]).ffill()
    pre = after.groupby(L["team"]).shift(1)
    return pre.fillna(0).to_numpy()


def base_rate(L, col, keys):
    hits, cnt = roll(L, col, keys, W_BASE)
    with np.errstate(invalid="ignore", divide="ignore"):
        b = np.where(cnt >= 50, hits / np.maximum(cnt, 1), np.nan)
    return np.clip(b, 0.02, 0.98)


def sf(k, n, base):
    return logit((k + M_SHRINK * base) / (n + M_SHRINK)) - logit(base)


def sstreak(s):
    return np.sign(s) * np.log1p(np.minimum(np.abs(s), STREAK_CAP))


def match_features(L, ev):
    """One row per match (from the home row) for a match-level event."""
    col = "e_" + ev
    ka, na = roll(L, col, ["team"], W_ALL)
    kv, nv = roll(L, col, ["team", "home"], W_VEN)
    kh, nh = roll(L, col, ["team", "opp"], W_H2H)
    st = streak(L, col)
    # base per division on match rows only (home rows)
    H = L[L.home == 1].copy()
    hb, cb = roll(H, col, ["div"], W_BASE)
    base_h = np.clip(np.where(cb >= 50, hb / np.maximum(cb, 1), np.nan), 0.02, 0.98)
    hi = L.index[L.home == 1].to_numpy()
    ai = hi + 1
    out = pd.DataFrame({
        "mid": L.mid.to_numpy()[hi], "date": L.date.to_numpy()[hi], "div": L["div"].to_numpy()[hi],
        "team": L.team.to_numpy()[hi], "opp": L.opp.to_numpy()[hi], "base": base_h,
        "H_k": ka[hi], "H_n": na[hi], "Hv_k": kv[hi], "Hv_n": nv[hi],
        "A_k": ka[ai], "A_n": na[ai], "Av_k": kv[ai], "Av_n": nv[ai],
        "h2h_k": kh[hi], "h2h_n": nh[hi], "H_st": st[hi], "A_st": st[ai],
        "elod": (L.elo.to_numpy()[hi] - L.eloo.to_numpy()[hi]), "y": L[col].to_numpy()[hi],
    })
    return out


def team_features(L, ev):
    """One row per team-match for a team-level event (own event of 'team' vs 'opp')."""
    own, ag = "e_" + ev, "e_a_" + ev
    ka, na = roll(L, own, ["team"], W_ALL)
    kv, nv = roll(L, own, ["team", "home"], W_VEN)
    kh, nh = roll(L, own, ["team", "opp"], W_H2H)
    st = streak(L, own)
    oa, ona = roll(L, ag, ["team"], W_ALL)
    ov, onv = roll(L, ag, ["team", "home"], W_VEN)
    ost = streak(L, ag)
    base = base_rate(L, own, ["div", "home"])
    idx = np.arange(len(L))
    other = np.where(L.home.to_numpy() == 1, idx + 1, idx - 1)
    out = pd.DataFrame({
        "mid": L.mid.to_numpy(), "date": L.date.to_numpy(), "div": L["div"].to_numpy(),
        "team": L.team.to_numpy(), "opp": L.opp.to_numpy(), "home": L.home.to_numpy(), "base": base,
        "T_k": ka, "T_n": na, "Tv_k": kv, "Tv_n": nv, "h2h_k": kh, "h2h_n": nh, "T_st": st,
        "O_k": oa[other], "O_n": ona[other], "Ov_k": ov[other], "Ov_n": onv[other], "O_st": ost[other],
        "elod": L.elo.to_numpy() - L.eloo.to_numpy(), "y": L[own].to_numpy(),
    })
    return out


def X_match(F):
    b = F.base.to_numpy()
    e = np.nan_to_num(F.elod.to_numpy()) / 100
    return np.column_stack([
        sf(F.H_k, F.H_n, b), sf(F.Hv_k, F.Hv_n, b), sf(F.A_k, F.A_n, b), sf(F.Av_k, F.Av_n, b),
        sf(F.h2h_k, F.h2h_n, b), sstreak(F.H_st), sstreak(F.A_st), logit(b), e, np.abs(e),
    ])


def X_team(F):
    b = F.base.to_numpy()
    e = np.nan_to_num(F.elod.to_numpy()) / 100
    return np.column_stack([
        sf(F.T_k, F.T_n, b), sf(F.Tv_k, F.Tv_n, b), sf(F.O_k, F.O_n, b), sf(F.Ov_k, F.Ov_n, b),
        sf(F.h2h_k, F.h2h_n, b), sstreak(F.T_st), sstreak(F.O_st), logit(b), e, F.home.to_numpy(),
    ])


FEATURE_NAMES = {
    "match": ["thuisploeg laatste 10", "thuisploeg thuis laatste 6", "uitploeg laatste 10",
              "uitploeg uit laatste 6", "onderling laatste 6", "reeks thuisploeg", "reeks uitploeg",
              "competitiegemiddelde", "Elo-verschil", "|Elo-verschil|"],
    "team": ["ploeg laatste 10", "ploeg zelfde venue laatste 6", "tegenstander toelaat laatste 10",
             "tegenstander toelaat venue laatste 6", "onderling laatste 6", "reeks ploeg",
             "reeks tegen tegenstander", "competitiegemiddelde", "Elo-verschil", "thuis"],
}


def logloss(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calib_bins(y, p, bins=10):
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = 0, 1
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1]) if i < bins - 1 else (p >= edges[i])
        if m.sum() > 0:
            out.append({"pred": round(float(p[m].mean()), 4), "real": round(float(y[m].mean()), 4), "n": int(m.sum())})
    return out


def main():
    mpath, epath, outdir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    outdir.mkdir(parents=True, exist_ok=True)
    df = load(mpath)
    L = long_table(df)
    print("matches", len(df), "team rows", len(L), flush=True)

    models, report, preds = {}, {}, {}
    for kind, events in (("match", MATCH_EVENTS), ("team", TEAM_EVENTS)):
        for ev in events:
            F = match_features(L, ev) if kind == "match" else team_features(L, ev)
            X = X_match(F) if kind == "match" else X_team(F)
            ok = F.y.notna().to_numpy() & np.isfinite(F.base.to_numpy()) & np.isfinite(X).all(axis=1)
            d = F.date.to_numpy()
            tr = ok & (d >= np.datetime64(TRAIN_START)) & (d <= np.datetime64(TRAIN_END))
            te = ok & (d >= np.datetime64(TEST_START))
            clf = LogisticRegression(C=1.0, max_iter=1000)
            clf.fit(X[tr], F.y.to_numpy()[tr])
            fin = np.isfinite(X).all(axis=1) & np.isfinite(F.base.to_numpy())
            p = np.full(len(X), np.nan)
            p[fin] = clf.predict_proba(X[fin])[:, 1]
            yte, pte, bte = F.y.to_numpy()[te], p[te], F.base.to_numpy()[te]
            report[ev] = {
                "kind": kind, "n_train": int(tr.sum()), "n_test": int(te.sum()),
                "ll_model": logloss(yte, pte), "ll_base": logloss(yte, bte),
                "calib": calib_bins(yte, pte),
                "coef": dict(zip(FEATURE_NAMES[kind], np.round(clf.coef_[0], 4).tolist())),
            }
            models[ev] = {"kind": kind, "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0])}
            F["p"] = p
            F["ok"] = ok
            preds[ev] = F
            r = report[ev]
            print(f"{ev:7s} {kind:5s} train={r['n_train']:6d} test={r['n_test']:6d} "
                  f"LL model={r['ll_model']:.4f} base={r['ll_base']:.4f} "
                  f"gain={100*(r['ll_base']-r['ll_model'])/r['ll_base']:.2f}%", flush=True)

    pd.to_pickle({"preds": preds, "df": df}, outdir / "preds.pkl")
    json.dump({"models": models, "params": {"W_ALL": W_ALL, "W_VEN": W_VEN, "W_H2H": W_H2H,
               "W_BASE": W_BASE, "M": M_SHRINK, "STREAK_CAP": STREAK_CAP}},
              open(outdir / "model.json", "w"))
    json.dump(report, open(outdir / "model_report.json", "w"), indent=1)


if __name__ == "__main__":
    main()
