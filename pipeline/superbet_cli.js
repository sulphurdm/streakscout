// node superbet_cli.js <outdir> <YYYY-MM-DD> <state/superbet.json> <state/superbet_week.json> <now "YYYY-MM-DD HH:MM"> <state/betje.json> [finalize]
// Superbet of the week: made once at the first update of the week (Monday to Sunday) and never changed afterwards;
// weekly.py settles its legs. When it is lost and enough matches are left this week, a "Second chance" of about 10x
// is made once. Both are shown on the page (data.superbetWeek); state/superbet.json holds the bet that is still running.
// Betje van de dag: a combi of about 5x from today's matches with real bookmaker odds where the model sees value
// (markets.js betje); made once a day (a day without one is tried again at a later update that day), settled in weekly.py.
// BTTS (beide teams scoren ja/nee) is an ordinary market: it can be a leg of the superbet (markets.js) and of the betje.
// Every leg gets "why": one line on why the model likes it (streaks, average, model and bookmaker).
//
// Deep dive (the daily scheduled task): this script writes state/dossier.json with the matches to research (today's
// betje candidates and the weekly legs that are made today or played today/tomorrow), with the model's numbers for
// every market. Claude researches them on the web and writes <root>/research/*.json and state/choice.json (its own
// picks for today's betje, and for the weekly superbet when it was made today). With "finalize" this
// script merges the research into state/research.json, checks Claude's picks (constraints below) and uses them,
// otherwise it keeps the model's picks; the page shows the research per match (data.research).
const fs = require("fs");
const path = require("path");
const [out, today, stateFile, weekFile, nowArg, betjeFile, mode] = process.argv.slice(2);
const FINAL = mode === "finalize";
const data = JSON.parse(fs.readFileSync(path.join(out, "app_data.json")));
const model = JSON.parse(fs.readFileSync(path.join(out, "model.json")));
const hist = JSON.parse(fs.readFileSync(path.join(out, "history.json")));
const { create } = require("./engine.js");
const M = require("./markets.js");
const eng = create(data, model);
const now = nowArg || today + " 00:00";
const tomorrow = new Date(Date.parse(today + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);
const stateDir = path.dirname(betjeFile);
const readJ = (p, dflt) => { try { return JSON.parse(fs.readFileSync(p)); } catch (e) { return dflt; } };
// competitions whose update failed today stay out (their schedule and form were not checked)
const failed = readJ(path.join(out, "failed_leagues.json"), []);
const tix = new Map(data.teams.map((t, i) => [t, i]));
const BTTS = new Set(["btts", "bttsno"]);
const keyOf = (x) => `${x.when.slice(0, 10)}|${x.div}|${x.h}|${x.a}`;

function isoWeek(dstr) {
  const d = new Date(dstr + "T12:00:00Z"), day = (d.getUTCDay() + 6) % 7; // 0 = Monday
  const mon = new Date(d.getTime() - day * 864e5), sun = new Date(mon.getTime() + 6 * 864e5);
  const th = new Date(mon.getTime() + 3 * 864e5), y = th.getUTCFullYear();
  const jan4 = new Date(Date.UTC(y, 0, 4)), w1 = new Date(jan4.getTime() - ((jan4.getUTCDay() + 6) % 7) * 864e5);
  const wk = 1 + Math.round((mon - w1) / (7 * 864e5));
  return { week: `${y}-W${String(wk).padStart(2, "0")}`, mon: mon.toISOString().slice(0, 10), sun: sun.toISOString().slice(0, 10), n: wk };
}
const W0 = isoWeek(today);
let W = readJ(weekFile, null);
if (W && W.week !== W0.week) W = null;
const make = (target, maxLegs, exclude) => M.superbet(eng, data, hist,
  { from: today, to: W0.sun, after: now, target, maxLegs, minP: 0.62, skipDivs: failed, exclude });
const stamp = (b, name) => Object.assign(b, { name, made: now, status: "bezig", legs_hit: 0, skipped: failed,
  legs: b.legs.map((l) => Object.assign(l, { hit: null })) });
// explanation per leg, written when the bet is made (or later for legs that have not been played yet)
const explain = (b) => {
  for (const l of (b && b.legs) || []) {
    if (l.why || l.hit != null || l.when <= now) continue;
    try { const w = M.explainLeg(eng, data, hist, l, today); if (w) l.why = w; } catch (e) { /* no explanation */ }
  }
  if (b && b.btts) explain(b.btts);
};

// European nights: on a day with Champions League, Europa League or Conference League matches, combis of at least 5x
// from that day's matches of that competition only: two different ones of 5x+ (no match in both) and a "knaller" of 10x+.
// On a day with Europa League and Conference League matches there is also one "safe" combi (about 2-3x) from both.
// Only markets settled by the final score (the cups have no football-data statistics); odds are estimates.
// Stored per day in betje.json under the group's key: cl, el, ecl, eusafe (arrays of combis), with <key>_note.
const CL_IDS = new Set(["win@H", "win@A", "draw", "nolose@H", "nolose@A", "12", "o15", "o25", "u25", "o35", "u35",
  "btts", "bttsno", "sc1@H", "sc1@A", "sc2@H", "sc2@A", "cs@H", "cs@A"]);
const EU = [
  { key: "cl", divs: ["CL"], name: "Champions League-avond", short: "CL-avond", tag: "CL" },
  { key: "el", divs: ["EL"], name: "Europa League-avond", short: "EL-avond", tag: "EL" },
  { key: "ecl", divs: ["ECL"], name: "Conference League-avond", short: "ECL-avond", tag: "ECL" },
  { key: "eusafe", divs: ["EL", "ECL"], name: "Europese safe combi", short: "Safe combi EL + ECL", tag: "EL/ECL", safe: true },
];
const euToday = (divs) => data.fixtures.filter((f) => divs.includes(f.div) && f.when.slice(0, 10) === today && f.when > now);
const groupOn = (g) => (g.safe ? g.divs.every((d) => euToday([d]).length >= 1) && euToday(g.divs).length >= 2 : euToday(g.divs).length >= 2);
const clToday = () => euToday(["CL"]);
// Spread rule for a European night: the same bet, or one that comes down to the same thing, is never in every combi
// (if it fails, they all fail; on 20/10 all three CL combis lost on Porto). A match may be in several combis with a different bet.
// Per match: H = needs the home team to score or not to lose, A = the same for the away team, G+ = goals, G- = few goals.
const CORE = { "win@H": ["H"], "nolose@H": ["H"], "sc1@H": ["H"], "sc2@H": ["H"], "win@A": ["A"], "nolose@A": ["A"], "sc1@A": ["A"],
  "sc2@A": ["A"], btts: ["H", "A", "G+"], o15: ["G+"], o25: ["G+"], o35: ["G+"], u25: ["G-"], u35: ["G-"], bttsno: ["G-"],
  "cs@H": ["G-"], "cs@A": ["G-"] };
const CORE_TXT = { H: "de thuisploeg moet scoren of niet verliezen", A: "de uitploeg moet scoren of niet verliezen", "G+": "er moeten goals vallen", "G-": "er mogen weinig goals vallen" };
function sharedCore(bets, tag = "CL") {
  if (bets.length < 2) return null;
  const sets = bets.map((b) => new Set(b.legs.flatMap((l) => (CORE[l.id] || [l.id]).map((t) => (l.mkey || l.h + "|" + l.a) + "|" + t))));
  for (const t of sets[0]) if (sets.every((s) => s.has(t))) {
    const [h, a, k] = t.split("|");
    return `${h}-${a} zit in alle ${bets.length} ${tag}-betjes met een bet die op hetzelfde neerkomt (${CORE_TXT[k] || k}); als die misloopt, verliezen ze allemaal`;
  }
  return null;
}
function euNight(g) {
  if (!groupOn(g)) return null;
  const mk = (target, maxLegs, exclude, minP = 0.6) => M.superbet(eng, data, hist, { from: today, to: today, after: now, target, maxLegs, minP,
    ids: CL_IDS, onlyDivs: new Set(g.divs), exclude });
  const out = [];
  if (g.safe) {
    // the safe combi: the surest legs of both competitions, about 2-3x
    let s = mk(2.2, 4, undefined, 0.72);
    if (s.reached && !g.divs.every((d) => s.legs.some((l) => l.div === d))) {
      // at least one leg from each competition: the surest part of one to ±1.5x, the other for the rest
      const one = (d, target, maxLegs) => M.superbet(eng, data, hist, { from: today, to: today, after: now, target, maxLegs, minP: 0.72,
        ids: CL_IDS, onlyDivs: new Set([d]) });
      const a = one(g.divs[0], 1.45, 2), b = a.reached ? one(g.divs[1], 2.2 / a.odds, 2) : null;
      s = a.reached && b && b.reached ? { legs: [...a.legs, ...b.legs], odds: Math.round(a.odds * b.odds * 100) / 100, p: a.p * b.p,
        reached: true, allPriced: a.allPriced && b.allPriced } : { legs: [], reached: false };
    }
    if (s.reached && s.legs.length >= 2) { s.legs.sort((x, y) => (x.when < y.when ? -1 : 1)); out.push(stamp(s, g.short)); }
    for (const x of out) { x.chosen_by = "model"; x.target = 2.2; }
    return out;
  }
  const a = mk(5, 6);
  if (a.reached && a.legs.length >= 2) out.push(stamp(a, `${g.short} betje 1`));
  const used = new Set(out.flatMap((b) => b.legs.map((l) => l.h + "|" + l.a)));
  const b = mk(5, 6, used);
  if (b.reached && b.legs.length >= 2) out.push(stamp(b, `${g.short} betje 2`));
  // betje 1 and 2 share no match, so the knaller can use any match; with only one other combi it stays apart from it
  const c = mk(10, 8, out.length === 1 ? used : undefined);
  if (c.reached && c.legs.length >= 2 && !out.some((x) => x.odds === c.odds && x.legs.length === c.legs.length) && !sharedCore([...out, c], g.tag)) out.push(stamp(c, `${g.short} knaller`));
  for (const x of out) { x.chosen_by = "model"; x.target = x.target || 5; }
  return out;
}

let made = null;
if (!FINAL) {
  if (!W) {
    const sb = make(20, 14);
    if (sb.legs.length >= 2) {
      W = { week: W0.week, weekNo: W0.n, from: W0.mon, to: W0.sun, main: stamp(sb, "Superbet of the week"), second: null };
      made = "main";
    }
  } else if (W.main && W.main.status === "verloren" && !W.second) {
    // second chance: about 10x, only matches that have not started yet and are not already in the lost superbet
    const used = new Set(W.main.legs.map((l) => l.h + "|" + l.a));
    const sc = make(10, 10, used);
    if (sc.reached && sc.legs.length >= 2) { W.second = stamp(sc, "Second chance"); made = "second"; }
    else W.secondNote = `${today}: te weinig matches over deze week voor een second chance van ±10x`;
  }
}
if (W) { explain(W.main); explain(W.second); }

// Betje van de dag
let BJ = readJ(betjeFile, []);
if (!Array.isArray(BJ)) BJ = [];
// a day without a betje is tried again at a later run, unless Claude decided so after the deep dive
let bjToday = BJ.find((x) => x.day === today && (!x.none || x.chosen_by === "claude"));
let bjMade = false;
if (!bjToday && !FINAL) {
  const bj = M.betje(eng, data, hist, { day: today, after: now, skipDivs: failed });
  const prevDay = BJ.find((x) => x.day === today) || {};
  BJ = BJ.filter((x) => x.day !== today);
  if (bj.legs.length >= 2) {
    bjToday = Object.assign(bj, { name: "Betje van de dag", made: now, status: "bezig", legs_hit: 0, chosen_by: "model",
      legs: bj.legs.map((l) => Object.assign(l, { hit: null })) });
    bjMade = true;
  } else {
    // no betje with bookmaker odds (often there are none): the surest ±5x from today's matches with estimated odds,
    // markets settled by the final score first, so there is a betje every day (the deep dive can replace it)
    const fb = M.superbet(eng, data, hist, { from: today, to: today, after: now, target: 4.5, maxLegs: 4, minP: 0.6, skipDivs: failed, ids: CL_IDS });
    if (fb.reached && fb.legs.length >= 2) {
      bjToday = Object.assign(fb, { day: today, name: "Betje van de dag", made: now, status: "bezig", legs_hit: 0, chosen_by: "model", target: 5,
        estimated: true, note: bj.note, legs: fb.legs.map((l) => Object.assign(l, { hit: null })) });
      bjMade = true;
    } else bjToday = { day: today, none: true, made: now, note: bj.note, priced: bj.priced, candidates: bj.candidates };
  }
  for (const g of EU) if (prevDay[g.key]) { bjToday[g.key] = prevDay[g.key]; if (prevDay[g.key + "_note"]) bjToday[g.key + "_note"] = prevDay[g.key + "_note"]; }
  BJ.push(bjToday);
  BJ = BJ.slice(-60);
}
if (!bjToday) bjToday = BJ.find((x) => x.day === today) || null;
if (!FINAL && betjeFile) for (const g of EU) {
  if (!groupOn(g)) continue;
  if (!bjToday) { bjToday = { day: today, none: true, made: now, note: "nog geen betje" }; BJ.push(bjToday); }
  if (!bjToday[g.key] || !bjToday[g.key].length) { const n = euNight(g); if (n && n.length) bjToday[g.key] = n; }
}
const euCombis = (t) => EU.flatMap((g) => (t && t[g.key]) || []);
if (bjToday) { explain(bjToday); for (const c of euCombis(bjToday)) explain(c); }

// ---- dossier for the deep dive: the matches to research, with the model's numbers for every market
const RESEARCH = readJ(path.join(stateDir, "research.json"), {});
// research counts as fresh for 20 hours, unless it was "limited" (too few pages could be read): then it is done again
const fresh = (k) => RESEARCH[k] && RESEARCH[k].checked && !RESEARCH[k].limited
  && (Date.parse(now.replace(" ", "T")) - Date.parse(RESEARCH[k].checked.replace(" ", "T"))) < 20 * 3600e3;
const lname = (d) => (data.divNames && data.divNames[d]) || (data.cups && data.cups[d] && data.cups[d].name) || d;
const nm = (t) => M.DISPLAY[t] || t;
function teamStats(t, n) {
  const L = (eng.teamMatches.get(t) || []).slice(-n), acc = { kf: [], ka: [], cf: [], ca: [], sf: [], sa: [], gf: [], ga: [] };
  for (const i of L) {
    const s = eng.side(i, data.m.h[i] === t);
    for (const k of Object.keys(acc)) if (s[k] != null) acc[k].push(s[k]);
  }
  const avg = (a) => (a.length ? Math.round((a.reduce((x, y) => x + y, 0) / a.length) * 10) / 10 : null);
  return { matches: L.length, goals_for: avg(acc.gf), goals_against: avg(acc.ga), cards_for: avg(acc.kf), cards_against: avg(acc.ka),
    corners_for: avg(acc.cf), corners_against: avg(acc.ca), shots_for: avg(acc.sf), shots_against: avg(acc.sa), with_stats: acc.kf.length };
}
function dossierMatch(f, roles) {
  const H = tix.get(f.h), A = tix.get(f.a);
  if (H == null || A == null) return null;
  const bs = M.betsFor(eng, data, hist, H, A, f.div, today);
  if (!bs.length) return null;
  const top = new Set(bs.filter((b) => b.p >= 0.5).sort((x, y) => (y.p - y.base) - (x.p - x.base)).slice(0, 12).map((b) => b.id));
  for (const b of bs) if (b.market || BTTS.has(b.id) || b.g === "cards" || roles.some((r) => r.id === b.id)) top.add(b.id);
  const markets = bs.filter((b) => top.has(b.id)).map((b) => {
    const real = b.market && !b.market.derived;
    const odd = b.market ? b.market.best.o : 0.95 / b.p;
    const leg = { div: f.div, when: f.when, h: f.h, a: f.a, id: b.id, p: b.p, odd, bk: b.market ? b.market.best.bk : null, derived: !!(b.market && b.market.derived) };
    let why = null;
    try { why = M.explainLeg(eng, data, hist, leg, today); } catch (e) { why = null; }
    return { id: b.id, label: b.label, group: b.g, p: Math.round(b.p * 1000) / 1000, average: Math.round(b.base * 1000) / 1000,
      signal: ({ 3: "sterk", 2: "matig", 1: "zwak" })[b.rel.lvl], odd: Math.round(odd * 100) / 100,
      odd_source: b.market ? b.market.best.bk + (b.market.derived ? " (afgeleid uit 1X2)" : "") : "geschat",
      bookmaker_p: b.market ? Math.round(b.market.pMkt * 1000) / 1000 : null,
      value: real ? Math.round((b.p * odd - 1) * 100) / 100 : null, why };
  });
  const k = keyOf(f);
  return { key: k, research_due: !fresh(k), league: lname(f.div), div: f.div, when: f.when, home: nm(f.h), away: nm(f.a),
    roles: roles.map((r) => r.role), form_last5: { home: formStr(H), away: formStr(A) },
    team_stats_last10: { home: teamStats(H, 10), away: teamStats(A, 10) }, markets };
}
function formStr(t) {
  const m = data.m, s = [];
  for (const i of (eng.teamMatches.get(t) || []).slice(-5)) {
    const home = m.h[i] === t, gf = home ? m.fh[i] : m.fa[i], ga = home ? m.fa[i] : m.fh[i];
    if (gf == null || ga == null || gf < 0 || ga < 0) continue;
    s.push(gf > ga ? "W" : gf === ga ? "G" : "V");
  }
  return s.join("");
}
function writeDossier() {
  const byKey = new Map();
  const add = (f, role, id) => { const k = keyOf(f); if (!byKey.has(k)) byKey.set(k, { f, roles: [] }); byKey.get(k).roles.push({ role, id }); };
  const legsOf = (b, role, onlySoon) => {
    for (const l of (b && !b.none && b.legs) || []) {
      if (l.hit != null || l.when <= now) continue;
      if (onlySoon && l.when.slice(0, 10) > tomorrow && !(b.made || "").startsWith(today)) continue;
      add(l, `${role}: ${l.label}`, l.id);
    }
  };
  // today's matches: candidates for the betje and its BTTS-optie (at most 10, the model's own picks first)
  const own = new Set((bjToday && !bjToday.none ? bjToday.legs : []).map((l) => l.h + "|" + l.a));
  const cands = [];
  for (const f of data.fixtures) {
    if (f.when.slice(0, 10) !== today || f.when <= now || failed.includes(f.div)) continue;
    const H = tix.get(f.h), A = tix.get(f.a);
    if (H == null || A == null) continue;
    const bs = M.betsFor(eng, data, hist, H, A, f.div, today);
    if (!bs.length) continue;
    const lift = Math.max(...bs.filter((b) => b.p >= 0.55).map((b) => b.p - b.base), 0);
    cands.push({ f, score: (own.has(f.h + "|" + f.a) ? 10 : 0) + (bs.some((b) => b.market) ? 1 : 0) + lift });
  }
  cands.sort((x, y) => y.score - x.score).slice(0, 10).forEach((c) => add(c.f, "kandidaat betje van de dag", null));
  if (bjToday && !bjToday.none) legsOf(bjToday, "betje van de dag");
  for (const c of euCombis(bjToday)) legsOf(c, c.name);
  for (const g of EU) if (!g.safe && bjToday && bjToday[g.key]) for (const f of euToday(g.divs)) add(f, `kandidaat ${g.short}`, null);
  if (W) for (const [k, nmK] of [["main", "superbet of the week"], ["second", "second chance"]]) {
    if (!W[k]) continue;
    legsOf(W[k], nmK, true);
  }
  const matches = [];
  for (const { f, roles } of byKey.values()) {
    const d = dossierMatch(f, roles);
    if (!d) continue;
    // research order: 1 legs of today's betje and its BTTS-optie, 2 weekly legs played today or tomorrow,
    // 3 other candidates for today's betje, 4 weekly legs later this week (only when that bet was made today)
    const r = d.roles.join(" | "), soon = f.when.slice(0, 10) <= tomorrow;
    d.priority = /^betje van de dag|^(CL|EL|ECL)-avond|^Safe combi|^kandidaat (CL|EL|ECL)-avond/m.test(d.roles.join("\n")) ? 1
      : (soon && /superbet|second chance/.test(r)) ? 2 : /kandidaat/.test(r) ? 3 : 4;
    matches.push(d);
  }
  matches.sort((x, y) => x.priority - y.priority || (x.when < y.when ? -1 : 1));
  const weekMadeToday = !!(W && W.main && (W.main.made || "").startsWith(today));
  fs.writeFileSync(path.join(stateDir, "dossier.json"), JSON.stringify({ today, now, rules: {
    betje: "2 tot 4 bets uit de matches van vandaag (rol 'kandidaat betje van de dag'), één per match, elke markt mag (uitslag, dubbele kans, goals, BTTS ja/nee, corners, kaarten, schoten); samen 4 tot 7,5x (mik op ±5x); enkel bets die je na je onderzoek 'sterk' of 'ok' vindt. Er moet elke dag met matchen een betje zijn: ook als het onderzoek beperkt is, kies je het beste betje dat je kan maken (bij voorkeur markten die op de eindstand afgerekend worden)",
    cl: "Champions League-avond (enkel als er vandaag CL-matchen zijn): 1 tot 4 combi's uit de CL-matchen van vandaag (rol 'kandidaat CL-avond'), elk 2 tot 6 bets, één per match, samen minstens 5x (hoogstens 40x), enkel markten die op de eindstand afgerekend worden (uitslag, dubbele kans, goals, BTTS, scoren, nul houden), enkel bets die je 'sterk' of 'ok' vindt; de odds zijn geschat. Spreiden: dezelfde bet, of een bet die op hetzelfde neerkomt (bv. 'Porto wint of gelijk' en 'Porto scoort': allebei moet Porto het goed doen; BTTS telt voor beide ploegen en voor goals), staat nooit in alle combi's tegelijk; een match mag wel in meerdere combi's met een andere bet. Ook als het onderzoek beperkt is: maak de combi's toch, met de beste info die je hebt",
    el: "Europa League-avond (enkel als er vandaag EL-matchen zijn): dezelfde regels als de Champions League-avond, maar enkel met de EL-matchen van vandaag (rol 'kandidaat EL-avond')",
    ecl: "Conference League-avond (enkel als er vandaag ECL-matchen zijn): dezelfde regels als de Champions League-avond, maar enkel met de ECL-matchen van vandaag (rol 'kandidaat ECL-avond')",
    eusafe: "Europese safe combi (enkel op een dag met EL- én ECL-matchen): precies 1 combi van 2 tot 4 bets uit de EL- en ECL-matchen van vandaag samen, minstens één uit elke competitie, samen 1,8 tot 3,5x (mik op ±2,5x); enkel markten die op de eindstand afgerekend worden en de bets die je het zekerst vindt (bij voorkeur 'sterk', nooit 'riskant' of 'afgeraden')",
    week: weekMadeToday ? "de superbet van de week is vandaag gemaakt: je mag hem nog aanpassen (2 tot 14 bets uit alle matches van deze week in het dossier, elke markt mag, ook BTTS; samen 15 tot 30x); daarna verandert hij niet meer" : "de weekbets liggen vast: enkel onderzoeken en beoordelen",
    current: { betje: bjToday && !bjToday.none ? bjToday.legs.map((l) => ({ key: keyOf(l), id: l.id })) : [],
      ...Object.fromEntries(EU.map((g) => [g.key, ((bjToday && bjToday[g.key]) || []).map((c) => ({ name: c.name, odds: c.odds, legs: c.legs.map((l) => ({ key: keyOf(l), id: l.id })) }))])),
    },
    week_made_today: weekMadeToday }, matches }, null, 1));
  return matches;
}

// ---- finalize: merge the research and use Claude's picks when they meet the rules
const fin = { research_files: 0, research_bad: [], choice: {} };
function legFrom(key, id) {
  const [d, div, h, a] = key.split("|");
  const f = data.fixtures.find((x) => x.div === div && x.h === h && x.a === a && x.when.slice(0, 10) === d);
  if (!f) return [null, `match ${key} niet gevonden in het programma`];
  if (f.when <= now) return [null, `${h}-${a} is al begonnen`];
  if (failed.includes(div)) return [null, `${h}-${a}: competitie niet bijgewerkt vandaag`];
  const H = tix.get(h), A = tix.get(a);
  const b = H != null && A != null ? M.betsFor(eng, data, hist, H, A, div, today).find((x) => x.id === id) : null;
  if (!b) return [null, `markt ${id} niet beschikbaar voor ${h}-${a}`];
  const real = b.market && !b.market.derived;
  const odd = b.market ? b.market.best.o : 0.95 / b.p;
  const pm = b.market ? b.market.pMkt : null;
  return [{ div, when: f.when, h, a, id, label: b.label, p: Math.round(b.p * 1000) / 1000, base: Math.round(b.base * 1000) / 1000,
    sig: b.rel.lvl, odd: Math.round(odd * 100) / 100, bk: b.market ? b.market.best.bk : null, derived: !!(b.market && b.market.derived),
    mkey: b.mkey, pm: pm != null ? Math.round(pm * 1000) / 1000 : undefined, pb: Math.round((real ? (b.p + pm) / 2 : b.p) * 1000) / 1000,
    hit: null }, null];
}
function verdictOf(key, id) {
  const r = RESEARCH[key];
  const m = r && Array.isArray(r.markets) ? r.markets.find((x) => x.id === id) : null;
  return m ? m.verdict : null;
}
function applyChoice(name, ch, o) {
  // o: {min, max, lo, hi, ids, day (only today's matches), weekFrom/weekTo}
  if (!ch || !Array.isArray(ch.legs) || !ch.legs.length) return [null, "geen keuze"];
  if (ch.legs.length < o.min || ch.legs.length > o.max) return [null, `${ch.legs.length} bets (moet ${o.min} tot ${o.max} zijn)`];
  const legs = [], seen = new Set();
  for (const x of ch.legs) {
    if (o.ids && !o.ids.has(x.id)) return [null, `markt ${x.id} mag hier niet`];
    const [leg, err] = legFrom(x.key || "", x.id);
    if (!leg) return [null, err];
    if (o.day && leg.when.slice(0, 10) !== o.day) return [null, `${leg.h}-${leg.a} is niet vandaag`];
    if (o.to && leg.when.slice(0, 10) > o.to) return [null, `${leg.h}-${leg.a} valt buiten deze week`];
    if (seen.has(leg.mkey)) return [null, `twee bets in ${leg.h}-${leg.a}`];
    seen.add(leg.mkey);
    const v = verdictOf(keyOf(leg), x.id);
    if (v === "afgeraden" || v === "riskant") return [null, `${leg.label} in ${leg.h}-${leg.a} staat zelf op '${v}' in het onderzoek`];
    if (x.reason) leg.reason = String(x.reason).slice(0, 600);
    legs.push(leg);
  }
  legs.sort((x, y) => (x.when < y.when ? -1 : 1));
  const odds = legs.reduce((a, l) => a * l.odd, 1);
  if (odds < o.lo || odds > o.hi) return [null, `totale odd ${odds.toFixed(2)} ligt buiten ${o.lo}–${o.hi}x`];
  return [{ legs, odds: Math.round(odds * 100) / 100, p: legs.reduce((a, l) => a * l.pb, 1), pModel: legs.reduce((a, l) => a * l.p, 1),
    allPriced: legs.every((l) => l.bk && !l.derived), reason: ch.reason ? String(ch.reason).slice(0, 1500) : undefined, chosen_by: "claude" }, null];
}
if (FINAL) {
  // research files written by the deep dive: <root>/research/*.json (one per match)
  const rdir = path.join(path.dirname(stateDir), "research");
  let files = [];
  try { files = fs.readdirSync(rdir).filter((f) => f.endsWith(".json")); } catch (e) { files = []; }
  for (const fn of files) {
    const r = readJ(path.join(rdir, fn), null);
    if (!r || typeof r.key !== "string" || typeof r.summary !== "string" || !Array.isArray(r.sources)) { fin.research_bad.push(fn); continue; }
    r.sources = r.sources.filter((s) => s && /^https?:\/\//.test(s.url || "")).slice(0, 12);
    r.checked = now; // the time of this run (subagents do not always know the clock)
    r.limited = !!r.limited || r.sources.length < 3;
    RESEARCH[r.key] = r;
    fin.research_files++;
  }
  for (const k of Object.keys(RESEARCH)) if (k.slice(0, 10) < new Date(Date.parse(today) - 21 * 864e5).toISOString().slice(0, 10)) delete RESEARCH[k];
  fs.writeFileSync(path.join(stateDir, "research.json"), JSON.stringify(RESEARCH, null, 1));
  const choice = readJ(path.join(stateDir, "choice.json"), {});
  // Claude can also decide there is no good betje today: empty legs with a reason
  const noneOf = (ch) => ch && Array.isArray(ch.legs) && !ch.legs.length && ch.reason;
  if (bjToday && noneOf(choice.betje)) {
    const keepCl = Object.fromEntries(EU.flatMap((g) => [[g.key, bjToday[g.key]], [g.key + "_note", bjToday[g.key + "_note"]]]));
    BJ = BJ.filter((x) => x.day !== today);
    bjToday = Object.assign({ day: today, none: true, made: bjToday.made || now, decided: now, chosen_by: "claude", note: String(choice.betje.reason).slice(0, 600) }, keepCl);
    BJ.push(bjToday);
    fin.choice.betje = "geen betje vandaag (keuze van Claude)";
    delete choice.betje;
  }
  // European nights: {"cl" | "el" | "ecl" | "eusafe": {"bets": [{"name", "legs": [...], "reason"}, ...], "reason"}} (empty bets: none tonight)
  for (const g of EU) {
    const ch = choice[g.key];
    if (!bjToday || !ch || !Array.isArray(ch.bets)) continue;
    if (!ch.bets.length && ch.reason) {
      bjToday[g.key] = [];
      bjToday[g.key + "_note"] = String(ch.reason).slice(0, 600);
      fin.choice[g.key] = `geen ${g.short} (keuze van Claude)`;
      continue;
    }
    const got = [], errs = [];
    const opts = g.safe ? { min: 2, max: 4, lo: 1.8, hi: 3.5, day: today, ids: CL_IDS } : { min: 2, max: 6, lo: 5, hi: 40, day: today, ids: CL_IDS };
    ch.bets.slice(0, g.safe ? 1 : 4).forEach((cb, i) => {
      const [b, err] = applyChoice(g.key, cb, opts);
      const divs = b ? new Set(b.legs.map((l) => l.div)) : null;
      if (b && b.legs.every((l) => g.divs.includes(l.div)) && (!g.safe || g.divs.every((d) => divs.has(d) || !euToday([d]).length)))
        got.push(Object.assign({ name: String(cb.name || (g.safe ? g.short : `${g.short} betje ${i + 1}`)).slice(0, 60), target: g.safe ? 2.2 : 5, made: now, decided: now, status: "bezig", legs_hit: 0 }, b));
      else errs.push(`${cb.name || i + 1}: ${err || (g.safe ? "minstens één EL- en één ECL-match, niets anders" : `enkel ${g.tag}-matchen`)}`);
    });
    const shared = !g.safe && got.length && !errs.length ? sharedCore(got, g.tag) : null;
    if (shared) errs.push(shared);
    if (got.length && !errs.length) { bjToday[g.key] = got; bjToday[g.key + "_note"] = ch.reason ? String(ch.reason).slice(0, 800) : undefined; fin.choice[g.key] = `gebruikt (${got.length})`; }
    else fin.choice[g.key] = `niet gebruikt: ${errs.join("; ")}`;
  }
  // today's betje
  if (bjToday && choice.betje) {
    const [b, err] = applyChoice("betje", choice.betje, { min: 2, max: 4, lo: 4, hi: 7.5, day: today });
    if (b) {
      const keepCl = Object.fromEntries(EU.flatMap((g) => [[g.key, bjToday[g.key]], [g.key + "_note", bjToday[g.key + "_note"]]]));
      BJ = BJ.filter((x) => x.day !== today);
      bjToday = Object.assign({ day: today, target: 5, name: "Betje van de dag", made: bjToday.made || now, decided: now, status: "bezig", legs_hit: 0 }, b, keepCl);
      BJ.push(bjToday);
      fin.choice.betje = "gebruikt";
    } else fin.choice.betje = `niet gebruikt: ${err}`;
  }
  // the weekly superbet only while it is still the day it was made
  if (W && W.main && (W.main.made || "").startsWith(today) && choice.week) {
    const [b, err] = applyChoice("week", choice.week, { min: 2, max: 14, lo: 15, hi: 30, to: W0.sun });
    if (b) { W.main = Object.assign({ name: "Superbet of the week", target: 20, from: today, to: W0.sun, made: W.main.made, decided: now, status: "bezig", legs_hit: 0 }, b); fin.choice.week = "gebruikt"; }
    else fin.choice.week = `niet gebruikt: ${err}`;
  }
  if (W) { explain(W.main); explain(W.second); }
  if (bjToday) { explain(bjToday); for (const c of euCombis(bjToday)) explain(c); }
}
if (bjToday && !bjToday.none && !FINAL) bjToday.chosen_by = bjToday.chosen_by || "model";
const dossier = writeDossier();
fin.dossier = { matches: dossier.length, due: dossier.filter((m) => m.research_due).length };
fs.writeFileSync(path.join(out, "finalize.json"), JSON.stringify(fin));
BJ = BJ.slice(-60);
fs.writeFileSync(betjeFile, JSON.stringify(BJ, null, 1));

// research shown on the page: the matches of the bets on show
const shown = new Set();
const mark = (b) => { for (const l of (b && !b.none && b.legs) || []) shown.add(keyOf(l)); if (b && b.btts) mark(b.btts); };
mark(bjToday); for (const c of euCombis(bjToday)) mark(c); if (W) { mark(W.main); mark(W.second); }
data.research = {};
for (const k of shown) if (RESEARCH[k]) data.research[k] = RESEARCH[k];

const effOdds = (b) => b.legs.filter((l) => l.hit !== "void").reduce((a, l) => a * l.odd, 1);
const done = BJ.filter((x) => !x.none && (x.status === "gewonnen" || x.status === "verloren"));
const won = done.filter((x) => x.status === "gewonnen");
data.betje = {
  today: bjToday || null,
  recent: BJ.filter((x) => !x.none && x.day < today).slice(-7).reverse()
    .map((x) => ({ day: x.day, odds: x.odds, status: x.status, legs_hit: x.legs_hit, legs: x.legs.length })),
  record: { n: done.length, won: won.length, profit: Math.round(done.reduce((s, x) => s + (x.status === "gewonnen" ? effOdds(x) - 1 : -1), 0) * 100) / 100 },
};


// record per European group (stake 1 per combi)
data.betje.euRecord = {};
for (const g of EU) {
  const cdone = BJ.flatMap((x) => x[g.key] || []).filter((b) => b.status === "gewonnen" || b.status === "verloren");
  data.betje.euRecord[g.key] = { n: cdone.length, won: cdone.filter((b) => b.status === "gewonnen").length,
    profit: Math.round(cdone.reduce((s, b) => s + (b.status === "gewonnen" ? effOdds(b) - 1 : -1), 0) * 100) / 100 };
}
data.betje.clRecord = data.betje.euRecord.cl;
data.betje.euGroups = EU.map((g) => ({ key: g.key, name: g.name, tag: g.tag, safe: !!g.safe }));
data.superbetWeek = W;
delete data.superbet;
fs.writeFileSync(path.join(out, "app_data.json"), JSON.stringify(data));
if (weekFile && W) fs.writeFileSync(weekFile, JSON.stringify(W, null, 1));
// state/superbet.json: the bet that is still running (second chance once the weekly one is lost), for the summary
const active = W ? (W.second && W.main.status === "verloren" ? W.second : W.main) : null;
fs.writeFileSync(stateFile, JSON.stringify(active ? Object.assign({ week: W.week, from: W.from, to: W.to, made_now: made }, active) : { legs: [], odds: 0, p: 0 }, null, 1));

// predictions for the track record: league matches today and tomorrow, markets settled by the final score
const SETTLE = new Set(["win@H", "win@A", "draw", "nolose@H", "nolose@A", "12", "o15", "o25", "u25", "o35", "u35",
  "btts", "bttsno", "sc1@H", "sc1@A", "sc2@H", "sc2@A", "cs@H", "cs@A"]);
const preds = [];
for (const f of data.fixtures) {
  const d = f.when.slice(0, 10);
  if (d < today || d > tomorrow || (data.cups && data.cups[f.div])) continue;
  const H = tix.get(f.h), A = tix.get(f.a);
  if (H == null || A == null) continue;
  const bets = M.betsFor(eng, data, hist, H, A, f.div, today).filter((b) => SETTLE.has(b.id) && b.p >= 0.6)
    .sort((x, y) => (y.p - y.base) - (x.p - x.base)).slice(0, 6);
  for (const b of bets) {
    preds.push({ made: today, date: d, div: f.div, h: f.h, a: f.a, id: b.id, g: b.g, p: Math.round(b.p * 1000) / 1000,
      base: Math.round(b.base * 1000) / 1000, lvl: b.rel.lvl, odd: Math.round((b.market ? b.market.best.o : 0.95 / b.p) * 100) / 100,
      priced: b.market ? 1 : 0 });
  }
}
fs.writeFileSync(path.join(out, "predictions.json"), JSON.stringify(preds));

// player predictions for the track record (API-Football data): per match and market the 3 most likely
// players plus every player at 50% or more; settled later from the player stats of that match
const PLY = require("./players.js");
const ppreds = [];
if (data.af && data.af.t) {
  for (const f of data.fixtures) {
    const d = f.when.slice(0, 10);
    if (d < today || d > tomorrow) continue;
    const H = tix.get(f.h), A = tix.get(f.a);
    if (!data.af.t[f.h] && !data.af.t[f.a]) continue;
    let ps = null;
    if (H != null && A != null) {
      const bs = M.betsFor(eng, data, hist, H, A, f.div, today);
      const g = (id) => { const b = bs.find((x) => x.id === id); return b ? b.p : NaN; };
      ps = { H: g("sc1@H"), A: g("sc1@A") };
    }
    const list = PLY.playerBets(data, f.h, f.a, ps).list.filter((x) => x.pPlay >= 0.5);
    for (const mk of PLY.MKTS) {
      const L = list.filter((x) => x.mk === mk.id).sort((x, y) => y.p - x.p);
      for (const [j, x] of L.entries()) {
        if (j >= 3 && x.p < 0.5) continue;
        ppreds.push({ made: today, date: d, div: f.div, h: f.h, a: f.a, pid: x.pid, name: x.name, team: x.team, mk: mk.id,
          p: Math.round(x.p * 1000) / 1000, pplay: x.pPlay, emin: x.eMin });
      }
    }
  }
}
fs.writeFileSync(path.join(out, "player_predictions.json"), JSON.stringify(ppreds));
console.log(JSON.stringify({ week: W && W.week, made, final: FINAL, fin, betje: bjToday && (bjToday.none ? "geen" : { made: bjMade, odds: bjToday.odds, legs: bjToday.legs.length, by: bjToday.chosen_by }),
  main: W && W.main && { legs: W.main.legs.length, odds: W.main.odds, status: W.main.status },
  second: W && W.second && { legs: W.second.legs.length, odds: W.second.odds, status: W.second.status } }));
