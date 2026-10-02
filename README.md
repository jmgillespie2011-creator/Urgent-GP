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
- **Workforce and access**: GP, nurse, direct-patient-care and admin FTE per 10,000 patients, and patients per GP. For each measure the panel shows:
  - median access in each fifth of practices by staffing
  - its effect once population mix is held constant

  Also added: two output measures (GP appointments and same-day GP appointments per GP FTE per month), and a model preset that adds staffing to the population factors.
- **Add your own data**: upload any practice-level CSV, for example smoking prevalence, NHS Digital's 5-year age band file or a Fingertips export. Its numeric columns become new factors. Files are processed in the browser only.

## Factors included in this build

| Group | Factors | Source |
|---|---|---|
| Deprivation | IMD 2025 score | Practice-weighted IMD 2025 |
| Age & sex | % aged 0–14, 65+, 75+, 80+; % male; % under 17 | NHS Digital *Patients Registered at a GP Practice*, 5-year bands, July 2026 |
| Comorbidity | 22 QOF registers: AF, asthma, cancer, CHD, CKD, COPD, dementia, depression, diabetes, epilepsy, heart failure, hypertension, learning disability, serious mental illness, NDH, obesity, osteoporosis, palliative care, PAD, rheumatoid arthritis, stroke/TIA; type 2 diabetes (NDA) | QOF 2024/25 via Fingertips GP profile; National Diabetes Audit 2024-25 |
| Smoking | Current smokers aged 15+ | QOF 2024/25 (Fingertips 91280) |
| Workforce | Qualified GP, all-GP, nurse, direct patient care, admin and reception/telephonist FTE per 10,000 patients; patients per qualified GP; partner, locum and GP-in-training shares of GP FTE | NHS Digital GP workforce, practice-level detailed file, 30 November 2025 |
| Practice | Registered list size | December 2025 (also the rate denominator) |

The workforce figures exclude staff employed by PCNs under the ARRS scheme.

## Definitions

- **Same-day**: `TIME_BETWEEN_BOOK_AND_APPT = "Same Day"`.
- **Urgent-type**: national categories *General Consultation Acute*, *Clinical Triage*, *Unplanned Clinical Activity* and *Walk-in*.
- All appointment statuses are counted.
- **Data-quality exclusions** (default on, can be switched off) remove practices with:
  - a list size under 1,000
  - more than 2,000 or fewer than 100 appointments per 1,000 patients a month
  - more than 40% of appointments in unmapped categories

## Fetching the public source files

`scripts/fetch_nhs_data.py` runs in GitHub Actions (`.github/workflows/fetch-nhs-data.yml`). It runs on a manual trigger, and automatically when the script changes. It commits slim practice-level CSVs to `data/sources/`, which `build_data.py` picks up.

- digital.nhs.uk puts automated clients behind a bot challenge. The script therefore reads the latest **archived** publication pages from the Internet Archive and downloads the `files.digital.nhs.uk` files they link to: GP workforce and registered patients by age.
- Fingertips supplies QOF prevalence, smoking and fallback age bands.

The newest workforce month in the archive was November 2025 when this was built. Re-run the workflow to pick up later months as they get archived.

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
- **Deprivation barely moves same-day access**:
  - the median rises from about 188 in the least-deprived quintile to 207 in the most-deprived (ρ ≈ 0.06)
  - urgent same-day access is flat at about 104 per 1,000 in both
- Smoking, age and disease burden also show weak links (|ρ| < 0.2).
- **GP staffing shows the clearest link**:
  - Same-day **GP** appointments rise from a median of 98 to 129 per 1,000 a month (+32%) from the lowest to the highest fifth of qualified GPs per 10,000 patients.
  - After adjusting for deprivation, age, sex, smoking and the QOF registers, 1 SD more qualified GP FTE (about 1.8 per 10,000) means about 13 more same-day GP appointments per 1,000 a month.
  - Nurse, admin and reception staffing show little or no link with same-day GP access.
- More deprived practices have fewer qualified GPs per patient (ρ ≈ −0.17), which partly offsets their higher need.
- Population and staffing factors together explain only about 6% of between-practice variation. Most of the variation is practice-level: how each practice organises and records access.
