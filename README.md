# BLS Microdata Analysis

An interactive dashboard of state-level underemployment, built from the Current Population Survey (CPS) Basic Monthly public-use microdata that the Census Bureau collects for the Bureau of Labor Statistics (BLS). For every state and the nation it reports the unemployment rate (U-3), the broader BLS alternatives U-4 through U-6, how many hours involuntary part-timers fall short of full time, and how often workers hold more education than their occupation typically requires. All figures pool January through October 2026.

## Key findings (pooled Jan–Oct 2026, not seasonally adjusted)

| Measure | National |
|---|---|
| U-3 (official unemployment) | 4.28% |
| U-6 (broadest underutilization) | 7.95% |
| Involuntary part-time hours gap | 12.3 hours/week per worker |
| Overqualification rate | 14.95% |

U-6 runs about 3.7 points above U-3. Most of that gap (2.7 points) is people working part time because they can't get full-time hours. The rest is people who want a job and could start one but have stopped looking, including discouraged workers who believe no work is available for them.

About 4.7 million people a month worked part time for economic reasons. On average they were 12.3 hours short of a 35-hour week, which adds up to roughly 57.6 million hours of wanted work each week.

Roughly one in seven employed workers has noticeably more schooling than is typical for their occupation. No single unemployment rate captures this kind of mismatch.

## Validation against BLS published figures

The national estimates were checked against BLS Table A-15 (not seasonally adjusted), pulled from the BLS Public Data API v2. The BLS column is the simple average of the ten monthly published values.

| Measure | This project (CPS pooled) | BLS published avg, Jan–Oct 2026 | Gap |
|---|---|---|---|
| U-3 | 4.28% | 4.28% | 0.00 pp |
| U-4 | 4.54% | 4.54% | 0.00 pp |
| U-5 | 5.23% | 5.24% | −0.01 pp |
| U-6 | 7.95% | 7.96% | −0.01 pp |

Single months also match BLS to its published rounding. May 2026, for example, comes out at 4.07 / 4.32 / 4.98 / 7.67 against published values of 4.1 / 4.3 / 5.0 / 7.7. The pooled rate weights each month by the size of its labor force, so it can differ by a hundredth of a point from a simple average of monthly rates.

The overqualification rate and hours gap have no BLS equivalent and are not validated externally.

## How each measure is calculated

All counts use the CPS composite final weight, `PWCMPWGT`, divided by 10,000 because the field carries four implied decimals. Each month's weights already sum to the full U.S. population, so the pipeline also divides by the number of months pooled. Worker and hour counts are therefore average monthly levels, not totals across ten months. Rates are unaffected by this step.

### U-3 to U-6

Labor force status comes from `PEMLR`. Codes 1–2 are employed, 3–4 unemployed and 5–7 not in the labor force.

Marginally attached workers come from `PRDISC`, the recode BLS uses to build U-4 and U-5. It covers people outside the labor force who want a job, were available to start one, and looked for work in the past 12 months but not the past 4 weeks. Code 1 marks discouraged workers, who stopped looking because they believe no work is available. Code 2 marks the rest of the marginally attached.

Part time for economic reasons (PTER) uses `PRWKSTAT` codes 3 and 6. These are people who worked 1–34 hours in the reference week because of slack business or because they could only find part-time work. The group includes both usual full-timers whose hours were cut and usual part-timers who want full-time work.

With U = unemployed, LF = labor force, D = discouraged, MA = all marginally attached and PT = PTER:

| Measure | Formula |
|---|---|
| U-3 | U ÷ LF |
| U-4 | (U + D) ÷ (LF + D) |
| U-5 | (U + MA) ÷ (LF + MA) |
| U-6 | (U + MA + PT) ÷ (LF + MA) |

### Involuntary hours gap

For each PTER worker the gap is 35 minus the hours they actually worked in the reference week (`PEHRACTT`). Actual hours are the figure that puts someone in PTER in the first place. The pipeline reports the weighted total gap per week and the average gap per affected worker.

### Overqualification (realized-matches method)

1. Take employed workers with a valid occupation code (`PTIO1OCD`) and education code (`PEEDUCA`).
2. Convert `PEEDUCA` to approximate years of schooling. For example, a high school diploma is 12 years, a bachelor's degree 16, a master's 18, and a professional degree or doctorate 20. The raw codes 31–46 are ordinal, and one code step can mean one grade or two years of graduate school, so averaging them directly would distort the result.
3. For each occupation, compute the weighted mean and standard deviation of years of schooling across the whole national pool. Skip occupations with fewer than 30 sampled workers. Because the norms are national, every state is measured against the same benchmark.
4. Flag a worker as overqualified when their schooling exceeds their occupation's mean by more than one standard deviation.
5. The overqualification rate is the weighted count of flagged workers divided by the weighted count of workers in scope.

The method needs no external "required education" table or O\*NET crosswalk. It measures how far a worker sits from what is typical in their occupation, so read it as a relative signal, not a skills assessment. BLS publishes no comparable estimate, and the calculation is specific to this project.

## Data source

The data are the BLS / Census CPS Basic Monthly public-use files, `jan26pub.csv` through `oct26pub.csv`: ten files with 1,214,584 person records in total. Figures are not seasonally adjusted. Pooling ten months keeps small states' samples large enough to use, but it mixes seasonal patterns, so the figures say nothing about trends or seasonal movement.

## Project structure

```
.
├── data/                     # not committed
│   ├── jan26pub.csv … oct26pub.csv   # raw CPS microdata, one file per month
│   ├── bls_a15.csv           # BLS published U-1..U-6 (from bls_api_pull.py)
│   ├── bls_analysis.csv      # cross-check output (from bls_crosscheck.py)
│   └── state_measures.csv    # pipeline output (51 states + national row)
├── duckdb/
│   └── bls_cps.duckdb        # pipeline output; shipped in the Docker image
├── scripts/
│   ├── bls_cps_pipeline.py   # ETL: load -> clean -> measure -> DuckDB
│   ├── cps_underemp.py       # measure math (U-3..U-6, hours gap, overqualification)
│   ├── bls_api_pull.py       # pulls BLS Table A-15 series and validates estimates
│   ├── bls_crosscheck.py     # re-pulls BLS data and diffs it against the cached CSV
│   └── dashboard.py          # Plotly Dash dashboard
├── Dockerfile                # serves the dashboard with gunicorn
├── data_dictionary.md        # CPS public-use record layout
├── cps_variable_list.txt     # annotated variable extract
├── pyproject.toml
└── uv.lock
```

## Setup and running

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync

# Run the ETL pipeline (needs the raw CPS files in data/)
uv run python scripts/bls_cps_pipeline.py

# Pull BLS published figures and compare (needs a free BLS API key)
export BLS_API_KEY="your_key"          # PowerShell: $env:BLS_API_KEY = "your_key"
uv run python scripts/bls_api_pull.py

# Launch the dashboard
uv run python scripts/dashboard.py
# Opens at http://localhost:8051
```

Download each month's public-use file from the Census Bureau's CPS Basic Monthly page and save it in `data/` under the filenames above. To add a month, add the file to `MONTHLY_FILES` in `bls_cps_pipeline.py`. Then update `BLS_NSA_U3_AVG` and `BLS_NSA_U6_AVG` in `dashboard.py` to the new averages of the published NSA rates.

## Dashboard features

- A choropleth map that switches between U-3, U-6 and overqualification. Click a state to select it, or pick one from the dropdown.
- KPI cards comparing the selected state with the national figure for U-3, U-6, the average hours gap and overqualification.
- A grouped bar chart of the selected state against the nation, with BLS published averages marked as reference lines.
- A gauge of the state's U-6 against the national rate, shaded green below the national figure and red above it.
- A bubble chart of U-3 against the overqualification rate for every state. Bubbles grow with the overqualification rate and darken as U-3 rises.
- A ranking of states by the metric shown on the map.
- A "Technical overview" button that opens a scrollable window describing the data sources, the pipeline and the math.

## Technical notes

- Column names in the 2026 CPS files differ from older documentation. The pipeline uses `PTIO1OCD` (not `PEIO1OCD`) and `PEHRRSN1` (not `PEHRRSN`).
- `_find_data_root()` walks up parent directories to find a populated `data/` folder, so the pipeline runs from either the main checkout or a git worktree.
- DuckDB stores the person-level records (`cps_microdata`, about 1.2 million rows) and the aggregated state measures (`state_measures`, 52 rows). The dashboard reads only `state_measures`.

## Caveats

- This is a snapshot of the ten-month period and hides movement within it.
- The overqualification measure is a statistical heuristic, not an assessment of skills.
- Small states have wide sampling error on every measure.
- The dashboard hides a state's overqualification rate when its weighted employment in scope is below 100,000.

## License

The project is built on BLS public-use microdata, which has no redistribution restrictions. See the [BLS Data Policy](https://www.bls.gov/bls/linksite.htm) for terms. The repository is not bound by a license for reproduction purposes and is closed source.
