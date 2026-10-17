// Player bets per match from the API-Football player data (data.af), shared by the page and superbet_cli.js.
// Per player: his weighted, shrunk rate per 90 minutes x expected minutes (when he plays) x a match factor:
//   goals/assists: the model's expected goals of his team in this match vs his team's average
//   shots/shots on target/yellow cards: what this opponent concedes vs the average, and home/away.
// Chances are "if he plays": bookmakers void player bets when the player does not take part.
(function (root) {
  const MKTS = [
    { id: "sc", l: "scoort", s: "Scoort", st: [0], k: 1 },
    { id: "ga", l: "scoort of geeft een assist", s: "Goal of assist", st: [0, 1], k: 1 },
    { id: "as", l: "geeft een assist", s: "Assist", st: [1], k: 1 },
    { id: "sot", l: "1+ schot op doel", s: "Schot op doel", st: [2], k: 1 },
    { id: "sh2", l: "2+ schoten", s: "2+ schoten", st: [3], k: 2 },
    { id: "yc", l: "krijgt geel", s: "Gele kaart", st: [4], k: 1 },
  ];
  const POS = { D: "verdediger", M: "middenvelder", F: "aanvaller", "?": "speler" };
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const atLeast = (lam, k) => (k <= 1 ? 1 - Math.exp(-lam) : 1 - Math.exp(-lam) * (1 + lam));
  // index of each stat in a player row: [pid, name, pos, pPlay, eMin, rg, ra, rsot, rsh, ry, pen, starts5, season, form]
  const R = { g: 5, a: 6, sot: 7, sh: 8, y: 9 };
  const FORM = { sc: [1], ga: [1, 2], as: [2], sot: [3], sh2: [4], yc: [5] };

  function teamFactors(af, team, opp, side, pScores) {
    const T = af.t[team], O = af.t[opp], avg = af.avg, vi = side === "H" ? 0 : 1;
    const lamAvg = (T.gf * T.n + avg.g * 3) / (T.n + 3);
    let fg = 1;
    if (Number.isFinite(pScores) && pScores > 0) fg = clamp(-Math.log(1 - Math.min(0.97, pScores)) / Math.max(0.3, lamAvg), 0.55, 1.8);
    const opf = (i, key) => {
      if (!O || !O.n) return 1;
      const c = (O.c[i] * O.n + avg[key] * 6) / (O.n + 6);
      return clamp(c / avg[key], 0.7, 1.4);
    };
    return {
      g: fg, a: fg,
      sot: opf(2, "sot") * af.vf.sot[vi],
      sh: opf(1, "sh") * af.vf.sh[vi],
      y: opf(3, "y") * af.vf.y[vi],
    };
  }

  /** all player bets for one fixture; pScores = {H: P(home scores), A: P(away scores)} from the team model */
  function playerBets(data, h, a, pScores) {
    const af = data.af;
    if (!af || !af.t) return { list: [], inj: null, have: { H: false, A: false } };
    const inj = (af.inj || {})[`${h}|${a}`] || null;
    const out = [];
    const have = { H: !!af.t[h], A: !!af.t[a] };
    for (const [team, opp, side] of [[h, a, "H"], [a, h, "A"]]) {
      const T = af.t[team];
      if (!T) continue;
      const f = teamFactors(af, team, opp, side, pScores ? pScores[side] : NaN);
      const out_ = new Map(((inj && inj[side]) || []).map((x) => [x[0], x]));
      for (const pl of T.pl) {
        const [pid, name, pos, pPlay, eMin] = pl;
        const ij = out_.get(pid);
        if (ij && ij[2] === "uit") continue;
        const lam = { g: pl[R.g] * eMin / 90 * f.g, a: pl[R.a] * eMin / 90 * f.a, sot: pl[R.sot] * eMin / 90 * f.sot,
          sh: pl[R.sh] * eMin / 90 * f.sh, y: pl[R.y] * eMin / 90 * f.y };
        for (const mk of MKTS) {
          const l = mk.id === "sc" ? lam.g : mk.id === "ga" ? lam.g + lam.a : mk.id === "as" ? lam.a
            : mk.id === "sot" ? lam.sot : mk.id === "sh2" ? lam.sh : lam.y;
          const p = atLeast(l, mk.k);
          if (!(p > 0.04)) continue;
          out.push({ id: `pl:${mk.id}:${pid}`, mk: mk.id, pid, name, team, side, pos, pPlay, eMin, p,
            label: `${name} ${mk.l}`, pen: !!pl[10], starts: pl[11], season: pl[12], form: pl[13], doubt: ij ? ij[3] || "twijfelachtig" : null });
        }
      }
    }
    return { list: out, inj, have };
  }

  const api = { MKTS, POS, FORM, playerBets, teamFactors };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SSPlayers = api;
})(typeof window !== "undefined" ? window : globalThis);
