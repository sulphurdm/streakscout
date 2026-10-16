/* StreakScout markets: bet definitions, reliability, bookmaker odds, and the daily superbet.
   Shared by the page (window.SSMarkets) and the daily pipeline (Node). */
(function (root) {
  const DISPLAY = { "St. Gilloise": "Union SG", "St Truiden": "STVV", "Oud-Heverlee Leuven": "OH Leuven",
    "RAAL La Louviere": "La Louvière", "Waregem": "Zulte Waregem", "Beveren": "SK Beveren", "Kortrijk": "KV Kortrijk",
    "Mechelen": "KV Mechelen", "Genk": "KRC Genk", "Gent": "KAA Gent" };

  const MK = [
    { id: "win@H", g: "res", l: (h) => `${h} wint` },
    { id: "draw", g: "res", l: () => "Gelijkspel" },
    { id: "win@A", g: "res", l: (h, a) => `${a} wint` },
    { id: "nolose@H", g: "res", l: (h) => `${h} wint of gelijk (1X)` },
    { id: "nolose@A", g: "res", l: (h, a) => `${a} wint of gelijk (X2)` },
    { id: "12", src: "draw", flip: true, g: "res", l: () => "Geen gelijkspel (12)" },
    { id: "htwin@H", g: "res", ht: true, l: (h) => `${h} leidt bij de rust` },
    { id: "htwin@A", g: "res", ht: true, l: (h, a) => `${a} leidt bij de rust` },
    { id: "o15", g: "goals", l: () => "Over 1.5 goals" },
    { id: "o25", g: "goals", l: () => "Over 2.5 goals" },
    { id: "u25", src: "o25", flip: true, g: "goals", l: () => "Under 2.5 goals" },
    { id: "o35", g: "goals", l: () => "Over 3.5 goals" },
    { id: "u35", src: "o35", flip: true, g: "goals", l: () => "Under 3.5 goals" },
    { id: "btts", g: "goals", l: () => "Beide teams scoren" },
    { id: "bttsno", src: "btts", flip: true, g: "goals", l: () => "Beide teams scoren: nee" },
    { id: "htg", g: "goals", ht: true, l: () => "Goal in de 1e helft" },
    { id: "sc1@H", g: "goals", l: (h) => `${h} scoort` },
    { id: "sc1@A", g: "goals", l: (h, a) => `${a} scoort` },
    { id: "sc2@H", g: "goals", l: (h) => `${h} scoort 2+` },
    { id: "sc2@A", g: "goals", l: (h, a) => `${a} scoort 2+` },
    { id: "cs@H", g: "goals", l: (h) => `${h} houdt de nul` },
    { id: "cs@A", g: "goals", l: (h, a) => `${a} houdt de nul` },
    { id: "c85", g: "corners", l: () => "Over 8.5 corners" },
    { id: "c95", g: "corners", l: () => "Over 9.5 corners" },
    { id: "u95", src: "c95", flip: true, g: "corners", l: () => "Under 9.5 corners" },
    { id: "c105", g: "corners", l: () => "Over 10.5 corners" },
    { id: "mcorn@H", g: "corners", l: (h) => `${h} meer corners` },
    { id: "mcorn@A", g: "corners", l: (h, a) => `${a} meer corners` },
    { id: "c45@H", g: "corners", l: (h) => `${h} 5+ corners` },
    { id: "c45@A", g: "corners", l: (h, a) => `${a} 5+ corners` },
    { id: "k35", g: "cards", l: () => "Over 3.5 kaarten" },
    { id: "u35k", src: "k35", flip: true, g: "cards", l: () => "Under 3.5 kaarten" },
    { id: "k45", g: "cards", l: () => "Over 4.5 kaarten" },
    { id: "sh10@H", g: "shots", l: (h) => `${h} 10+ schoten` },
    { id: "sh10@A", g: "shots", l: (h, a) => `${a} 10+ schoten` },
    { id: "sh13@H", g: "shots", l: (h) => `${h} 13+ schoten` },
    { id: "sh13@A", g: "shots", l: (h, a) => `${a} 13+ schoten` },
    { id: "sot3@H", g: "shots", l: (h) => `${h} 3+ schoten op doel` },
    { id: "sot3@A", g: "shots", l: (h, a) => `${a} 3+ schoten op doel` },
    { id: "sot5@H", g: "shots", l: (h) => `${h} 5+ schoten op doel` },
    { id: "sot5@A", g: "shots", l: (h, a) => `${a} 5+ schoten op doel` },
    { id: "msh@H", g: "shots", l: (h) => `${h} meer schoten` },
    { id: "msh@A", g: "shots", l: (h, a) => `${a} meer schoten` },
  ];
  // markets that need match statistics (corners, cards, shots, half-time): stale until football-data is current
  const NEEDS_STATS = (b) => b.g === "corners" || b.g === "cards" || b.g === "shots" || b.ht;

  // how well streaks in this market held up historically (lift of 8+/10 over the average)
  function reliability(HIST, ev, flip) {
    const r = HIST.reality[ev];
    if (!r) return { lvl: 1, lift: 0 };
    const rows = r.by_k.filter((x) => (flip ? x.k <= 2 : x.k >= 8));
    if (!rows.length) return { lvl: 1, lift: 0 };
    const real = rows.reduce((s, x) => s + (flip ? 1 - x.real : x.real), 0) / rows.length;
    const lift = real - (flip ? 1 - r.overall : r.overall);
    return { lvl: lift >= 0.15 ? 3 : lift >= 0.07 ? 2 : 1, lift };
  }

  // ---- bookmaker odds (football-data.co.uk + The Odds API)
  // double chance odds are derived from the 1X2 odds of the same bookmaker (1 / (1/o1 + 1/oX)); real double-chance odds are a bit lower
  const ODDS_IDX = { "win@H": ["1x2", [0]], "draw": ["1x2", [1]], "win@A": ["1x2", [2]], "o25": ["ou25", [0]], "u25": ["ou25", [1]],
    "nolose@H": ["1x2", [0, 1]], "nolose@A": ["1x2", [1, 2]], "12": ["1x2", [0, 2]] };
  const AGG = new Set(["Gemiddelde markt", "Hoogste markt"]);
  function fixtureOdds(DATA, hName, aName, today) {
    let found = null;
    for (const [k, v] of Object.entries(DATA.odds || {})) {
      const [, hh, aa, d] = k.split("|");
      if (hh === hName && aa === aName && d >= today && (!found || d < found.d)) found = { d, v };
    }
    return found;
  }
  function marketFor(id, fo) {
    if (!fo || !ODDS_IDX[id]) return null;
    const [mk, ix] = ODDS_IDX[id];
    const inv = (pr) => ix.reduce((acc, j) => acc + 1 / pr[j], 0);
    const list = Object.entries(fo.v[mk] || {}).filter(([, pr]) => pr && ix.every((j) => pr[j] > 1))
      .map(([bk, pr]) => ({ bk, o: Math.round(100 / inv(pr)) / 100, pr }));
    if (!list.length) return null;
    const real = list.filter((x) => !AGG.has(x.bk));
    const best = (real.length ? real : list).slice().sort((x, y) => y.o - x.o)[0];
    const ref = list.find((x) => /pinnacle/i.test(x.bk)) || list.find((x) => x.bk === "Gemiddelde markt") || list[0];
    const pMkt = inv(ref.pr) / ref.pr.reduce((acc, q) => acc + 1 / q, 0);
    return { list: list.slice().sort((x, y) => y.o - x.o), best, pMkt, refBk: ref.bk, t: fo.v.t, derived: ix.length > 1 };
  }

  /** analyse one fixture; for European cups each side uses its own competition. */
  function analyseFixture(eng, DATA, H, A, divCode) {
    const isCup = !!(DATA.cups && DATA.cups[divCode]);
    let div = DATA.divs.indexOf(divCode), opts = {};
    if (isCup) {
      const dH = eng.homeDiv(H), dA = eng.homeDiv(A);
      const eH = DATA.elo[DATA.teams[H]], eA = DATA.elo[DATA.teams[A]];
      if ((dH == null && dA == null) || (dH == null && !eH) || (dA == null && !eA)) return null;
      opts = { divH: dH ?? dA, divA: dA ?? dH };
      div = opts.divH;
    }
    return { r: eng.analyse(H, A, div, opts), divH: opts.divH ?? div, divA: opts.divA ?? div, cup: isCup };
  }

  /** compact bets for one fixture (no streak strips): id, label, p, base, reliability, odds */
  function betsFor(eng, DATA, HIST, H, A, divCode, today) {
    const an = analyseFixture(eng, DATA, H, A, divCode);
    if (!an) return [];
    const name = (i) => DISPLAY[DATA.teams[i]] || DATA.teams[i];
    const fo = fixtureOdds(DATA, DATA.teams[H], DATA.teams[A], today);
    const out = [];
    for (const mk of MK) {
      const src = an.r[mk.src || mk.id];
      if (!src || !Number.isFinite(src.p)) continue;
      const fl = !!mk.flip;
      out.push({ id: mk.id, g: mk.g, ht: !!mk.ht, label: mk.l(name(H), name(A)), p: fl ? 1 - src.p : src.p,
        base: fl ? 1 - src.base : src.base, rel: reliability(HIST, src.ev, fl), market: marketFor(mk.id, fo),
        mkey: `${DATA.teams[H]}|${DATA.teams[A]}` });
    }
    return out;
  }

  const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);

  /**
   * Superbet: one leg per match from matches in [from, to]. For every match all bets with a strong enough
   * streak signal are options (e.g. "Club wint" and "Club wint of gelijk (1X)"); the combination with the
   * highest total chance that still reaches the target odds is chosen (exact search over odds in steps of 1%).
   * A win gives more odds than 1X but carries more risk; the search weighs exactly that: odds gained (ln odd)
   * against chance lost (ln p). Leg odds are the best bookmaker odd when known, otherwise an estimate:
   * the fair odd minus a typical 5% margin.
   */
  const SIBLING = { "win@H": "nolose@H", "nolose@H": "win@H", "win@A": "nolose@A", "nolose@A": "win@A",
    "o15": "o25", "o25": "o15", "u25": "u35", "u35": "u25", "sc1@H": "sc2@H", "sc2@H": "sc1@H", "sc1@A": "sc2@A", "sc2@A": "sc1@A" };
  const legOdd = (b) => (b.market ? b.market.best.o : 0.95 / b.p);
  function superbet(eng, DATA, HIST, o) {
    const target = o.target || 20, maxLegs = o.maxLegs || 14, minP = o.minP || 0.62, minOdd = o.minOdd || 1.1;
    const tix = new Map(DATA.teams.map((t, i) => [t, i]));
    const fresh = (div) => {
      const le = DATA.leagueEnd && DATA.leagueEnd[div];
      return le && le.stats ? daysBetween(le.stats, DATA.updated || le.goals) <= 14 : false;
    };
    const matches = []; // [{f, opts:[...], all:[...]}]
    for (const f of DATA.fixtures) {
      const d = f.when.slice(0, 10);
      if (d < o.from || d > o.to) continue;
      const H = tix.get(f.h), A = tix.get(f.a);
      if (H == null || A == null) continue;
      const all = betsFor(eng, DATA, HIST, H, A, f.div, o.from);
      const opts = [];
      for (const b of all) {
        if (b.p < minP || b.rel.lvl < 2) continue;
        if (NEEDS_STATS(b) && !fresh(f.div)) continue; // stale statistics: skip
        const odd = legOdd(b);
        if (odd < minOdd) continue;
        opts.push(Object.assign({}, b, { f, odd, gain: Math.log(odd), cost: -Math.log(b.p) }));
      }
      if (opts.length) matches.push({ f, opts, all });
    }
    // exact search: maximise the total chance (sum of ln p) with total ln(odds) >= ln(target), at most maxLegs legs
    const STEP = 0.01, G = Math.ceil(Math.log(target) / STEP - 1e-9);
    const NEG = -1e18, M = matches.length;
    let dp = Array.from({ length: maxLegs + 1 }, () => new Float64Array(G + 1).fill(NEG));
    dp[0][0] = 0;
    const W = G + 1, stages = []; // per match: chosen option (k+1, 0 = skipped) and the odds bucket it came from
    for (let j = 0; j < M; j++) {
      const nd = dp.map((row) => Float64Array.from(row));
      const ch = new Int16Array((maxLegs + 1) * W), pg = new Int16Array((maxLegs + 1) * W);
      for (let n = 0; n < maxLegs; n++) {
        for (let g = 0; g <= G; g++) {
          const v = dp[n][g];
          if (v === NEG) continue;
          matches[j].opts.forEach((op, k) => {
            const g2 = Math.min(G, g + Math.floor(op.gain / STEP + 1e-9)), v2 = v - op.cost;
            if (v2 > nd[n + 1][g2] + 1e-12) { nd[n + 1][g2] = v2; ch[(n + 1) * W + g2] = k + 1; pg[(n + 1) * W + g2] = g; }
          });
        }
      }
      stages.push({ ch, pg });
      dp = nd;
    }
    let bestN = -1, bestV = NEG;
    for (let n = 1; n <= maxLegs; n++) if (dp[n][G] > bestV + 1e-12) { bestV = dp[n][G]; bestN = n; }
    let picked = [];
    if (bestN > 0) {
      let n = bestN, g = G;
      for (let j = M - 1; j >= 0 && n > 0; j--) {
        const k = stages[j].ch[n * W + g];
        if (!k) continue;
        picked.push({ op: matches[j].opts[k - 1], j });
        g = stages[j].pg[n * W + g];
        n -= 1;
      }
    }
    if (!picked || !picked.length) {
      // target not reachable: take each match's best option by chance per unit of odds
      picked = matches.map((mt, j) => ({ op: mt.opts.slice().sort((x, y) => x.cost / x.gain - y.cost / y.gain)[0], j }))
        .sort((x, y) => x.op.cost / x.op.gain - y.op.cost / y.op.gain).slice(0, maxLegs);
    }
    picked.sort((x, y) => (x.op.f.when < y.op.f.when ? -1 : x.op.f.when > y.op.f.when ? 1 : 0));
    const legs = picked.map(({ op, j }) => {
      const sib = SIBLING[op.id] && matches[j].all.find((b) => b.id === SIBLING[op.id]);
      return {
        div: op.f.div, when: op.f.when, h: op.f.h, a: op.f.a, id: op.id, label: op.label, p: Math.round(op.p * 1000) / 1000,
        base: Math.round(op.base * 1000) / 1000, sig: op.rel.lvl, odd: Math.round(op.odd * 100) / 100,
        bk: op.market ? op.market.best.bk : null, derived: !!(op.market && op.market.derived), mkey: op.mkey,
        alt: sib ? { id: sib.id, label: sib.label, p: Math.round(sib.p * 1000) / 1000, odd: Math.round(legOdd(sib) * 100) / 100, priced: !!sib.market } : null,
      };
    });
    const prod = legs.reduce((a, l) => a * l.odd, 1), p = picked.reduce((a, x) => a * x.op.p, 1);
    return { from: o.from, to: o.to, target, candidates: matches.length, odds: Math.round(prod * 100) / 100, p,
      allPriced: legs.length > 0 && legs.every((l) => l.bk), legs };
  }

  const api = { DISPLAY, MK, ODDS_IDX, reliability, fixtureOdds, marketFor, analyseFixture, betsFor, superbet };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SSMarkets = api;
})(typeof window !== "undefined" ? window : globalThis);
