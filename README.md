# GP Same-Day Access Explorer

An interactive dashboard comparing **urgent and same-day appointment rates** across ~6,100 GP practices in England. It shows how those rates vary with **deprivation, age structure, comorbidity and smoking**.

The appointments data is the NHS Digital *Appointments in General Practice* practice-level crosstab for June to August 2026 ([August 2026 publication](https://digital.nhs.uk/data-and-information/publications/statistical/appointments-in-general-practice/august-2026)).

## What the dashboard does

- **Access measures** (per 1,000 registered patients per month, or as shares):
  - same-day
  - urgent same-day
  - same-day GP
  - all appointments and GP appointments
  - % booked same day
  - % of urgent-type appointments seen same day
  - % of same-day appointments face-to-face
  - DNA rate
- **Period**: June, July or August 2026, or the 3-month average.
- **Area**: England, a region, an ICB or a PCN. Practices outside the chosen area stay visible in grey for context.
- **Scatter plot** of each practice against the chosen factor, with a binned-median trend line.
- **Quintile chart**: median practice and interquartile range for each national quintile of the factor (e.g. IMD Q1 least deprived to Q5 most deprived).
- **"What lines up with the variation?" table**:
  - an unadjusted Spearman ρ for every factor
  - an adjusted linear model (effect per 1 SD, with 95% CI) whose factors you can tick on or off
- **Compare practices**: search for a practice (or add its whole PCN). Each one is shown against its England percentile and against the value the model *expects* for its population mix.
- **All-practices table**: sortable, filterable and copyable as CSV.
- **Add your own data**: upload any practice-level CSV, for example smoking prevalence, NHS Digital's 5-year age band file or a Fingertips export. Its numeric columns become new factors. Files are processed in the browser only.

## Factors included in this build

| Factor | Source |
|---|---|
| Deprivation | IMD 2025 practice-weighted score (`data/sources/imd_2025_practice.csv`) |
| Age | Share of patients under 17: 1 − NDA list size aged 17+ ÷ registered list, March 2025 |
| CVD comorbidity | QOF 2024-25 prevalence: AF, CHD, heart failure, hypertension, stroke/TIA, PAD |
| Diabetes | National Diabetes Audit 2024-25: type 2 diabetes and non-diabetic hyperglycaemia registrations per list aged 17+ |
| List size | Registered list, December 2025 (also the rate denominator) |

**Not yet included: % male, full age bands and smoking.** These need two files that weren't available in this build:

- NHS Digital's *Patients Registered at a GP Practice* 5-year age file (`gp-reg-pat-prac-quin-age.csv`)
- the QOF smoking / full prevalence file

You can add them in either of two ways:

- upload them in the dashboard's **Add your own practice-level data** panel, or
- drop them into `data/sources/` (any `PREVALENCE_*.csv` / `gp-reg-pat-prac-quin-age*.csv`, or any CSV in `data/sources/extra/`) and rebuild. `build_data.py` picks them up automatically.

## Definitions

- **Same-day**: `TIME_BETWEEN_BOOK_AND_APPT = "Same Day"`.
- **Urgent-type**: national categories *General Consultation Acute*, *Clinical Triage*, *Unplanned Clinical Activity* and *Walk-in*.
- All appointment statuses are counted.
- **Data-quality exclusions** (default on, can be switched off) remove practices with:
  - a list size under 1,000
  - more than 2,000 or fewer than 100 appointments per 1,000 patients a month
  - more than 40% of appointments in unmapped categories

## Rebuilding

```bash
pip install pandas openpyxl
# put Practice_Level_Crosstab_<Mon>_<YY>.csv files and Mapping.csv in data/raw/
python3 scripts/build_data.py      # -> site/data/practices.json, data/processed/practice_access.csv
python3 scripts/build_site.py      # -> site/index.html (standalone page)
cd site && python3 -m http.server  # open http://localhost:8000
```

`site/dashboard.html` is the page source. `site/index.html` is generated from it.

The `site/` folder is fully static, so it can be hosted on GitHub Pages, Vercel or any web server.

## First findings (June to August 2026, 3-month average, outliers excluded)

- The median practice delivers about **199 same-day appointments per 1,000 patients per month**. The 90th-percentile practice delivers 3.5× the 10th.
- Same-day access barely tracks deprivation:
  - the median rises from about 188 in the least-deprived quintile to 207 in the most-deprived
  - rank correlation ρ ≈ 0.06
- **Urgent same-day** rates are flat across deprivation quintiles: a median of about 104 per 1,000 in both Q1 and Q5.
- Taken together, deprivation, age and the disease-prevalence factors explain only about 4% of between-practice variation in same-day rates. Most of the variation is practice-level: how each practice organises access and records its appointments.
