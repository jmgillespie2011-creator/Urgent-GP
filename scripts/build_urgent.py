#!/usr/bin/env python3
"""Same-day care for clinically urgent appointments -> site/data/urgent.json.

Uses NHS England's 2026/27 contract measure: appointments in the national category
"General Consultation Acute" that took place on the day they were booked, as a share of all
"General Consultation Acute" appointments. Target: 90%.

Practice-level figures use June-August 2026 combined. Practices with fewer than 30 such
appointments a month, or failing the dashboard's data-quality checks, are left out of
distributions and models (their figures are still shown individually on the page).
Needs pandas and statsmodels. Run after build_data.py and build_funding.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data" / "urgent.json"
TARGET = 90.0
QUINTS = ["Q1", "Q2", "Q3", "Q4", "Q5"]
MIN_ACUTE_PER_MONTH = 30


def monthly() -> list[dict]:
    j = json.loads((ROOT / "site" / "data" / "practices.json").read_text())
    out = []
    for i, m in enumerate(j["months"]):
        t = {f: sum((p["k"][f][i] or 0) for p in j["practices"]) for f in ("acute", "acute_sd", "acute_gp", "acute_sd_gp", "total")}
        out.append({"month": m, "pct": round(100 * t["acute_sd"] / t["acute"], 1),
                    "pct_gp": round(100 * t["acute_sd_gp"] / t["acute_gp"], 1),
                    "pct_other": round(100 * (t["acute_sd"] - t["acute_sd_gp"]) / (t["acute"] - t["acute_gp"]), 1),
                    "acute": t["acute"], "share_of_all": round(100 * t["acute"] / t["total"], 1)})
    return out


def main() -> None:
    d = pd.read_csv(ROOT / "data" / "processed" / "practice_access.csv")
    funding = json.loads((ROOT / "site" / "data" / "funding.json").read_text())
    d = d[d["list_size"] >= 1000].copy()
    tot = d["total_3m"] / 3 / d["list_size"] * 1000
    d = d[(tot < 2000) & (tot > 100) & (d["unmapped_3m"] / d["total_3m"] < 0.4)].copy()
    d["acute_m"] = d["acute_3m"] / 3
    d = d[d["acute_m"] >= MIN_ACUTE_PER_MONTH].copy()
    d["pct"] = 100 * d["acute_sd_3m"] / d["acute_3m"]
    d["acute_rate"] = d["acute_m"] / d["list_size"] * 1000
    d["acute_share"] = 100 * d["acute_3m"] / d["total_3m"]
    d["meets"] = d["pct"] >= TARGET
    d["short_m"] = (TARGET / 100 * d["acute_3m"] - d["acute_sd_3m"]).clip(lower=0) / 3
    d["imd_q"] = pd.qcut(d["imd"], 5, labels=QUINTS)
    d["gp_q"] = pd.qcut(d["gp_qual_fte_10k"], 5, labels=QUINTS)

    def by(q: str) -> dict:
        g = d.groupby(q, observed=True)
        return {"median_pct": [round(float(v), 1) for v in g["pct"].median().reindex(QUINTS)],
                "meets_pct": [round(float(v), 1) for v in (100 * g["meets"].mean()).reindex(QUINTS)],
                "acute_rate": [round(float(v), 1) for v in g["acute_rate"].median().reindex(QUINTS)],
                "gp10k": [round(float(v), 2) for v in g["gp_qual_fte_10k"].median().reindex(QUINTS)],
                "iqr": [[round(float(g["pct"].quantile(0.25)[k]), 1), round(float(g["pct"].quantile(0.75)[k]), 1)] for k in QUINTS],
                "n": [int(v) for v in g.size().reindex(QUINTS)]}

    # Distribution of practices in 5-point bands.
    bins = list(range(0, 101, 5))
    hist = np.histogram(d["pct"].clip(0, 99.999), bins=bins)[0]

    # Adjusted association with GP staffing (ICB-clustered).
    cov = ["imd", "pct_65plus", "pct_0_14", "pct_male", "prev_smok", "nurse_fte_10k", "dpc_fte_10k", "admin_fte_10k", "acute_share"]
    m = d.dropna(subset=["pct", "gp_qual_fte_10k", "icb", *cov]).copy()
    for c in cov:
        m[c + "_z"] = (m[c] - m[c].mean()) / m[c].std()
    rhs = "gp_qual_fte_10k + " + " + ".join(c + "_z" for c in cov)
    groups = m["icb"].astype("category").cat.codes
    ols = smf.ols(f"pct ~ {rhs}", m).fit(cov_type="cluster", cov_kwds={"groups": groups})
    m["meets_i"] = m["meets"].astype(int)
    logit = smf.logit(f"meets_i ~ {rhs}", m).fit(disp=0, cov_type="cluster", cov_kwds={"groups": groups})
    b, lo, hi = float(ols.params["gp_qual_fte_10k"]), *[float(x) for x in ols.conf_int().loc["gp_qual_fte_10k"]]
    imd_pp_per_sd = float(ols.params["imd_z"])

    # Restoring core funding: each practice's salaried-GP equivalent -> GPs per 10,000 -> predicted change.
    gps = d["practice_code"].map(lambda c: funding["practices"].get(c, [None, 0])[1]).fillna(0)
    add10k = gps / d["list_size"] * 1e4
    m_add = add10k.reindex(m.index).fillna(0)
    pred_pct = (m["pct"] + b * m_add).clip(upper=100)
    p0 = logit.predict(m)
    m2 = m.copy()
    m2["gp_qual_fte_10k"] = m2["gp_qual_fte_10k"] + m_add
    p1 = logit.predict(m2)
    nat_now = 100 * d["acute_sd_3m"].sum() / d["acute_3m"].sum()
    nat_after = 100 * float((m["acute_sd_3m"] + m["acute_3m"] * (pred_pct - m["pct"]) / 100).sum() / m["acute_3m"].sum())
    nat_now_m = 100 * float(m["acute_sd_3m"].sum() / m["acute_3m"].sum())

    q1, q5 = d[d["imd_q"] == "Q1"], d[d["imd_q"] == "Q5"]
    out = {
        "target": TARGET, "min_acute_per_month": MIN_ACUTE_PER_MONTH,
        "monthly": monthly(),
        "practices_n": int(len(d)),
        "national_pct_3m": round(float(nat_now), 1),
        "meets_pct": round(100 * float(d["meets"].mean()), 1),
        "meets_80_pct": round(100 * float((d["pct"] >= 80).mean()), 1),
        "median_pct": round(float(d["pct"].median()), 1),
        "short_per_month": int(round(float(d["short_m"].sum()), -3)),
        "acute_share_p10_p90": [round(float(d["acute_share"].quantile(0.1)), 1), round(float(d["acute_share"].quantile(0.9)), 1)],
        "national_pct_attended": round(100 * float(d["acute_sd_att_3m"].sum() / d["acute_att_3m"].sum()), 1),
        "hist": {"bins": bins, "counts": [int(x) for x in hist]},
        "by_imd": by("imd_q"), "by_gp": by("gp_q"),
        "deprived": {"least_meets": round(100 * float(q1["meets"].mean()), 1), "most_meets": round(100 * float(q5["meets"].mean()), 1),
                     "least_median": round(float(q1["pct"].median()), 1), "most_median": round(float(q5["pct"].median()), 1),
                     "least_rate": round(float(q1["acute_rate"].median()), 1), "most_rate": round(float(q5["acute_rate"].median()), 1),
                     "most_miss_pct": round(100 * (1 - float(q5["meets"].mean())), 1)},
        "model": {"pp_per_gp_per_10k": round(b, 2), "ci": [round(lo, 2), round(hi, 2)], "n": int(ols.nobs), "r2": round(float(ols.rsquared), 3),
                  "imd_pp_per_sd": round(imd_pp_per_sd, 2),
                  "or_meets_per_gp_per_10k": round(float(np.exp(logit.params["gp_qual_fte_10k"])), 2),
                  "or_ci": [round(float(x), 2) for x in np.exp(logit.conf_int().loc["gp_qual_fte_10k"])],
                  "adjusted_for": cov},
        "rho": {f: round(float(d[["pct", f]].dropna().corr(method="spearman").iloc[0, 1]), 2)
                for f in ("imd", "gp_qual_fte_10k", "pts_per_gp", "acute_rate", "pct_65plus")},
        "restore": {"avg_add_gp_per_10k": round(float(m_add.mean()), 2),
                    "national_pct_now": round(nat_now_m, 1), "national_pct_after": round(nat_after, 1),
                    "meets_now_pred": round(100 * float(p0.mean()), 1), "meets_after_pred": round(100 * float(p1.mean()), 1),
                    "meets_now_obs": round(100 * float(m["meets"].mean()), 1)},
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("hist", "by_imd", "by_gp")}, indent=1))


if __name__ == "__main__":
    main()
