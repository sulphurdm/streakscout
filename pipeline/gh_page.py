"""Wrap the built StreakScout page into a standalone index.html for GitHub Pages.

Usage: python3 -I gh_page.py <dist/streakscout.html> <out/index.html>
"""
import sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
page = src.read_text(encoding="utf-8")
cut = page.index("</style>") + len("</style>")
head, body = page[:cut], page[cut:]
doc = f"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex">
<style>body{{margin:0}}[hidden]{{display:none!important}}</style>
{head}
</head>
<body>
{body}
</body>
</html>
"""
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(doc, encoding="utf-8")
print(dst, round(dst.stat().st_size / 1024), "KB")
