"""Inline data, model, history and engine into one self-contained page.

Usage: python3 -I build.py <outdir with app_data.json, model.json, history.json> <target.html>
"""
import json
import sys
from pathlib import Path

here = Path(__file__).parent
out, target = Path(sys.argv[1]), Path(sys.argv[2])


def js(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


model = json.load(open(out / "model.json"))
hist = json.load(open(out / "history.json"))
page = (here / "app.html").read_text()
page = page.replace("/*__DATA__*/null", js(json.load(open(out / "app_data.json"))))
page = page.replace("/*__MODEL__*/null", js(model))
page = page.replace("/*__HISTORY__*/null", js(hist))
page = page.replace("/*__ENGINE__*/", "\n".join((here / f).read_text() for f in ("engine.js", "markets.js", "players.js")))
assert "/*__" not in page
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(page)
print(target, round(target.stat().st_size / 1024), "KB")
