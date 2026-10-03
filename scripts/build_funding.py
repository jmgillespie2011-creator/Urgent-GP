#!/usr/bin/env python3
"""Core-funding shortfall per practice since 2008/09, its salaried-GP equivalent, and the
projected effect on same-day GP appointments -> site/data/funding.json.

Method
  1. 2008/09 England core + QOF funding (NHS Digital, Investment in General Practice 2008/09-2012/13):
     Global Sum/MPIG + Balance of PMS + PCTMS/APMS core payments + QOF.
     QOF is included because QOF points were moved into the global sum in 2013/14-2014/15.
     Seniority (later also folded into the global sum) is not separable in 2008/09 and is left out,
     which makes the estimated shortfall smaller, i.e. conservative.
  2. Uprate it to the latest payments year by CPI (financial-year average) and by England's population.
  3. Subtract the same components actually paid in that year, summed over practices
     (NHS Payments to General Practice: global sum, MPIG, balance of PMS, QOF).
  4. Share the national shortfall between practices by weighted patients (the global sum's own needs weighting).
  5. Divide by the full employment cost of a salaried GP to get whole-time-equivalent GPs.
  6. Multiply by the adjusted same-day effect of one extra qualified GP FTE (from story.json).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FUND = ROOT / "data" / "funding"
OUT = ROOT / "site" / "data" / "funding.json"

# England 2008/09, £000s (Investment in General Practice 2008/09 to 2012/13, summary table).
CORE_2008 = {"Global Sum/MPIG": 1863957, "Balance of PMS": 2086083,
             "PCTMS & APMS essential & additional services and other payments": 118199,
             "Quality & Outcomes Framework": 1068184}

# Salaried GP full-time (9 sessions) employment cost, by financial year of the payments data.
# Pay: BMA model contract range midpoint. On-costs: employer NI above the secondary threshold and
# the employer pension contribution practices pay (14.38%; the remainder is funded centrally).
GP_COST = {
    "2023-24": {"pay_min": 68975, "pay_max": 104085, "ni_rate": 0.138, "ni_threshold": 9100, "pension": 0.1438},
    "2024-25": {"pay_min": 73113, "pay_max": 110330, "ni_rate": 0.138, "ni_threshold": 9100, "pension": 0.1438},
    "2025-26": {"pay_min": 76038, "pay_max": 114743, "ni_rate": 0.15, "ni_threshold": 5000, "pension": 0.1438},
    "2026-27": {"pay_min": 78699, "pay_max": 118759, "ni_rate": 0.15, "ni_threshold": 5000, "pension": 0.1438},
}


def gp_cost(year: str) -> dict:
    c = GP_COST.get(year) or GP_COST[min(GP_COST, key=lambda y: abs(int(y[:4]) - int(year[:4])))]
    pay = (c["pay_min"] + c["pay_max"]) / 2
    ni = c["ni_rate"] * max(0.0, pay - c["ni_threshold"])
    pen = c["pension"] * pay
    return {**c, "pay_mid": round(pay), "ni": round(ni), "pension_cost": round(pen), "total": round(pay + ni + pen)}


def cpi_fy(start_year: int) -> float:
    """Mean CPI (ONS D7BT, 2015=100) over April start_year to March start_year+1."""
    df = pd.read_csv(FUND / "cpi_d7bt.csv", dtype=str)
    months = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
    vals = []
    for per, v in zip(df["period"], df["value"]):
        m = re.fullmatch(r"(\d{4}) ([A-Z]{3})", str(per).strip())
        if not m:
            continue
        y, mo = int(m.group(1)), months[m.group(2)]
        if (y == start_year and mo >= 4) or (y == start_year + 1 and mo <= 3):
            vals.append(float(v))
    if len(vals) < 12:
        raise SystemExit(f"CPI for {start_year}/{start_year + 1 - 2000:02d} incomplete ({len(vals)} months)")
    return sum(vals) / len(vals)


def population(year: int) -> float:
    df = pd.read_csv(FUND / "england_population_enpop.csv", dtype=str)
    row = df[df["period"].str.strip() == str(year)]
    return float(str(row["value"].iloc[0]).replace(",", ""))


def norm(c: str) -> str:
    return re.sub(r"[^a-z]", "", c.lower())


def load_payments() -> tuple[pd.DataFrame, str]:
    files = sorted((FUND / "payments").glob("*.csv"))
    best = None
    for p in files:
        m = re.search(r"(20\d\d|\d\d)[-_](\d\d)", p.name)
        if not m:
            continue
        y0 = int(m.group(1)) if len(m.group(1)) == 4 else 2000 + int(m.group(1))
        if best is None or y0 > best[0]:
            best = (y0, p)
    if best is None:
        raise SystemExit("no payments file in data/funding/payments")
    y0, path = best
    df = pd.read_csv(path, encoding="latin1", low_memory=False)
    cols = {norm(c): c for c in df.columns}

    def col(*keys: str) -> str | None:
        for k in keys:
            hit = next((orig for n, orig in cols.items() if n.startswith(k)), None) or \
                next((orig for n, orig in cols.items() if k in n), None)
            if hit:
                return hit
        return None

    pick = {
        "code": col("practicecode"),
        "registered": col("numberofregisteredpatients"),
        "weighted": col("numberofweightedpatients"),
        "global_sum": col("globalsum"),
        "mpig": col("mpigcorrection"),
        "pms": col("balanceofpms"),
        "qof": col("totalqofpayments", "totalqof"),
        "total": col("totalnhspaymentstogeneralpracticeminusdeductions", "totalnhspaymentstogeneralpractice"),
    }
    missing = [k for k, v in pick.items() if v is None and k in ("code", "weighted", "global_sum", "qof")]
    if missing:
        raise SystemExit(f"{path.name}: missing columns {missing}")
    out = pd.DataFrame({k: df[v] for k, v in pick.items() if v})
    for k in out.columns:
        if k != "code":
            out[k] = pd.to_numeric(out[k].astype(str).str.replace(r"[£,\s]", "", regex=True), errors="coerce").fillna(0)
    out["code"] = out["code"].astype(str).str.strip().str.upper()
    return out, f"{y0}-{(y0 + 1) % 100:02d}"


def main() -> None:
    pay, year = load_payments()
    y0 = int(year[:4])
    pay["core_now"] = pay[[c for c in ("global_sum", "mpig", "pms", "qof") if c in pay]].sum(axis=1)
    pay = pay[(pay["weighted"] > 0) & (pay["core_now"] > 0)]

    core_2008 = sum(CORE_2008.values()) * 1000
    cpi_ratio = cpi_fy(y0) / cpi_fy(2008)
    pop_ratio = population(y0) / population(2008)
    expected = core_2008 * cpi_ratio * pop_ratio
    actual = float(pay["core_now"].sum())
    gap_total = expected - actual
    weighted_total = float(pay["weighted"].sum())
    gap_per_weighted = gap_total / weighted_total

    story = json.loads((ROOT / "site" / "data" / "story.json").read_text())
    lever = story["lever"]["sd_gp_rate"]  # same-day GP appts per 1,000 patients a month per +1 GP FTE per 10,000
    per_gp_month = lever["per_gp_per_10k"] * 10  # = extra same-day GP appointments a month per added GP FTE
    per_gp_ci = [lever["ci"][0] * 10, lever["ci"][1] * 10]
    cost = gp_cost(year)

    pay["shortfall"] = pay["weighted"] * gap_per_weighted
    pay["gps"] = pay["shortfall"] / cost["total"]
    pay["sd_gp_extra_month"] = pay["gps"] * per_gp_month

    practices = {r.code: [round(r.shortfall), round(r.gps, 2), round(r.sd_gp_extra_month), round(r.weighted), round(r.core_now)]
                 for r in pay.itertuples()}
    total_gps = float(pay["gps"].sum())
    out = {
        "year": year,
        "method": "2008/09 core + QOF uprated by CPI and population, minus actual core + QOF, shared by weighted patients",
        "core_2008_m": round(core_2008 / 1e6, 1),
        "core_2008_components_m": {k: round(v / 1000, 1) for k, v in CORE_2008.items()},
        "cpi_ratio": round(cpi_ratio, 4), "pop_ratio": round(pop_ratio, 4),
        "cpi_2008_09": round(cpi_fy(2008), 2), f"cpi_{year}": round(cpi_fy(y0), 2),
        "pop_2008": population(2008), f"pop_{y0}": population(y0),
        "expected_m": round(expected / 1e6, 1), "actual_m": round(actual / 1e6, 1), "gap_m": round(gap_total / 1e6, 1),
        "gap_pct_of_expected": round(100 * gap_total / expected, 1),
        "per_head_2008": round(core_2008 / population(2008), 2),
        "per_head_expected": round(expected / population(y0), 2), "per_head_actual": round(actual / population(y0), 2),
        "gap_per_weighted_patient": round(gap_per_weighted, 2),
        "weighted_total": round(weighted_total), "practices_n": len(pay),
        "gp_cost": cost,
        "total_gps": round(total_gps), "sd_per_gp_month": round(per_gp_month), "sd_per_gp_ci": [round(x) for x in per_gp_ci],
        "total_sd_gp_extra_month": round(total_gps * per_gp_month),
        "total_sd_gp_extra_month_ci": [round(total_gps * x) for x in per_gp_ci],
        "practice_fields": ["shortfall_gbp", "salaried_gp_wte", "extra_same_day_gp_appts_month", "weighted_patients", "core_now_gbp"],
        "practices": practices,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(json.dumps({k: v for k, v in out.items() if k != "practices"}, indent=1))


if __name__ == "__main__":
    main()
