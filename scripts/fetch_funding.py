#!/usr/bin/env python3
"""Download the sources for the core-funding analysis (runs in GitHub Actions).

Writes to data/funding/:
  cpi_d7bt.csv, gdp_deflator_ybgb.csv   ONS price indices (annual and financial-year rows kept)
  investment/                           NHS Digital "Investment in General Practice 2008/09 to 2012/13" files
  payments/                             NHS Digital "NHS Payments to General Practice" practice-level file (latest)
  funding_manifest.json
"""
from __future__ import annotations

import io
import json
import re
import sys
import traceback
import urllib.parse
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_nhs_data as f  # noqa: E402  (shares the HTTP, browser and Internet Archive helpers)

OUT = f.ROOT / "data" / "funding"
manifest: dict = {}


def wayback_page(url: str) -> tuple[str, str]:
    """Raw HTML of the newest archived copy of a page."""
    cdx = json.loads(f.get(f"{f.WAYBACK}/cdx/search/cdx?url={urllib.parse.quote(url.replace('https://', ''))}"
                           f"&output=json&filter=statuscode:200&fl=timestamp,original&limit=-5", tries=3))
    if len(cdx) < 2:
        raise RuntimeError(f"not archived: {url}")
    ts, orig = cdx[-1]
    return ts, f.get(f"{f.WAYBACK}/web/{ts}id_/{orig}", tries=3).decode("utf-8", "replace")


def file_links(html: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href = re.sub(r"^(https?://web\.archive\.org)?/web/\d+[a-z_]*/", "", m.group(1).replace("&amp;", "&"))
        href = urllib.parse.urljoin("https://digital.nhs.uk/", href)
        text = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()
        if re.search(r"\.(csv|xlsx?|zip|pdf|ods)(\?|$)", href, re.I):
            out.append((href, text))
    return out


def save(name: str, blob: bytes, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    p.write_bytes(blob)
    print(f"  saved {p.relative_to(f.ROOT)} ({len(blob) / 1e6:.2f} MB)")
    return p


# --------------------------------------------------------------------------- ONS price indices
def ons_series(code: str, dataset: str, topic: str, fname: str) -> None:
    uri = f"/{topic}/timeseries/{code.lower()}/{dataset}"
    urls = [f"https://www.ons.gov.uk/generator?format=csv&uri={uri}",
            f"https://api.ons.gov.uk/timeseries/{code.lower()}/dataset/{dataset}/data"]
    for u in urls:
        for src in (u, f"{f.WAYBACK}/web/2026id_/{u}"):
            try:
                b = f.get(src, tries=2)
            except Exception as e:  # noqa: BLE001
                print(f"    {src}: {e}")
                continue
            text = b.decode("utf-8-sig", "replace")
            if text.lstrip().startswith("{"):
                j = json.loads(text)
                rows = [(r["date"], r["value"]) for k in ("years", "quarters", "months") for r in j.get(k, [])]
            else:
                rows = [tuple(x.strip('"') for x in line.split(",", 1)) for line in text.splitlines()
                        if re.match(r'^"?\d{4}', line)]
            if len(rows) > 20:
                (OUT / fname).write_text("period,value\n" + "\n".join(f"{a},{b}" for a, b in rows))
                print(f"  wrote {fname}: {len(rows)} rows from {src}")
                manifest[fname] = {"source": src, "series": code}
                return
    raise RuntimeError(f"ONS series {code} unavailable")


# --------------------------------------------------------------------------- Investment in General Practice
INVEST = ("https://digital.nhs.uk/data-and-information/publications/statistical/investment-in-general-practice/"
          "investment-in-general-practice-england-wales-northern-ireland-and-scotland-2008-09-to-2012-13")


def fetch_investment() -> None:
    ts, html = wayback_page(INVEST)
    files = file_links(html)
    print(f"  archived {ts}: {len(files)} files")
    got = []
    for href, text in files:
        print(f"    {text[:80]} -> {href}")
        try:
            blob = f.get_file(href) if not href.lower().endswith(".pdf") else f.get(f"{f.WAYBACK}/web/2026id_/{href}", tries=3)
        except Exception as e:  # noqa: BLE001
            print(f"      failed: {e}")
            continue
        got.append(str(save(href.rsplit("/", 1)[-1], blob, OUT / "investment").relative_to(f.ROOT)))
    if not got:
        raise RuntimeError("no investment files downloaded")
    manifest["investment"] = {"page": INVEST, "archived": ts, "files": got}


# --------------------------------------------------------------------------- NHS Payments to General Practice
def fetch_payments() -> None:
    page, files = f.archived_files("nhs-payments-to-general-practice", r"[a-z0-9-]*20\d\d-\d\d", r"\.(csv|zip)")
    files.sort(key=lambda x: not re.search(r"practice|csv", x[0] + x[1], re.I))
    for href, text in files:
        try:
            blob = f.get_file(href)
        except Exception as e:  # noqa: BLE001
            print(f"    {href}: {e}")
            continue
        if blob[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(blob))
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            print(f"    zip {href}: {names}")
            for n in names:
                save(n.rsplit("/", 1)[-1], z.read(n), OUT / "payments")
        else:
            save(href.rsplit("/", 1)[-1], blob, OUT / "payments")
        manifest["payments"] = {"page": page, "file": href}
        return
    raise RuntimeError("no payments file downloaded")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    steps = [("CPI", lambda: ons_series("D7BT", "mm23", "economy/inflationandpriceindices", "cpi_d7bt.csv")),
             ("GDP deflator", lambda: ons_series("YBGB", "ukea", "economy/grossdomesticproductgdp", "gdp_deflator_ybgb.csv")),
             ("population", lambda: ons_series("ENPOP", "pop", "peoplepopulationandcommunity/populationandmigration/populationestimates", "england_population_enpop.csv")),
             ("payments", fetch_payments)]
    if not (OUT / "investment").exists():
        steps.append(("investment", fetch_investment))
    for name, fn in steps:
        print(f"== {name}")
        try:
            fn()
            ok += 1
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            manifest[name + "_error"] = traceback.format_exc(limit=1).strip().splitlines()[-1]
    (OUT / "funding_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"{ok}/{len(steps)} funding sources fetched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
