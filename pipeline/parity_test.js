// node parity_test.js <outdir> — compares JS probabilities with Python's on the shipped subset
const fs = require("fs");
const path = require("path");
const out = process.argv[2];
const data = JSON.parse(fs.readFileSync(path.join(out, "app_data.json")));
const model = JSON.parse(fs.readFileSync(path.join(out, "model.json")));
const par = JSON.parse(fs.readFileSync(path.join(out, "parity.json")));
const { create } = require("./engine.js");
const eng = create(data, model);
const tix = Object.fromEntries(data.teams.map((t, i) => [t, i]));
let n = 0, maxd = 0, worst = null, missing = 0;
for (const [key, evs] of Object.entries(par.p)) {
  const [mid, home] = key.split("|").map(Number);
  const meta = par.meta[mid];
  const r = eng.analyse(tix[meta.h], tix[meta.a], data.divs.indexOf(meta.div),
    { before: mid, eloH: meta.eh ?? NaN, eloA: meta.ea ?? NaN });
  for (const [ev, pPy] of Object.entries(evs)) {
    const k = eng.MATCH_EVENTS.includes(ev) ? ev : ev + (home ? "@H" : "@A");
    const pJs = r[k].p;
    if (pPy === null || !Number.isFinite(pJs)) { if ((pPy === null) !== !Number.isFinite(pJs)) missing++; continue; }
    const d = Math.abs(pPy - pJs);
    n++;
    if (d > maxd) { maxd = d; worst = { key, ev, pPy, pJs, meta }; }
  }
}
console.log(JSON.stringify({ compared: n, maxAbsDiff: maxd, nanMismatch: missing, worst }));
process.exit(maxd < 1e-5 && missing === 0 ? 0 : 1);
