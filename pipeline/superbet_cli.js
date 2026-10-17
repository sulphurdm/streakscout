// node superbet_cli.js <outdir> <YYYY-MM-DD> <state/superbet.json>
// Picks the superbet for today + tomorrow and stores it in app_data.json (shown in the page) and in the state file.
const fs = require("fs");
const path = require("path");
const [out, today, stateFile] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(path.join(out, "app_data.json")));
const model = JSON.parse(fs.readFileSync(path.join(out, "model.json")));
const hist = JSON.parse(fs.readFileSync(path.join(out, "history.json")));
const { create } = require("./engine.js");
const M = require("./markets.js");
const eng = create(data, model);
const tomorrow = new Date(Date.parse(today + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);
// competitions whose update failed today stay out of the superbet (their schedule and form were not checked)
let failed = [];
try { failed = JSON.parse(fs.readFileSync(path.join(out, "failed_leagues.json"))); } catch (e) { failed = []; }
const sb = M.superbet(eng, data, hist, { from: today, to: tomorrow, target: 20, maxLegs: 14, minP: 0.62, skipDivs: failed });
sb.skipped = failed;
sb.made = today;
data.superbet = sb;
fs.writeFileSync(path.join(out, "app_data.json"), JSON.stringify(data));

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
fs.writeFileSync(stateFile, JSON.stringify(sb, null, 1));
console.log(JSON.stringify({ legs: sb.legs.length, odds: sb.odds, p: Math.round(sb.p * 10000) / 10000, candidates: sb.candidates }));
