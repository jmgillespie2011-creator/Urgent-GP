#!/usr/bin/env python3
"""Download the latest public practice-level files the dashboard uses as factors.

Runs in GitHub Actions (see .github/workflows/fetch-nhs-data.yml), because the
NHS sites are not reachable from every environment. Each source is fetched
independently; a failure in one is logged and the others still run.

Writes slim practice-level CSVs to data/sources/:
  workforce_practice.csv        GP workforce (practice level, latest month)
  age_sex_practice.csv          registered patients: % 0-14, % 65+, % 80+, % male
  qof_prevalence_practice.csv   QOF prevalence for every register (latest year)
  smoking_practice.csv          smoking prevalence, 15+ (Fingertips 91280 / QOF)
  fetch_manifest.json           where each file came from
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources"
BASE = "https://digital.nhs.uk"
PUB = BASE + "/data-and-information/publications/statistical/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) urgent-gp-dashboard data fetch"}
CODE = re.compile(r"^[A-Z]\d{5}$")
manifest: dict[str, dict] = {}


def get(url: str, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print(f"    GET {url} failed ({e}); attempt {i + 1}/{tries}")
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"could not fetch {url}")


def links(url: str) -> list[tuple[str, str]]:
    """(absolute href, link text) for every anchor on a page."""
    html = get(url).decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href = urllib.parse.urljoin(url, m.group(1).replace("&amp;", "&"))
        text = re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()
        out.append((href, text))
    return out


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def latest_child(series: str, pattern: str) -> str:
    """Newest publication page under a statistical series, judged by the date in its slug."""
    cands = set()
    for href, _ in links(PUB + series):
        m = re.search(PUB.replace(".", r"\.") + series + r"/(" + pattern + r")/?$", href)
        if m:
            cands.add(m.group(1))
    if not cands:
        raise RuntimeError(f"no publications found under {series}")

    def key(slug: str):
        y = re.findall(r"(20\d\d)", slug)
        mo = next((MONTHS[w] for w in re.findall(r"[a-z]+", slug) if w in MONTHS), 0)
        return (int(y[-1]) if y else 0, mo, slug)

    best = sorted(cands, key=key)[-1]
    print(f"  latest {series}: {best}")
    return PUB + series + "/" + best


def read_csv_from(blob: bytes, name_hint: re.Pattern | None = None) -> tuple[str, list[dict]]:
    """Return (filename, rows) from a CSV or a zip containing one."""
    if blob[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if name_hint:
            names = [n for n in names if name_hint.search(n)] or names
        names.sort(key=lambda n: -z.getinfo(n).file_size)
        name = names[0]
        text = z.read(name).decode("utf-8-sig", "replace")
    else:
        name, text = "download.csv", blob.decode("utf-8-sig", "replace")
    return name, list(csv.DictReader(io.StringIO(text)))


def write(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path.relative_to(ROOT)} ({len(rows)} rows)")


def num(v) -> float | None:
    try:
        x = float(str(v).replace(",", "").strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- workforce
def fetch_workforce() -> None:
    page = latest_child("general-and-personal-medical-services", r"\d{1,2}-[a-z]+-20\d\d")
    files = [(h, t) for h, t in links(page) if re.search(r"\.(zip|csv)(\?|$)", h, re.I)]
    for h, t in files:
        print(f"    file: {t[:90]} -> {h}")

    def pick(words):
        for h, t in files:
            s = (h + " " + t).lower()
            if all(w in s for w in words):
                return h
        return None

    url = (pick(["practice", "level", "detailed"]) or pick(["practice", "detailed"])
           or pick(["practice", "level"]) or pick(["practice"]))
    if not url:
        raise RuntimeError("no practice-level workforce file on " + page)
    name, rows = read_csv_from(get(url), re.compile(r"(?i)practice"))
    code_col = next(c for c in rows[0] if re.fullmatch(r"(?i)prac_code|practice_code|code", c))
    keep = [c for c in rows[0] if re.match(r"(?i)^total_", c) or c.upper() in {"PRAC_NAME", "PRAC_POSTCODE"}]
    out = []
    for r in rows:
        code = (r.get(code_col) or "").strip().upper()
        if CODE.match(code):
            out.append([code] + [r.get(c, "") for c in keep])
    write(OUT / "workforce_practice.csv", ["practice_code"] + keep, out)
    manifest["workforce"] = {"page": page, "file": url, "csv": name, "columns": len(keep)}


# --------------------------------------------------------------------------- age / sex
def fetch_age_sex() -> None:
    page = latest_child("patients-registered-at-a-gp-practice", r"[a-z]+-20\d\d")
    files = [(h, t) for h, t in links(page) if re.search(r"\.(zip|csv)(\?|$)", h, re.I)]
    url = next((h for h, t in files if re.search(r"quin", h + t, re.I) and re.search(r"prac", h + t, re.I)), None)
    if not url:
        for h, t in files:
            print(f"    file: {t[:90]} -> {h}")
        raise RuntimeError("no quinary-age practice file on " + page)
    name, rows = read_csv_from(get(url), re.compile(r"(?i)quin"))
    code_col = "ORG_CODE" if "ORG_CODE" in rows[0] else "CODE"
    acc = defaultdict(lambda: {"t": 0.0, "m": 0.0, "u15": 0.0, "o65": 0.0, "o80": 0.0, "o75": 0.0})
    for r in rows:
        code, sex, age = (r.get(code_col) or "").strip().upper(), r.get("SEX"), str(r.get("AGE_GROUP_5", ""))
        n = num(r.get("NUMBER_OF_PATIENTS"))
        if not CODE.match(code) or sex not in ("MALE", "FEMALE") or age.upper() == "ALL" or n is None:
            continue
        m = re.match(r"(\d+)", age)
        if not m:
            continue
        lo = int(m.group(1))
        a = acc[code]
        a["t"] += n
        a["m"] += n if sex == "MALE" else 0
        a["u15"] += n if lo < 15 else 0
        a["o65"] += n if lo >= 65 else 0
        a["o75"] += n if lo >= 75 else 0
        a["o80"] += n if lo >= 80 else 0
    out = [[c, int(a["t"]), *(round(100 * a[k] / a["t"], 3) for k in ("u15", "o65", "o75", "o80", "m"))]
           for c, a in sorted(acc.items()) if a["t"] > 0]
    write(OUT / "age_sex_practice.csv",
          ["practice_code", "registered_patients", "pct_0_14", "pct_65plus", "pct_75plus", "pct_80plus", "pct_male"], out)
    extract = rows[0].get("EXTRACT_DATE", "")
    manifest["age_sex"] = {"page": page, "file": url, "csv": name, "extract_date": extract}


# --------------------------------------------------------------------------- QOF
def fetch_qof() -> None:
    page = latest_child("quality-and-outcomes-framework-achievement-prevalence-and-exceptions-data", r"20\d\d-\d\d")
    files = [(h, t) for h, t in links(page) if re.search(r"\.(zip|csv)(\?|$)", h, re.I)]
    for h, t in files:
        print(f"    file: {t[:90]} -> {h}")
    blob = None
    for h, t in files:
        if re.search(r"raw|csv", h + " " + t, re.I) and h.lower().endswith(".zip"):
            b = get(h)
            if b[:2] == b"PK" and any(re.search(r"(?i)prevalence", n) for n in zipfile.ZipFile(io.BytesIO(b)).namelist()):
                blob, url = b, h
                break
    if blob is None:
        raise RuntimeError("no QOF raw-data zip with a prevalence CSV on " + page)
    z = zipfile.ZipFile(io.BytesIO(blob))
    print("    zip contents:", z.namelist())
    prev_name = next(n for n in z.namelist() if re.search(r"(?i)prevalence.*\.csv$", n))
    prev = list(csv.DictReader(io.StringIO(z.read(prev_name).decode("utf-8-sig", "replace"))))
    cols = {c.upper(): c for c in prev[0]}
    den_col = cols.get("PRACTICE_LIST_SIZE") or cols.get("LIST_SIZE")
    wide: dict[str, dict[str, float]] = defaultdict(dict)
    groups = set()
    for r in prev:
        code = (r[cols["PRACTICE_CODE"]] or "").strip().upper()
        reg, den = num(r[cols["REGISTER"]]), num(r[den_col])
        g = (r[cols["GROUP_CODE"]] or "").strip().lower()
        if CODE.match(code) and reg is not None and den:
            wide[code][g] = round(100 * reg / den, 4)
            groups.add(g)
    groups = sorted(groups)
    write(OUT / "qof_prevalence_practice.csv", ["practice_code"] + [f"prev_{g}" for g in groups],
          [[c] + [v.get(g, "") for g in groups] for c, v in sorted(wide.items())])
    year = page.rstrip("/").rsplit("/", 1)[-1]
    manifest["qof"] = {"page": page, "file": url, "csv": prev_name, "year": year, "registers": groups}

    # QOF smoking fallback: SMOK indicator denominators are the recorded current smokers.
    ach_name = next((n for n in z.namelist() if re.search(r"(?i)achievement.*\.csv$", n)), None)
    if ach_name:
        smok = defaultdict(dict)
        with z.open(ach_name) as fh:
            rdr = csv.DictReader(io.TextIOWrapper(fh, "utf-8-sig", errors="replace"))
            for r in rdr:
                ind = (r.get("INDICATOR_CODE") or "").upper()
                if ind.startswith("SMOK"):
                    code = (r.get("PRACTICE_CODE") or "").upper()
                    smok[code][(ind, (r.get("MEASURE") or "").upper())] = num(r.get("VALUE"))
        if smok:
            keys = sorted({k for v in smok.values() for k in v})
            print("    SMOK measures:", keys[:20])
            write(OUT / "qof_smok_practice.csv", ["practice_code"] + [f"{i}_{m}".lower() for i, m in keys],
                  [[c] + [v.get(k, "") for k in keys] for c, v in sorted(smok.items())])
            manifest["qof_smok"] = {"csv": ach_name, "measures": [f"{i}_{m}" for i, m in keys]}


# --------------------------------------------------------------------------- smoking (Fingertips)
def fetch_smoking() -> None:
    last = None
    for parent in (15, 167, 66, 221):
        url = ("https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
               f"?indicator_ids=91280&child_area_type_id=7&parent_area_type_id={parent}")
        try:
            _, rows = read_csv_from(get(url, tries=2))
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        rows = [r for r in rows if r.get("Area Type", "").startswith("GP") and CODE.match((r.get("Area Code") or "").upper())]
        if not rows:
            continue
        periods = sorted({r["Time period"] for r in rows})
        latest = periods[-1]
        out = [[r["Area Code"].upper(), r["Value"], r.get("Count", ""), r.get("Denominator", "")]
               for r in rows if r["Time period"] == latest and num(r.get("Value")) is not None]
        write(OUT / "smoking_practice.csv", ["practice_code", "smoking_prev_15plus", "smokers", "list_15plus"], out)
        manifest["smoking"] = {"source": url, "indicator": rows[0].get("Indicator Name"), "period": latest}
        return
    raise RuntimeError(f"Fingertips smoking prevalence unavailable ({last})")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, fn in [("workforce", fetch_workforce), ("age/sex", fetch_age_sex),
                     ("QOF", fetch_qof), ("smoking", fetch_smoking)]:
        print(f"== {name}")
        try:
            fn()
            ok += 1
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            manifest[name + "_error"] = traceback.format_exc(limit=1).strip().splitlines()[-1]
    manifest["fetched_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (OUT / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"{ok}/4 sources fetched")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
