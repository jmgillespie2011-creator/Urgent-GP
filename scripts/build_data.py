#!/usr/bin/env python3
"""Build the practice-level dataset behind the urgent / same-day access dashboard.

Inputs (see README for where each comes from):
  data/raw/Practice_Level_Crosstab_<Mon>_<YY>.csv   NHS Digital Appointments in General Practice
  data/raw/Mapping.csv                              practice -> PCN / sub-ICB / ICB / region
  data/sources/list_sizes_*.csv                     registered list size (denominator)
  data/sources/imd_2025_practice.csv                practice-weighted IMD 2025 score
  data/sources/qof_2425_cvd_prevalence.csv          QOF 2024-25 CVD register prevalence
  data/sources/nda_2425_practice.csv                NDA 2024-25 T2DM / NDH registrations, list 17+
  data/sources/gp-reg-pat-prac-all-*.csv            registered patients (for the under-17 share)

Optional inputs, picked up automatically when present in data/sources/:
  gp-reg-pat-prac-quin-age*.csv     NHS Digital 5-year age bands by sex -> % 65+, % 0-14, % male
  PREVALENCE_*.csv / qof*prev*.csv  QOF practice prevalence file (all registers, inc. SMOK)
  extra/*.csv                        any CSV with a practice code column + numeric columns

Output: site/data/practices.json (compact, loaded by the dashboard)
        data/processed/practice_access.csv (flat, one row per practice, 3-month totals)
"""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SRC = ROOT / "data" / "sources"
OUT_JSON = ROOT / "site" / "data" / "practices.json"
OUT_CSV = ROOT / "data" / "processed" / "practice_access.csv"

# Categories treated as urgent / on-the-day demand in the national category mapping.
URGENT_CATEGORIES = {
    "General Consultation Acute",
    "Clinical Triage",
    "Unplanned Clinical Activity",
    "Walk-in",
}
UNMAPPED_CATEGORIES = {"Inconsistent Mapping", "Unmapped"}

# Count fields stored per practice per month; the dashboard derives every metric from these.
COUNT_FIELDS = [
    "total",      # all appointments
    "gp",         # GP appointments
    "sd",         # booked same day
    "sd_gp",      # booked same day, GP
    "sd_f2f",     # booked same day, face-to-face
    "urg",        # urgent-type category, any lead time
    "urg_sd",     # urgent-type category, booked same day
    "dna",        # did not attend
    "unmapped",   # category = Inconsistent Mapping / Unmapped (data quality)
]

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def norm_code(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


# --------------------------------------------------------------------------- appointments
def crosstab_files() -> list[tuple[str, Path]]:
    files = []
    for p in RAW.glob("Practice_Level_Crosstab_*.csv"):
        m = re.search(r"_([A-Z][a-z]{2})_(\d{2})\.csv$", p.name)
        if m:
            key = f"20{m.group(2)}-{MONTHS[m.group(1)]:02d}"
            files.append((key, p))
    if not files:
        raise SystemExit(f"No Practice_Level_Crosstab_*.csv files found in {RAW}")
    return sorted(files)


def aggregate_month(path: Path) -> pd.DataFrame:
    usecols = ["GP_CODE", "HCP_TYPE", "APPT_MODE", "NATIONAL_CATEGORY",
               "TIME_BETWEEN_BOOK_AND_APPT", "COUNT_OF_APPOINTMENTS", "APPT_STATUS"]
    df = pd.read_csv(path, usecols=usecols, dtype=str)
    n = pd.to_numeric(df["COUNT_OF_APPOINTMENTS"], errors="coerce").fillna(0).astype("int64")
    same_day = df["TIME_BETWEEN_BOOK_AND_APPT"].str.strip().eq("Same Day")
    gp = df["HCP_TYPE"].eq("GP")
    f2f = df["APPT_MODE"].eq("Face-to-Face")
    urgent = df["NATIONAL_CATEGORY"].isin(URGENT_CATEGORIES)
    flags = pd.DataFrame({
        "code": norm_code(df["GP_CODE"]),
        "total": n,
        "gp": n.where(gp, 0),
        "sd": n.where(same_day, 0),
        "sd_gp": n.where(same_day & gp, 0),
        "sd_f2f": n.where(same_day & f2f, 0),
        "urg": n.where(urgent, 0),
        "urg_sd": n.where(urgent & same_day, 0),
        "dna": n.where(df["APPT_STATUS"].eq("DNA"), 0),
        "unmapped": n.where(df["NATIONAL_CATEGORY"].isin(UNMAPPED_CATEGORIES), 0),
    })
    return flags.groupby("code", sort=True).sum()


def practice_names(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["GP_CODE", "GP_NAME", "SUPPLIER"], dtype=str).drop_duplicates("GP_CODE")
    df["code"] = norm_code(df["GP_CODE"])
    return df.set_index("code")[["GP_NAME", "SUPPLIER"]]


# --------------------------------------------------------------------------- covariates
def load_list_size() -> pd.Series:
    files = sorted(SRC.glob("list_sizes_*.csv"))
    if not files:
        raise SystemExit("Missing data/sources/list_sizes_*.csv")
    df = pd.read_csv(files[-1], dtype={"row_id": str})
    print(f"  list size: {files[-1].name} ({len(df)} practices)")
    return pd.Series(pd.to_numeric(df["total_list_size"], errors="coerce").values,
                     index=norm_code(df["row_id"]), name="list_size")


def load_imd() -> pd.Series:
    df = pd.read_csv(SRC / "imd_2025_practice.csv", dtype=str)
    df = df[~df["Practice_Code"].str.startswith("E92")]
    return pd.Series(pd.to_numeric(df["IMD_Score_2025"], errors="coerce").values,
                     index=norm_code(df["Practice_Code"]), name="imd")


def load_qof_cvd() -> pd.DataFrame:
    df = pd.read_csv(SRC / "qof_2425_cvd_prevalence.csv", dtype={"practice_code": str})
    df.index = norm_code(df.pop("practice_code"))
    return df.rename(columns={
        "af_prev": "prev_af", "chd_prev": "prev_chd", "hf_prev": "prev_hf",
        "hyp_prev": "prev_hyp", "stia_prev": "prev_stia", "pad_prev": "prev_pad",
    }).apply(pd.to_numeric, errors="coerce")


def extract_nda() -> pd.DataFrame | None:
    """NDA 2024-25 (NDH tables) -> practice T2DM / NDH registrations and list size aged 17+."""
    csv = SRC / "nda_2425_practice.csv"
    xlsx = RAW / "NDA24_25.xlsx"
    if not csv.exists() and xlsx.exists():
        raw = pd.read_excel(xlsx, sheet_name="Registrations & demographics", header=None, dtype=object)
        # Columns: 4 GP code, 6 list size 17+, 8/9 T2DM n/%, 10/11 NDH n/%
        rows = raw[raw[4].astype(str).str.fullmatch(r"[A-Z]\d{5}")]
        out = pd.DataFrame({
            "practice_code": rows[4].astype(str),
            "list_17plus": pd.to_numeric(rows[6], errors="coerce"),
            "t2dm_n": pd.to_numeric(rows[8], errors="coerce"),
            "ndh_n": pd.to_numeric(rows[10], errors="coerce"),
        })
        out.to_csv(csv, index=False)
        print(f"  extracted {len(out)} practices from {xlsx.name} -> {csv.name}")
    if not csv.exists():
        return None
    df = pd.read_csv(csv, dtype={"practice_code": str})
    df.index = norm_code(df.pop("practice_code"))
    res = pd.DataFrame(index=df.index)
    res["prev_t2dm"] = 100 * df["t2dm_n"] / df["list_17plus"]
    res["prev_ndh"] = 100 * df["ndh_n"] / df["list_17plus"]
    res["list_17plus"] = df["list_17plus"]
    return res


def load_under17(list17: pd.Series | None) -> pd.Series | None:
    files = sorted(SRC.glob("gp-reg-pat-prac-all*.csv"))
    if not files or list17 is None:
        return None
    df = pd.read_csv(files[-1], dtype=str)
    df = df[(df["SEX"] == "ALL") & (df["AGE"] == "ALL")]
    tot = pd.Series(pd.to_numeric(df["NUMBER_OF_PATIENTS"], errors="coerce").values,
                    index=norm_code(df["CODE"]))
    share = 100 * (1 - list17.reindex(tot.index) / tot)
    # Guard against list-size mismatches between the two extracts.
    return share.where((share >= 0) & (share <= 60)).rename("pct_u17")


def load_quinary_age() -> pd.DataFrame | None:
    files = sorted(SRC.glob("gp-reg-pat-prac-quin-age*.csv"))
    if not files:
        return None
    df = pd.read_csv(files[-1], dtype=str)
    code_col = "ORG_CODE" if "ORG_CODE" in df.columns else "CODE"
    df["n"] = pd.to_numeric(df["NUMBER_OF_PATIENTS"], errors="coerce").fillna(0)
    df["code"] = norm_code(df[code_col])
    age = df["AGE_GROUP_5"].astype(str)
    lower = pd.to_numeric(age.str.extract(r"^(\d+)")[0], errors="coerce")
    by_sex = df[(df["SEX"].isin(["MALE", "FEMALE"])) & (age != "ALL")]
    tot = by_sex.groupby("code")["n"].sum()
    res = pd.DataFrame(index=tot.index)
    res["pct_65plus"] = 100 * by_sex[lower.loc[by_sex.index] >= 65].groupby("code")["n"].sum() / tot
    res["pct_80plus"] = 100 * by_sex[lower.loc[by_sex.index] >= 80].groupby("code")["n"].sum() / tot
    res["pct_0_14"] = 100 * by_sex[lower.loc[by_sex.index] < 15].groupby("code")["n"].sum() / tot
    res["pct_male"] = 100 * by_sex[by_sex["SEX"] == "MALE"].groupby("code")["n"].sum() / tot
    print(f"  age/sex: {files[-1].name}")
    return res


def load_qof_prevalence_file() -> pd.DataFrame | None:
    """Official QOF practice prevalence CSV (PRACTICE_CODE, GROUP_CODE, REGISTER, PRACTICE_LIST_SIZE)."""
    files = [p for p in SRC.glob("*.csv") if re.search(r"(PREVALENCE_\d+|qof.*prev)", p.name, re.I)
             and "cvd_prevalence" not in p.name]
    frames = []
    for p in files:
        df = pd.read_csv(p, dtype=str)
        cols = {c.upper(): c for c in df.columns}
        if not {"PRACTICE_CODE", "GROUP_CODE", "REGISTER"} <= set(cols):
            continue
        denom_col = cols.get("PRACTICE_LIST_SIZE") or cols.get("LIST_SIZE")
        if denom_col is None:
            continue
        df["code"] = norm_code(df[cols["PRACTICE_CODE"]])
        df["reg"] = pd.to_numeric(df[cols["REGISTER"]], errors="coerce")
        df["den"] = pd.to_numeric(df[denom_col], errors="coerce")
        df["grp"] = df[cols["GROUP_CODE"]].str.strip().str.lower()
        df = df.dropna(subset=["reg", "den"])
        wide = (100 * df["reg"] / df["den"]).groupby([df["code"], df["grp"]]).mean().unstack()
        wide.columns = [f"prev_{c}" for c in wide.columns]
        frames.append(wide)
        print(f"  QOF prevalence: {p.name} ({wide.shape[1]} registers)")
    return pd.concat(frames, axis=1) if frames else None


def load_extra() -> pd.DataFrame | None:
    frames = []
    for p in sorted((SRC / "extra").glob("*.csv")):
        df = pd.read_csv(p, dtype=str)
        code_col = next((c for c in df.columns if re.search(r"(practice|gp|org|prac|area).*code|^code$", c, re.I)), None)
        if code_col is None:
            print(f"  skip {p.name}: no practice code column")
            continue
        df.index = norm_code(df.pop(code_col))
        num = df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        num.columns = [f"x_{re.sub(r'[^a-z0-9]+', '_', c.lower()).strip('_')}" for c in num.columns]
        frames.append(num.groupby(level=0).mean())
        print(f"  extra: {p.name} ({num.shape[1]} columns)")
    return pd.concat(frames, axis=1) if frames else None


def load_wide(name: str) -> pd.DataFrame | None:
    """A practice_code + numeric columns CSV written by scripts/fetch_nhs_data.py."""
    path = SRC / name
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"practice_code": str})
    df.index = norm_code(df.pop("practice_code"))
    df = df.apply(pd.to_numeric, errors="coerce")
    print(f"  {name}: {len(df)} practices, {df.shape[1]} columns")
    return df[~df.index.duplicated()]


def fetch_manifest() -> dict:
    path = SRC / "fetch_manifest.json"
    return json.loads(path.read_text()) if path.exists() else {}


FT_RULES = [  # (regex on Fingertips indicator name, key, label, group)
    (r"aged? 65\+|65\+ years", "pct_65plus", "Patients aged 65+ (%)", "Age & sex"),
    (r"aged? 75\+|75\+ years", "pct_75plus", "Patients aged 75+ (%)", "Age & sex"),
    (r"aged? 85\+|85\+ years", "pct_85plus", "Patients aged 85+ (%)", "Age & sex"),
    (r"under 18", "pct_u18", "Patients aged under 18 (%)", "Age & sex"),
    (r"aged 0 to 4", "pct_0_4", "Patients aged 0–4 (%)", "Age & sex"),
    (r"^% male|\bmale\b.*%", "pct_male", "Male patients (%)", "Age & sex"),
]
FT_QOF = {"diabetes": "dm", "asthma": "ast", "copd": "copd", "depression": "dep", "mental health": "mh",
          "ckd": "ckd", "chronic kidney": "ckd", "cancer": "can", "dementia": "dem", "obesity": "ob",
          "epilepsy": "ep", "learning disab": "ld", "osteoporosis": "ost", "rheumatoid": "ra",
          "palliative": "pc", "hypertension": "hyp", "chd": "chd", "coronary": "chd", "heart failure": "hf",
          "atrial fibrillation": "af", "stroke": "stia", "peripheral arterial": "pad", "pad": "pad",
          "non-diabetic hyperglycaemia": "ndh", "ndh": "ndh"}


def load_fingertips() -> tuple[pd.DataFrame | None, dict]:
    """Fingertips GP profile indicators -> named factors (age bands, QOF prevalence)."""
    wide = load_wide("fingertips_practice.csv")
    meta_path = SRC / "fingertips_practice_meta.json"
    if wide is None or not meta_path.exists():
        return None, {}
    out, labels = pd.DataFrame(index=wide.index), {}
    for m in json.loads(meta_path.read_text()):
        name, key = m["name"], None
        low = name.lower()
        for rx, k, label, group in FT_RULES:
            if re.search(rx, low):
                key, labels[k] = k, (label, f"Fingertips GP profile: {name} ({m['period']})", group, "%")
                break
        if key is None and "prevalence" in low and "qof" in low:
            g = next((v for kw, v in FT_QOF.items() if kw in low), None)
            if g:
                key = f"prev_{g}"
                labels[key] = (f"{QOF_GROUP_NAMES.get(g, g.upper())} prevalence (%)",
                               f"QOF via Fingertips ({m['period']})", "Comorbidity", "%")
        if key and key not in out.columns and m["key"] in wide.columns:
            out[key] = wide[m["key"]]
    return out, labels


# GP workforce (practice level). Column names follow the NHS Digital GPW CSV.
WORKFORCE_FTE = {
    "gp_fte": ["TOTAL_GP_FTE"],
    "gp_qual_fte": ["TOTAL_GP_EXTGL_FTE", "TOTAL_GP_EXTG_FTE", "TOTAL_GP_EXL_FTE"],
    "nurse_fte": ["TOTAL_NURSES_FTE", "TOTAL_NURSE_FTE"],
    "dpc_fte": ["TOTAL_DPC_FTE"],
    "admin_fte": ["TOTAL_ADMIN_FTE"],
    "locum_fte": ["TOTAL_LOCUUM_TRN_FTE", "TOTAL_LOCUM_FTE", "TOTAL_GP_LOCUM_FTE"],
    "trainee_fte": ["TOTAL_GP_TRN_GR_FTE", "TOTAL_GP_TRAINEE_FTE"],
}


def load_workforce(list_size: pd.Series) -> pd.DataFrame | None:
    wf = load_wide("workforce_practice.csv")
    if wf is None:
        return None
    cols = {c.upper(): c for c in wf.columns}
    fte = pd.DataFrame(index=wf.index)
    for key, options in WORKFORCE_FTE.items():
        col = next((cols[o] for o in options if o in cols), None)
        if col is not None:
            fte[key] = wf[col]
    print("  workforce columns used:", {k: next(o for o in WORKFORCE_FTE[k] if o in cols) for k in fte.columns})
    pts = wf[cols["TOTAL_PATIENTS"]] if "TOTAL_PATIENTS" in cols else list_size.reindex(wf.index)
    pts = pts.where(pts > 0)
    res = pd.DataFrame(index=wf.index)
    for key in fte.columns:
        if key in ("locum_fte", "trainee_fte"):
            continue
        res[f"{key}_10k"] = 10000 * fte[key] / pts
    if "gp_qual_fte" in fte:
        res["pts_per_gp"] = pts / fte["gp_qual_fte"].where(fte["gp_qual_fte"] > 0)
    if "gp_fte" in fte:
        res["gp_fte"] = fte["gp_fte"]
        for extra in ("locum_fte", "trainee_fte"):
            if extra in fte:
                res[f"pct_{extra[:-4]}"] = 100 * fte[extra] / fte["gp_fte"].where(fte["gp_fte"] > 0)
    # Workforce returns occasionally carry placeholder or wildly implausible values.
    for c in res.columns:
        if c.endswith("_10k"):
            res[c] = res[c].where((res[c] >= 0) & (res[c] < 60))
    return res


# --------------------------------------------------------------------------- main
COVARIATE_META = {
    "imd": ("Deprivation (IMD 2025 score)", "IMD 2025 practice-weighted score", "Deprivation", ""),
    "pct_u17": ("Patients aged under 17 (%)", "Derived: 1 − NDA list 17+ ÷ registered list (Mar 2025)", "Age & sex", "%"),
    "pct_0_14": ("Patients aged 0–14 (%)", "NHS Digital registered patients, 5-year bands", "Age & sex", "%"),
    "pct_65plus": ("Patients aged 65+ (%)", "NHS Digital registered patients, 5-year bands", "Age & sex", "%"),
    "pct_75plus": ("Patients aged 75+ (%)", "NHS Digital registered patients, 5-year bands", "Age & sex", "%"),
    "pct_80plus": ("Patients aged 80+ (%)", "NHS Digital registered patients, 5-year bands", "Age & sex", "%"),
    "pct_male": ("Male patients (%)", "NHS Digital registered patients, 5-year bands", "Age & sex", "%"),
    "prev_hyp": ("Hypertension prevalence (%)", "QOF 2024-25", "Comorbidity", "%"),
    "prev_chd": ("CHD prevalence (%)", "QOF 2024-25", "Comorbidity", "%"),
    "prev_af": ("Atrial fibrillation prevalence (%)", "QOF 2024-25", "Comorbidity", "%"),
    "prev_hf": ("Heart failure prevalence (%)", "QOF 2024-25", "Comorbidity", "%"),
    "prev_stia": ("Stroke/TIA prevalence (%)", "QOF 2024-25", "Comorbidity", "%"),
    "prev_pad": ("PAD prevalence (%)", "QOF 2024-25", "Comorbidity", "%"),
    "prev_t2dm": ("Type 2 diabetes prevalence, 17+ (%)", "National Diabetes Audit 2024-25", "Comorbidity", "%"),
    "prev_ndh": ("Non-diabetic hyperglycaemia (%)", "National Diabetes Audit 2024-25", "Comorbidity", "%"),
    "prev_smok": ("Smoking prevalence, 15+ (%)", "QOF-recorded current smokers (Fingertips 91280)", "Smoking", "%"),
    "gp_fte_10k": ("GP FTE per 10,000 patients", "NHS Digital GP workforce, all GPs inc. trainees and locums", "Workforce", ""),
    "gp_qual_fte_10k": ("Qualified GP FTE per 10,000 patients", "NHS Digital GP workforce, excluding GPs in training", "Workforce", ""),
    "nurse_fte_10k": ("Nurse FTE per 10,000 patients", "NHS Digital GP workforce", "Workforce", ""),
    "dpc_fte_10k": ("Direct patient care FTE per 10,000 patients", "NHS Digital GP workforce (practice-employed; excludes PCN ARRS staff)", "Workforce", ""),
    "admin_fte_10k": ("Admin/non-clinical FTE per 10,000 patients", "NHS Digital GP workforce", "Workforce", ""),
    "pts_per_gp": ("Patients per qualified GP FTE", "NHS Digital GP workforce", "Workforce", ""),
    "pct_locum": ("Locum share of GP FTE (%)", "NHS Digital GP workforce", "Workforce", "%"),
    "pct_trainee": ("GPs in training share of GP FTE (%)", "NHS Digital GP workforce", "Workforce", "%"),
    "list_size": ("Registered list size", "NHS Digital, list size used as denominator", "Practice", ""),
}
QOF_GROUP_NAMES = {  # noqa: also used by load_fingertips
    "dm": "Diabetes", "ast": "Asthma", "copd": "COPD", "dep": "Depression", "mh": "Serious mental illness",
    "ckd": "CKD", "can": "Cancer", "dem": "Dementia", "ob": "Obesity", "ep": "Epilepsy",
    "ld": "Learning disability", "ost": "Osteoporosis", "ra": "Rheumatoid arthritis", "pc": "Palliative care",
    "ndh": "Non-diabetic hyperglycaemia", "cvdpp": "CVD primary prevention", "bp": "Blood pressure", "smok": "Smoking",
}


def main() -> None:
    print("Appointments:")
    months = crosstab_files()
    counts = {}
    for key, path in months:
        counts[key] = aggregate_month(path)
        print(f"  {key}: {path.name} -> {len(counts[key])} practices, {int(counts[key]['total'].sum()):,} appts")
    latest = months[-1][1]

    mapping = pd.read_csv(RAW / "Mapping.csv", dtype=str)
    mapping.index = norm_code(mapping.pop("GP_CODE"))
    mapping = mapping[~mapping.index.duplicated(keep="last")]
    names = practice_names(latest)

    print("Covariates:")
    cov = pd.DataFrame({"list_size": load_list_size(), "imd": load_imd()})
    cov = cov.join(load_qof_cvd(), how="outer")
    nda = extract_nda()
    if nda is not None:
        cov = cov.join(nda[["prev_t2dm", "prev_ndh"]], how="outer")
        u17 = load_under17(nda["list_17plus"])
        if u17 is not None:
            cov = cov.join(u17, how="outer")
    manifest = fetch_manifest()
    # Newer full QOF prevalence file replaces the CVD-only 2024-25 extract where both exist.
    qof_full = load_wide("qof_prevalence_practice.csv")
    qof_year = manifest.get("qof", {}).get("year", "")
    if qof_full is not None:
        cov = cov.drop(columns=[c for c in qof_full.columns if c in cov.columns])
        cov = cov.join(qof_full, how="outer")
        for c in qof_full.columns:
            if c in COVARIATE_META:
                label, _, group, unit = COVARIATE_META[c]
                COVARIATE_META[c] = (label, f"QOF {qof_year}", group, unit)
    age = load_wide("age_sex_practice.csv")
    if age is not None:
        cov = cov.join(age.drop(columns=["registered_patients"], errors="ignore"), how="outer")
    smoking = load_wide("smoking_practice.csv")
    if smoking is not None:
        cov = cov.join(smoking[["smoking_prev_15plus"]].rename(columns={"smoking_prev_15plus": "prev_smok"}), how="outer")
        period = manifest.get("smoking", {}).get("period")
        if period:
            label, src, group, unit = COVARIATE_META["prev_smok"]
            COVARIATE_META["prev_smok"] = (label, f"{src}, {period}", group, unit)
    ft, ft_labels = load_fingertips()
    if ft is not None:
        add = [c for c in ft.columns if c not in cov.columns]
        cov = cov.join(ft[add], how="outer")
        for c in add:
            if c not in COVARIATE_META or c.startswith("pct_"):
                COVARIATE_META[c] = ft_labels[c]
        print("  Fingertips factors added:", ", ".join(add) or "none")
    wf = load_workforce(cov["list_size"])
    if wf is not None:
        cov = cov.join(wf, how="outer")
        wf_page = manifest.get("workforce", {}).get("page", "")
        when = wf_page.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        for k in list(COVARIATE_META):
            if COVARIATE_META[k][2] == "Workforce" and when:
                label, src, group, unit = COVARIATE_META[k]
                COVARIATE_META[k] = (label, f"{src}, {when}", group, unit)
    extras = [load_extra()]
    if age is None:
        extras.insert(0, load_quinary_age())
    if qof_full is None:
        extras.insert(0, load_qof_prevalence_file())
    for extra in extras:
        if extra is not None:
            cov = cov.combine_first(extra) if set(extra.columns) & set(cov.columns) else cov.join(extra, how="outer")

    cov = cov.groupby(level=0).first()
    codes = sorted(set().union(*[c.index for c in counts.values()]))
    month_keys = [k for k, _ in months]

    practices = []
    flat_rows = []
    for code in codes:
        m = mapping.loc[code] if code in mapping.index else None
        nm = names.loc[code] if code in names.index else None
        rec = {
            "c": code,
            "n": (m["GP_NAME"] if m is not None else (nm["GP_NAME"] if nm is not None else code)).title(),
            "pcn": m["PCN_NAME"] if m is not None else "",
            "sub": m["SUB_ICB_LOCATION_NAME"] if m is not None else "",
            "icb": m["ICB_NAME"] if m is not None else "",
            "reg": m["REGION_NAME"] if m is not None else "",
            "sup": m["SUPPLIER"] if m is not None else (nm["SUPPLIER"] if nm is not None else ""),
            # counts[field] = [month1, month2, ...]
            "k": {f: [int(counts[mk].at[code, f]) if code in counts[mk].index else None for mk in month_keys]
                  for f in COUNT_FIELDS},
            "v": {},
        }
        if code in cov.index:
            row = cov.loc[code]
            for col, val in row.items():
                if pd.notna(val):
                    rec["v"][col] = round(float(val), 3) if col != "list_size" else int(val)
        practices.append(rec)
        flat = {"practice_code": code, "practice_name": rec["n"], "pcn": rec["pcn"], "sub_icb": rec["sub"],
                "icb": rec["icb"], "region": rec["reg"], "supplier": rec["sup"]}
        for f in COUNT_FIELDS:
            flat[f"{f}_3m"] = sum(x for x in rec["k"][f] if x is not None)
        flat.update(rec["v"])
        flat_rows.append(flat)

    covariates = []
    for col in cov.columns:
        if col in ("list_17plus", "gp_fte") or cov[col].notna().sum() < 100:
            continue
        if col in COVARIATE_META:
            label, source, group, unit = COVARIATE_META[col]
        elif col.startswith("prev_"):
            g = col[5:]
            label, source, group, unit = (f"{QOF_GROUP_NAMES.get(g, g.upper())} prevalence (%)", "QOF prevalence file",
                                          "Smoking" if g == "smok" else "Comorbidity", "%")
        else:
            label, source, group, unit = (col[2:].replace("_", " ").capitalize(), "Extra file", "Other", "")
        covariates.append({"key": col, "label": label, "source": source, "group": group, "unit": unit})
    group_order = ["Deprivation", "Age & sex", "Comorbidity", "Smoking", "Workforce", "Practice", "Other"]
    covariates.sort(key=lambda c: (group_order.index(c["group"]), c["label"]))

    out = {
        "generated": pd.Timestamp.now(tz="Europe/London").strftime("%Y-%m-%d"),
        "months": month_keys,
        "countFields": COUNT_FIELDS,
        "urgentCategories": sorted(URGENT_CATEGORIES),
        "covariates": covariates,
        "practices": practices,
        "sources": manifest,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(flat_rows).to_csv(OUT_CSV, index=False)
    print(f"Wrote {OUT_JSON.relative_to(ROOT)} ({OUT_JSON.stat().st_size/1e6:.1f} MB, {len(practices)} practices)")
    print(f"Wrote {OUT_CSV.relative_to(ROOT)}")
    print("Covariates available:", ", ".join(c["key"] for c in covariates))


if __name__ == "__main__":
    main()
