# BLS Microdata Analysis 

An interactive state-level underemployment dashboard built from Bureau of Labor Statistics (BLS) Current Population Survey (CPS) Basic Monthly public-use microdata. The dashboard surfaces four labor underutilization measures — U-3, U-6, the involuntary hours gap, and occupational mismatch, for every U.S. state, pooled across January through May 2026.

---

## Key Findings (Pooled Jan–May 2026, Not Seasonally Adjusted)

| Measure | National |
|---|---|
| U-3 (official unemployment) | 4.36% |
| U-6 (broad underutilization) | 7.44% |
| Overqualification rate | 15.01% |

The ~3-percentage-point spread between U-3 and U-6 reveals the population that the headline figure leaves unnamed: workers employed fewer hours than they need, and those who have **quietly stopped searching for doors they have learned will not open.** 

*One in six employed workers carries credentials that exceed what their current role demands, a clear structural signal of misallocated human capital that no single unemployment rate conveys.*

## Validation Against BLS Published NSA Data

These CPS microdata estimates were cross-validated against the Bureau of Labor Statistics' official published Table A-15 figures (not seasonally adjusted), retrieved via the BLS Public Data API v2.

| Measure | This Project (CPS pooled NSA) | BLS Published NSA Avg (Jan-May 2026) | Gap |
|---|---|---|---|
| U-3 | 4.36% | 4.36% | 0.00pp — exact match |
| U-6 | 7.44% | 8.10% | -0.66pp — disclosed gap |

**U-3** is fully validated. The pooled estimate equals the arithmetic mean of BLS's five monthly NSA values (4.70, 4.70, 4.30, 4.00, 4.10), confirming that the CPS weighting (`PWCMPWGT / 10,000`) and labor force classification logic are correctly implemented.

**U-6** shows a persistent 0.66pp undercount relative to BLS. 

This gap is a known, disclosed methodology boundary: the marginal-attachment filter used here (`PEDWWNTO=1`, `PEDWAVL=1`, `PEDWLKWK ∈ {1,2}`) captures a narrower population than BLS's full U-6 numerator, which incorporates additional classification logic in the published estimates. 

The measure ladder from the BLS data (Jan-May NSA avg) shows: U-6 = U-3 (4.36%) + discouraged workers (+0.26pp) + other marginally attached (+0.70pp) + involuntary part-time (+2.78pp) = 8.10%. 

The 0.66pp gap falls primarily in the marginally-attached component. This does not affect U-3 or the overqualification rate.

## Measures Defined

**U-3** — The official unemployment rate. Counts those without a job, available to work, and who actively searched for employment in the prior four weeks.

**U-6** — The BLS's broadest alternative measure. Extends U-3 to include marginally attached workers (those who want work, are available, and searched in the prior 12 months but not the prior 4 weeks) and workers employed part-time for economic reasons (involuntary part-time).

**Involuntary Hours Gap** — The weekly hours shortfall among workers employed part-time for economic reasons who want full-time work. Reported as average hours short per affected worker and as aggregate weekly hours lost across the cohort.

**Occupational Mismatch (Overqualification)** — Measures the share of employed workers whose education outpaces the typical requirement of their occupation, using the **realized-matches method**:

1. Restrict to employed workers (PEMLR ∈ {1, 2}) with a valid occupation code (PTIO1OCD) and education code (PEEDUCA, ordinal scale 31–46).
2. Group workers by occupation. Exclude any occupation with fewer than 30 observations (small-cell suppression).
3. Within each qualifying occupation, compute the population-weighted mean and standard deviation of educational attainment.
4. Flag a worker as overqualified if their PEEDUCA exceeds (occupation mean + 1.0 × occupation SD).
5. Apply CPS composite weights (PWCMPWGT ÷ 10,000). 
6. **Overqualification rate = weighted overqualified count ÷ weighted employed-in-scope count.**

This is a relative, data-driven heuristic, no external "required education" table or O\*NET crosswalk is needed, and no subjective threshold is imposed. 

The result reflects statistical deviation from occupation norms as observed in the data itself. 

Because PEEDUCA is an ordinal code rather than continuous years of schooling, the measure should be read as a rank-based signal, not a precise credential gap. 

*The overqualification rate is not cross-validated against BLS published figures, (BLS does not publish a comparable national estimate) and is a properitary calculation.*

## Data Source

Bureau of Labor Statistics, Current Population Survey (CPS) Basic Monthly Public-Use Microdata
Files: `jan26pub.csv`, `feb26pub.csv`, `mar26pub.csv`, `apr26pub.csv`, `may26pub.csv`
Composite final weight: `PWCMPWGT` (4 implied decimals — divide by 10,000)

Figures are **not seasonally adjusted** and are **pooled across five months**. 

Pooling stabilizes small state-level cells but blends seasonal factors, do not read trend or make seasonal claims from these figures.

--- 

## Project Structure

```
.
├── data/
│   ├── jan26pub.csv          # Raw CPS microdata (not committed — download from BLS)
│   ├── feb26pub.csv
│   ├── mar26pub.csv
│   ├── apr26pub.csv
    ├── may26pub.csv
│   └── state_measures.csv    # Pipeline output (52 rows: 51 states + national)
├── duckdb/
│   └── bls_cps.duckdb        # Analytical database (not committed — generated by pipeline)
├── scripts/
│   ├── bls_cps_pipeline.py   # ETL: load -> clean -> measure -> DuckDB
│   ├── cps_underemp.py       # Core measure functions (U-3, U-6, hours gap, mismatch)
│   └── dashboard.py          # Plotly Dash interactive dashboard
├── .claude/agents/           # Agent definitions for this pipeline
├── cps_variable_list.txt     # Annotated variable extract specification
├── pyproject.toml
└── uv.lock
```

## Setup and Running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync

# Run the ETL pipeline (requires raw CPS files placed in data/)
uv run python scripts/bls_cps_pipeline.py

# Launch the interactive dashboard
uv run python scripts/dashboard.py
# Opens at http://localhost:8050
```

Raw CPS microdata files are available from the BLS CPS Basic Monthly Data page. Download the public-use file for each month and place in `data/` using the filenames above.

## Dashboard Features

- Choropleth map with metric toggle (U-3 / U-6 / Overqualification)
- KPI cards showing selected state vs. national benchmarks:
    - Bar chart of all four measures for the selected state
    - Bullet gauge displaying hours-gap intensity
    - Scatter plot of U-3 vs. U-6 across all states
    - State rankings chart sorted by the active map metric

Click any state on the map to drill into that state's measures. Use the state dropdown for direct selection.

## Technical Notes

- Column name corrections for 2026 CPS files baked into pipeline: `PEIO1OCD` -> `PTIO1OCD`, `PEHRRSN` -> `PEHRRSN1`
- `_find_data_root()` walks parent directories to locate populated `data/` folder, supporting both main checkout and git worktree execution paths
- DuckDB stores both the person-level microdata (`cps_microdata`, ~486K rows) and the pre-aggregated state measures (`state_measures`, 52 rows) — the dashboard reads only the latter

## Caveats

- Five-month pooling stabilizes state-level cells but masks within-period movement; this is a current-snapshot tool, not a trend explorer
- The overqualification measure is a **statistical rank-based heuristic** — not an authoritative skills assessment
- Small states may exhibit wide effective confidence intervals on any given measure

## License

Built on BLS public-use microdata, which carries no redistribution restrictions. See the [BLS Data Policy](https://www.bls.gov/bls/linksite.htm) for terms. Repo is not bound to licensure for reproduction purposes and is closed-source. 
