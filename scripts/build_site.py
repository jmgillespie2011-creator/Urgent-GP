#!/usr/bin/env python3
"""Wrap site/dashboard.html (a page fragment) into a standalone site/index.html."""
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
body = (SITE / "dashboard.html").read_text(encoding="utf-8")
html = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
    "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
    "body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>\n"
    "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
)
(SITE / "index.html").write_text(html, encoding="utf-8")
print("Wrote site/index.html")
