/* StreakScout engine — mirrors engine.py feature logic exactly. Works in browser (window.SSEngine) and Node. */
(function (root) {
  const MATCH_EVENTS = ["o15", "o25", "o35", "btts", "draw", "htg", "c85", "c95", "c105", "k35", "k45"];
  const TEAM_EVENTS = ["win", "nolose", "sc1", "sc2", "cs", "htwin", "mcorn", "c45", "sh10", "sh13", "sot3", "sot5", "msh"];

  function create(data, modelFile) {
    const P = modelFile.params, models = modelFile.models;
    const m = data.m, N = m.d.length;
    const nn = (x) => (x == null || x < 0 ? null : x);
    const SH = m.sh || [], SA = m.sa || [], TH = m.th || [], TA = m.ta || [];

    // per-team match lists (indices in chronological order), per-div lists
    const teamMatches = new Map(), divMatches = new Map();
    for (let i = 0; i < N; i++) {
      for (const t of [m.h[i], m.a[i]]) {
        if (!teamMatches.has(t)) teamMatches.set(t, []);
        teamMatches.get(t).push(i);
      }
      if (!divMatches.has(m.v[i])) divMatches.set(m.v[i], []);
      divMatches.get(m.v[i]).push(i);
    }

    // raw numbers from a side's perspective
    function side(i, home) {
      return home
        ? { gf: nn(m.fh[i]), ga: nn(m.fa[i]), hgf: nn(m.hh[i]), hga: nn(m.ha[i]), cf: nn(m.ch[i]), ca: nn(m.ca[i]), kf: nn(m.kh[i]), ka: nn(m.ka[i]),
            sf: nn(SH[i]), sa: nn(SA[i]), tf: nn(TH[i]), ta: nn(TA[i]) }
        : { gf: nn(m.fa[i]), ga: nn(m.fh[i]), hgf: nn(m.ha[i]), hga: nn(m.hh[i]), cf: nn(m.ca[i]), ca: nn(m.ch[i]), kf: nn(m.ka[i]), ka: nn(m.kh[i]),
            sf: nn(SA[i]), sa: nn(SH[i]), tf: nn(TA[i]), ta: nn(TH[i]) };
    }
    const b = (c) => (c ? 1 : 0);
    const has = (...xs) => xs.every((x) => x !== null);
    const EV = {
      o15: (s) => (has(s.gf, s.ga) ? b(s.gf + s.ga >= 2) : null),
      o25: (s) => (has(s.gf, s.ga) ? b(s.gf + s.ga >= 3) : null),
      o35: (s) => (has(s.gf, s.ga) ? b(s.gf + s.ga >= 4) : null),
      btts: (s) => (has(s.gf, s.ga) ? b(s.gf > 0 && s.ga > 0) : null),
      draw: (s) => (has(s.gf, s.ga) ? b(s.gf === s.ga) : null),
      htg: (s) => (has(s.hgf, s.hga) ? b(s.hgf + s.hga >= 1) : null),
      c85: (s) => (has(s.cf, s.ca) ? b(s.cf + s.ca >= 9) : null),
      c95: (s) => (has(s.cf, s.ca) ? b(s.cf + s.ca >= 10) : null),
      c105: (s) => (has(s.cf, s.ca) ? b(s.cf + s.ca >= 11) : null),
      k35: (s) => (has(s.kf, s.ka) ? b(s.kf + s.ka >= 4) : null),
      k45: (s) => (has(s.kf, s.ka) ? b(s.kf + s.ka >= 5) : null),
      win: (s) => (has(s.gf, s.ga) ? b(s.gf > s.ga) : null),
      nolose: (s) => (has(s.gf, s.ga) ? b(s.gf >= s.ga) : null),
      sc1: (s) => (has(s.gf) ? b(s.gf >= 1) : null),
      sc2: (s) => (has(s.gf) ? b(s.gf >= 2) : null),
      cs: (s) => (has(s.ga) ? b(s.ga === 0) : null),
      htwin: (s) => (has(s.hgf, s.hga) ? b(s.hgf > s.hga) : null),
      mcorn: (s) => (has(s.cf, s.ca) ? b(s.cf > s.ca) : null),
      c45: (s) => (has(s.cf) ? b(s.cf >= 5) : null),
      sh10: (s) => (has(s.sf) ? b(s.sf >= 10) : null),
      sh13: (s) => (has(s.sf) ? b(s.sf >= 13) : null),
      sot3: (s) => (has(s.tf) ? b(s.tf >= 3) : null),
      sot5: (s) => (has(s.tf) ? b(s.tf >= 5) : null),
      msh: (s) => (has(s.sf, s.sa) ? b(s.sf > s.sa) : null),
    };
    // event value for team t in match i; against=true → event achieved by t's opponent
    function evFor(ev, i, t, against) {
      const home = m.h[i] === t;
      return EV[ev](side(i, against ? !home : home));
    }

    function prior(t, before) {
      const L = teamMatches.get(t) || [];
      let lo = 0, hi = L.length; // first index with L[idx] >= before
      while (lo < hi) { const mid = (lo + hi) >> 1; if (L[mid] < before) lo = mid + 1; else hi = mid; }
      return L.slice(0, lo);
    }
    // last w rows (rows w/o data still count as rows) → {k, n, list}
    function rollRows(rows, ev, t, against, w) {
      const sel = rows.slice(Math.max(0, rows.length - w));
      let k = 0, n = 0; const list = [];
      for (const i of sel) { const v = evFor(ev, i, t, against); list.push({ i, v }); if (v !== null) { n++; k += v; } }
      return { k, n, list };
    }
    function streakOf(rows, ev, t, against) {
      let s = 0, val = null;
      for (let j = rows.length - 1; j >= 0; j--) {
        const v = evFor(ev, rows[j], t, against);
        if (v === null) continue;
        if (val === null) { val = v; s = 1; } else if (v === val) s++; else break;
      }
      return val === null ? 0 : val === 1 ? s : -s;
    }
    function baseRate(div, ev, before, homeSide /* null=match-level */) {
      const L = divMatches.get(div) || [];
      let lo = 0, hi = L.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (L[mid] < before) lo = mid + 1; else hi = mid; }
      const sel = L.slice(Math.max(0, lo - P.W_BASE), lo);
      let k = 0, n = 0;
      for (const i of sel) {
        const v = EV[ev](side(i, homeSide === null ? true : homeSide));
        if (v !== null) { n++; k += v; }
      }
      if (n < 50) return { base: NaN, n };
      return { base: Math.min(0.98, Math.max(0.02, k / n)), n };
    }
    const logit = (p) => { p = Math.min(1 - 1e-4, Math.max(1e-4, p)); return Math.log(p / (1 - p)); };
    const sf = (k, n, base) => logit((k + P.M * base) / (n + P.M)) - logit(base);
    const sst = (s) => Math.sign(s) * Math.log1p(Math.min(Math.abs(s), P.STREAK_CAP));
    const sigmoid = (z) => 1 / (1 + Math.exp(-z));
    const apply = (ev, x) => { const mm = models[ev]; let z = mm.intercept; for (let j = 0; j < x.length; j++) z += mm.coef[j] * x[j]; return sigmoid(z); };

    /** Analyse a fixture. before = index of the first match NOT to use (N for "now"). */
    // base rate when the two sides come from different competitions (European cups): average of both leagues
    function baseFor(divH, divA, ev, before, homeSide) {
      if (homeSide !== null) return baseRate(homeSide ? divH : divA, ev, before, homeSide);
      if (divH === divA) return baseRate(divH, ev, before, null);
      const a = baseRate(divH, ev, before, null), b = baseRate(divA, ev, before, null);
      if (!Number.isFinite(a.base) || !Number.isFinite(b.base)) return { base: NaN, n: Math.min(a.n, b.n) };
      return { base: (a.base + b.base) / 2, n: Math.min(a.n, b.n) };
    }

    /** last competition a team played in (index into data.divs), or null */
    function homeDiv(t) {
      const L = teamMatches.get(t);
      return L && L.length ? m.v[L[L.length - 1]] : null;
    }

    function analyse(H, A, div, opts) {
      opts = opts || {};
      const divH = opts.divH ?? div, divA = opts.divA ?? div;
      const before = opts.before ?? N;
      const eloH = opts.eloH ?? data.elo[data.teams[H]] ?? null;
      const eloA = opts.eloA ?? data.elo[data.teams[A]] ?? null;
      const elod = eloH !== null && eloA !== null && !Number.isNaN(eloH) && !Number.isNaN(eloA) ? (eloH - eloA) / 100 : 0;
      const rH = prior(H, before), rA = prior(A, before);
      const rHhome = rH.filter((i) => m.h[i] === H), rAaway = rA.filter((i) => m.a[i] === A);
      const rAhome = rA.filter((i) => m.h[i] === A), rHaway = rH.filter((i) => m.a[i] === H);
      const h2hH = rH.filter((i) => m.h[i] === A || m.a[i] === A);
      const out = {};

      for (const ev of MATCH_EVENTS) {
        const { base, n: bn } = baseFor(divH, divA, ev, before, null);
        const f = {
          H: rollRows(rH, ev, H, false, P.W_ALL), Hv: rollRows(rHhome, ev, H, false, P.W_VEN),
          A: rollRows(rA, ev, A, false, P.W_ALL), Av: rollRows(rAaway, ev, A, false, P.W_VEN),
          h2h: rollRows(h2hH, ev, H, false, P.W_H2H),
          Hst: streakOf(rH, ev, H, false), Ast: streakOf(rA, ev, A, false),
        };
        let p = NaN;
        if (Number.isFinite(base)) {
          const x = [sf(f.H.k, f.H.n, base), sf(f.Hv.k, f.Hv.n, base), sf(f.A.k, f.A.n, base), sf(f.Av.k, f.Av.n, base),
            sf(f.h2h.k, f.h2h.n, base), sst(f.Hst), sst(f.Ast), logit(base), elod, Math.abs(elod)];
          p = apply(ev, x);
        }
        out[ev] = { kind: "match", ev, base, baseN: bn, p, f };
      }
      for (const ev of TEAM_EVENTS) {
        for (const [T, O, home, rT, rTv, rO, rOv] of [[H, A, true, rH, rHhome, rA, rAaway], [A, H, false, rA, rAaway, rH, rHhome]]) {
          const { base, n: bn } = baseFor(divH, divA, ev, before, home);
          const h2hT = rT.filter((i) => m.h[i] === O || m.a[i] === O);
          const f = {
            T: rollRows(rT, ev, T, false, P.W_ALL), Tv: rollRows(rTv, ev, T, false, P.W_VEN),
            O: rollRows(rO, ev, O, true, P.W_ALL), Ov: rollRows(rOv, ev, O, true, P.W_VEN),
            h2h: rollRows(h2hT, ev, T, false, P.W_H2H),
            Tst: streakOf(rT, ev, T, false), Ost: streakOf(rO, ev, O, true),
          };
          let p = NaN;
          if (Number.isFinite(base)) {
            const ed = home ? elod : -elod;
            const x = [sf(f.T.k, f.T.n, base), sf(f.Tv.k, f.Tv.n, base), sf(f.O.k, f.O.n, base), sf(f.Ov.k, f.Ov.n, base),
              sf(f.h2h.k, f.h2h.n, base), sst(f.Tst), sst(f.Ost), logit(base), ed, home ? 1 : 0];
            p = apply(ev, x);
          }
          out[ev + (home ? "@H" : "@A")] = { kind: "team", ev, home, team: T, opp: O, base, baseN: bn, p, f };
        }
      }
      return out;
    }

    return { analyse, homeDiv, N, teamMatches, side, EV, evFor, MATCH_EVENTS, TEAM_EVENTS };
  }

  const api = { create, MATCH_EVENTS, TEAM_EVENTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SSEngine = api;
})(typeof window !== "undefined" ? window : globalThis);
