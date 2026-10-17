// node superbet_cli.js <outdir> <YYYY-MM-DD> <state/superbet.json> [<state/superbet_week.json> <now "YYYY-MM-DD HH:MM">]
// Superbet of the week: made once at the first update of the week (Monday to Sunday) and never changed afterwards;
// weekly.py settles its legs. When it is lost and enough matches are left this week, a "Second chance" of about 10x
// is made once. Both are shown on the page (data.superbetWeek); state/superbet.json holds the bet that is still running.
const fs = require("fs");
const path = require("path");
const [out, today, stateFile, weekFile, nowArg] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(path.join(out, "app_data.json")));
const model = JSON.parse(fs.readFileSync(path.join(out, "model.json")));
const hist = JSON.parse(fs.readFileSync(path.join(out, "history.json")));
const { create } = require("./engine.js");
const M = require("./markets.js");
const eng = create(data, model);
const now = nowArg || today + " 00:00";
const tomorrow = new Date(Date.parse(today + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);
// competitions whose update failed today stay out (their schedule and form were not checked)
let failed = [];
try { failed = JSON.parse(fs.readFileSync(path.join(out, "failed_leagues.json"))); } catch (e) { failed = []; }

function isoWeek(dstr) {
  const d = new Date(dstr + "T12:00:00Z"), day = (d.getUTCDay() + 6) % 7; // 0 = Monday
  const mon = new Date(d.getTime() - day * 864e5), sun = new Date(mon.getTime() + 6 * 864e5);
  const th = new Date(mon.getTime() + 3 * 864e5), y = th.getUTCFullYear();
  const jan4 = new Date(Date.UTC(y, 0, 4)), w1 = new Date(jan4.getTime() - ((jan4.getUTCDay() + 6) % 7) * 864e5);
  const wk = 1 + Math.round((mon - w1) / (7 * 864e5));
  return { week: `${y}-W${String(wk).padStart(2, "0")}`, mon: mon.toISOString().slice(0, 10), sun: sun.toISOString().slice(0, 10), n: wk };
}
const W0 = isoWeek(today);
let W = null;
try { if (weekFile) W = JSON.parse(fs.readFileSync(weekFile)); } catch (e) { W = null; }
if (W && W.week !== W0.week) W = null;
const make = (target, maxLegs, exclude) => M.superbet(eng, data, hist,
  { from: today, to: W0.sun, after: now, target, maxLegs, minP: 0.62, skipDivs: failed, exclude });
const stamp = (b, name) => Object.assign(b, { name, made: now, status: "bezig", legs_hit: 0, skipped: failed,
  legs: b.legs.map((l) => Object.assign(l, { hit: null })) });
let made = null;
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
const tix = new Map(data.teams.map((t, i) => [t, i]));
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
console.log(JSON.stringify({ week: W && W.week, made, main: W && W.main && { legs: W.main.legs.length, odds: W.main.odds, status: W.main.status },
  second: W && W.second && { legs: W.second.legs.length, odds: W.second.odds, status: W.second.status } }));
