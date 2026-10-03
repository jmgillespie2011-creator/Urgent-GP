#!/usr/bin/env python3
"""Headline figures for the advocacy section of the page -> site/data/story.json.

Reads data/processed/practice_access.csv (written by build_data.py) and applies the same
data-quality exclusions as the dashboard. Every number quoted on the page comes from here.
Needs pandas and statsmodels.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "processed" / "practice_access.csv"
OUT = ROOT / "site" / "data" / "story.json"
QUINTS = ["Q1", "Q2", "Q3", "Q4", "Q5"]
LTC = ["prev_hyp", "prev_dm", "prev_ast", "prev_copd", "prev_chd", "prev_ckd", "prev_dep", "prev_mh",
       "prev_dem", "prev_hf", "prev_af", "prev_can", "prev_stia", "prev_ob"]


def load() -> pd.DataFrame:
    d = pd.read_csv(SRC)
    d = d[d["list_size"] >= 1000].copy()
    per = lambda c: d[c] / 3 / d["list_size"] * 1000  # noqa: E731  monthly per 1,000 patients
    d["tot_rate"] = per("total_3m")
    d = d[(d["tot_rate"] < 2000) & (d["tot_rate"] > 100) & (d["unmapped_3m"] / d["total_3m"] < 0.4)].copy()
    per = lambda c: d[c] / 3 / d["list_size"] * 1000  # noqa: E731
    d["sd_rate"], d["sd_gp_rate"], d["urg_sd_rate"] = per("sd_3m"), per("sd_gp_3m"), per("urg_sd_3m")
    d["gp_rate"], d["dna_rate"] = per("gp_3m"), per("dna_3m")
    d["dna_pct"] = 100 * d["dna_3m"] / d["total_3m"]
    fte = d["gp_fte"].where(d["gp_fte"] >= 0.5)
    d["sd_gp_per_gp"] = (d["sd_gp_3m"] / 3 / fte).where(lambda x: x < 2000)
    d["gp_per_gp"] = (d["gp_3m"] / 3 / fte).where(lambda x: x < 3000)
    d["ltc_sum"] = d[[c for c in LTC if c in d]].sum(axis=1, min_count=10)
    d["imd_q"] = pd.qcut(d["imd"], 5, labels=QUINTS)
    d["gp_q"] = pd.qcut(d["gp_qual_fte_10k"], 5, labels=QUINTS)
    d["trains"] = d["pct_trainee"] > 0
    return d


def by_q(d: pd.DataFrame, q: str, col: str, stat: str = "median", nd: int = 1) -> list[float]:
    g = d.groupby(q, observed=True)[col]
    s = g.median() if stat == "median" else g.mean()
    return [round(float(s.get(k, np.nan)), nd) for k in QUINTS]


def iqr(d: pd.DataFrame, q: str, col: str) -> list[list[float]]:
    g = d.groupby(q, observed=True)[col]
    return [[round(float(g.quantile(0.25)[k]), 1), round(float(g.quantile(0.75)[k]), 1)] for k in QUINTS]


def pct_diff(a: float, b: float) -> float:
    return round(100 * (b / a - 1), 0)


def main() -> None:
    d = load()
    q1, q5 = d[d["imd_q"] == "Q1"], d[d["imd_q"] == "Q5"]
    med = lambda g, c: float(g[c].median())  # noqa: E731
    wmean = lambda g, c: float((g[c] * g["list_size"]).sum() / g.loc[g[c].notna(), "list_size"].sum())  # noqa: E731

    # Need vs capacity, most vs least deprived fifth of practices (medians).
    compare = []
    for key, label, kind in [
        ("prev_smok", "Smoking", "need"), ("prev_copd", "COPD", "need"), ("prev_mh", "Serious mental illness", "need"),
        ("prev_ob", "Obesity", "need"), ("prev_dm", "Diabetes", "need"), ("prev_dep", "Depression", "need"),
        ("dna_rate", "Missed appointments (DNAs) per patient", "pressure"),
        ("sd_gp_per_gp", "Same-day GP appointments per GP", "pressure"),
        ("pts_per_gp", "Patients per qualified GP", "pressure"),
        ("gp_qual_fte_10k", "Qualified GPs per patient", "capacity"),
        ("trains", "Practices training new GPs", "capacity"),
    ]:
        a = float(q1[key].mean() * 100) if key == "trains" else med(q1, key)
        b = float(q5[key].mean() * 100) if key == "trains" else med(q5, key)
        compare.append({"key": key, "label": label, "kind": kind, "least": round(a, 2), "most": round(b, 2),
                        "diff_pct": pct_diff(a, b)})

    # GPs needed for the most deprived fifth to match the least deprived fifth's staffing (list-weighted).
    gap_10k = wmean(q1, "gp_qual_fte_10k") - wmean(q5, "gp_qual_fte_10k")
    q5_pts = float(q5["list_size"].sum())
    gp_gap = gap_10k * q5_pts / 1e4

    # Staffing lever: adjusted same-day GP appointments per extra qualified GP FTE per 10,000 patients.
    covars = ["imd", "pct_65plus", "pct_0_14", "pct_male", "prev_smok", "ltc_sum", "nurse_fte_10k", "dpc_fte_10k", "admin_fte_10k"]
    m = d.dropna(subset=["sd_gp_rate", "gp_qual_fte_10k", "icb", *covars]).copy()
    for c in covars:
        m[c + "_z"] = (m[c] - m[c].mean()) / m[c].std()
    groups = m["icb"].astype("category").cat.codes
    rhs = "gp_qual_fte_10k + " + " + ".join(c + "_z" for c in covars)
    lever = {}
    for out in ("sd_gp_rate", "urg_sd_rate", "gp_rate"):
        r = smf.ols(f"{out} ~ {rhs}", m).fit(cov_type="cluster", cov_kwds={"groups": groups})
        lo, hi = r.conf_int().loc["gp_qual_fte_10k"]
        lever[out] = {"per_gp_per_10k": round(float(r.params["gp_qual_fte_10k"]), 1),
                      "ci": [round(float(lo), 1), round(float(hi), 1)], "n": int(r.nobs), "r2": round(float(r.rsquared), 3)}

    # Is the deprivation-staffing gradient just regional? Compare with ICB fixed effects.
    s = d.dropna(subset=["gp_qual_fte_10k", "imd", "icb"])
    raw = smf.ols("gp_qual_fte_10k ~ imd", s).fit().params["imd"] * 10
    within = smf.ols("gp_qual_fte_10k ~ imd + C(icb)", s).fit().params["imd"] * 10

    # Strongest single associations in the data (Spearman), for transparency.
    access = {"sd_rate": "Same-day appointments per 1,000", "sd_gp_rate": "Same-day GP appointments per 1,000",
              "urg_sd_rate": "Urgent same-day appointments per 1,000", "tot_rate": "All appointments per 1,000",
              "dna_pct": "DNA rate"}
    factors = {"imd": "Deprivation (IMD 2025)", "pct_65plus": "Patients aged 65+", "pct_0_14": "Patients aged 0–14",
               "pct_male": "Male patients", "prev_smok": "Smoking prevalence", "ltc_sum": "Long-term condition burden",
               "prev_dm": "Diabetes", "prev_mh": "Serious mental illness", "prev_copd": "COPD", "prev_ast": "Asthma",
               "prev_chd": "CHD", "gp_qual_fte_10k": "Qualified GPs per 10,000", "pts_per_gp": "Patients per qualified GP",
               "nurse_fte_10k": "Nurses per 10,000", "dpc_fte_10k": "Other clinical staff per 10,000"}
    assoc = []
    for a, al in access.items():
        for f, fl in factors.items():
            x = d[[a, f]].dropna()
            assoc.append({"measure": al, "factor": fl, "rho": round(float(x[a].corr(x[f], method="spearman")), 2), "n": len(x)})
    assoc.sort(key=lambda r: -abs(r["rho"]))

    rho = lambda a, b: round(float(d[[a, b]].dropna().corr(method="spearman").iloc[0, 1]), 2)  # noqa: E731
    quoted = {"dna_age": rho("dna_pct", "pct_65plus"), "dna_imd": rho("dna_pct", "imd"),
              "sdgp_gp": rho("sd_gp_rate", "gp_qual_fte_10k"), "sd_imd": rho("sd_rate", "imd"),
              "urg_imd": rho("urg_sd_rate", "imd")}
    s_icb = d.dropna(subset=["sd_gp_per_gp", "icb"])
    within_pergp = smf.ols("sd_gp_per_gp ~ C(imd_q) + C(icb)", s_icb).fit().params.filter(like="Q5").iloc[0]

    story = {
        "rho": quoted,
        "age65": {"least": round(med(q1, "pct_65plus"), 1), "most": round(med(q5, "pct_65plus"), 1)},
        "within_icb_sd_gp_per_gp_q5_vs_q1": round(float(within_pergp), 0),
        "practices": int(len(d)),
        "patients_m": round(float(d["list_size"].sum()) / 1e6, 1),
        "sd_gp_month_m": round(float(d["sd_gp_3m"].sum()) / 3 / 1e6, 1),
        "sd_month_m": round(float(d["sd_3m"].sum()) / 3 / 1e6, 1),
        "imd_cuts": [round(float(x), 1) for x in d["imd"].quantile([0.2, 0.4, 0.6, 0.8])],
        "by_imd": {
            "gp_qual_fte_10k": by_q(d, "imd_q", "gp_qual_fte_10k", nd=2),
            "pts_per_gp": by_q(d, "imd_q", "pts_per_gp", nd=0),
            "sd_gp_per_gp": by_q(d, "imd_q", "sd_gp_per_gp", nd=0),
            "sd_gp_rate": by_q(d, "imd_q", "sd_gp_rate"),
            "sd_rate": by_q(d, "imd_q", "sd_rate"),
            "urg_sd_rate": by_q(d, "imd_q", "urg_sd_rate"),
            "dna_rate": by_q(d, "imd_q", "dna_rate"),
            "trains_pct": [round(float(v), 0) for v in (100 * d.groupby("imd_q", observed=True)["trains"].mean()).reindex(QUINTS)],
            "ltc_sum": by_q(d, "imd_q", "ltc_sum"),
        },
        "by_gp": {
            "gp_qual_fte_10k": by_q(d, "gp_q", "gp_qual_fte_10k", nd=2),
            "sd_gp_rate": by_q(d, "gp_q", "sd_gp_rate"),
            "sd_gp_rate_iqr": iqr(d, "gp_q", "sd_gp_rate"),
            "urg_sd_rate": by_q(d, "gp_q", "urg_sd_rate"),
            "n": [int(v) for v in d.groupby("gp_q", observed=True).size().reindex(QUINTS)],
        },
        "compare": compare,
        "gp_gap": {"per_10k": round(gap_10k, 2), "q5_patients_m": round(q5_pts / 1e6, 1), "fte": int(round(gp_gap, -1)),
                   "q1_weighted": round(wmean(q1, "gp_qual_fte_10k"), 2), "q5_weighted": round(wmean(q5, "gp_qual_fte_10k"), 2)},
        "lever": lever,
        "gradient": {"raw_per_10_imd": round(float(raw), 3), "within_icb_per_10_imd": round(float(within), 3)},
        "strongest": assoc[:12],
    }
    OUT.write_text(json.dumps(story, indent=1))
    print(json.dumps({k: story[k] for k in ("practices", "gp_gap", "lever", "gradient")}, indent=1))
    print("top association:", story["strongest"][0])


if __name__ == "__main__":
    main()
