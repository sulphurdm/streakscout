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
const sb = M.superbet(eng, data, hist, { from: today, to: tomorrow, target: 20, maxLegs: 14, minP: 0.62 });
sb.made = today;
data.superbet = sb;
fs.writeFileSync(path.join(out, "app_data.json"), JSON.stringify(data));
fs.writeFileSync(stateFile, JSON.stringify(sb, null, 1));
console.log(JSON.stringify({ legs: sb.legs.length, odds: sb.odds, p: Math.round(sb.p * 10000) / 10000, candidates: sb.candidates }));
