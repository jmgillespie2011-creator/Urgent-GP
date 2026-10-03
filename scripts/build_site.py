#!/usr/bin/env python3
"""Wrap the page fragments into standalone documents.

site/page.html      -> site/index.html     (the advocacy page)
site/dashboard.html -> site/explorer.html  (the full analyst explorer)
"""
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
HEAD = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
    "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
    "body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>\n"
    "</head>\n<body>\n"
)
for src, dst in (("page.html", "index.html"), ("dashboard.html", "explorer.html")):
    body = (SITE / src).read_text(encoding="utf-8")
    (SITE / dst).write_text(HEAD + body + "\n</body>\n</html>\n", encoding="utf-8")
    print(f"Wrote site/{dst}")
