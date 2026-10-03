#!/usr/bin/env python3
"""Compact per-practice file for the advocacy page -> site/data/lite.json.

One row per practice with the handful of figures the page shows: 3-month average access rates,
deprivation fifth, age, GP staffing and the core-funding gap. Practices failing the data-quality
checks keep their row (for "your practice" lookups) but are flagged, and are left out of charts.
Run after build_data.py, build_story.py and build_funding.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data" / "lite.json"


def main() -> None:
    d = pd.read_csv(ROOT / "data" / "processed" / "practice_access.csv")
    story = json.loads((ROOT / "site" / "data" / "story.json").read_text())
    funding = json.loads((ROOT / "site" / "data" / "funding.json").read_text())

    L = d["list_size"].where(d["list_size"] > 0)
    per1k = lambda c: d[c] / 3 / L * 1000  # noqa: E731  monthly, per 1,000 registered patients
    tot = per1k("total_3m")
    d["qc"] = ((d["list_size"] >= 1000) & (tot < 2000) & (tot > 100) & (d["unmapped_3m"] / d["total_3m"] < 0.4)).astype(int)
    d["sd"], d["urg"], d["sdgp"], d["dna"] = per1k("sd_3m"), per1k("urg_sd_3m"), per1k("sd_gp_3m"), per1k("dna_3m")
    fte = d["gp_fte"].where(d["gp_fte"] >= 0.5) if "gp_fte" in d else np.nan
    d["sdgp_per_gp"] = (d["sd_gp_3m"] / 3 / fte).where(lambda x: x < 2000)
    cuts = story["imd_cuts"]
    d["imd_q"] = np.select([d["imd"] <= c for c in cuts], [1, 2, 3, 4], default=5)
    d.loc[d["imd"].isna(), "imd_q"] = 0

    f = funding["practices"]
    d["gap"] = d["practice_code"].map(lambda c: f.get(c, [None])[0])
    d["gps"] = d["practice_code"].map(lambda c: f.get(c, [None, None])[1])
    d["extra"] = d["practice_code"].map(lambda c: f.get(c, [None, None, None])[2])
    d["gap_per_pt"] = d["gap"] / L

    title = lambda s: (str(s).title().replace("Pcn", "PCN") if isinstance(s, str) else "")  # noqa: E731
    cols = {
        "c": d["practice_code"], "n": d["practice_name"], "pcn": d["pcn"].map(title),
        "icb": d["icb"].fillna("").str.replace("Integrated Care Board", "ICB").str.replace("NHS ", "", regex=False),
        "reg": d["region"].fillna(""), "list": d["list_size"].round(0),
        "qc": d["qc"], "imd": d["imd"].round(1), "imd_q": d["imd_q"],
        "age65": d.get("pct_65plus", pd.Series(np.nan, index=d.index)).round(1),
        "gp10k": d.get("gp_qual_fte_10k", pd.Series(np.nan, index=d.index)).round(2),
        "ptsgp": d.get("pts_per_gp", pd.Series(np.nan, index=d.index)).round(0),
        "sd": d["sd"].round(1), "urg": d["urg"].round(1), "sdgp": d["sdgp"].round(1), "dna": d["dna"].round(1),
        "sdgp_gp": d["sdgp_per_gp"].round(0),
        "gap": d["gap"].round(-2), "gps": d["gps"].round(2), "extra": d["extra"].round(0), "gap_pt": d["gap_per_pt"].round(2),
    }
    out = {k: [None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v) for v in s.tolist()]
           for k, s in cols.items()}
    meta = {"rows": len(d), "fields": list(cols), "funding_year": funding["year"], "imd_cuts": cuts}
    OUT.write_text(json.dumps({"meta": meta, "cols": out}, separators=(",", ":"), ensure_ascii=False))
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB, {len(d)} practices, {int(d['qc'].sum())} pass QC)")


if __name__ == "__main__":
    main()
