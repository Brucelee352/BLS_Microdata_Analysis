"""
#----------------------------------------------------------------#

BLS CPS Underemployment ETL Pipeline v1.0

Pools the 2026 CPS Basic Monthly public-use microdata files listed in
MONTHLY_FILES, cleans and renames them to analyst-friendly snake_case,
computes the official and alternative labor-underutilization measures
(U-3..U-6, involuntary part-time hours gap, occupational mismatch) per state
and nationally, and loads both the person-level records and the aggregated
state measures into DuckDB.

Run from the project (or worktree) root:

    python scripts/bls_cps_pipeline.py

Measures are NOT seasonally adjusted and pooled across all loaded months --
do not read trend or make seasonal claims from them. Levels (worker counts,
hours) are average monthly levels, not sums across months.

#----------------------------------------------------------------#
"""

# Standard library imports
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime as dt

# Third-party imports
import duckdb
import pandas as pd

try:
    from cps_underemp import person_contributions, summarize
except ImportError:  # imported as scripts.bls_cps_pipeline
    from scripts.cps_underemp import person_contributions, summarize

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional
    load_dotenv = None


# ---------------------------------------------------------------------------
# Paths and logging
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent


def _find_data_root(start: Path) -> Path:
    """Locate the project root that actually holds the raw CPS data.

    The script may run inside a git worktree (.claude/worktrees/<name>) whose
    own data/ dir is empty, while the real CSVs live at the main checkout root.
    Walk up from the script until a populated data/ dir is found; otherwise
    fall back to the immediate parent of scripts/.
    """
    needed = "jan26pub.csv"  # first of MONTHLY_FILES; defined below
    for parent in start.parents:
        if (parent / "data" / needed).exists():
            return parent
    return start.parent


PROJECT_ROOT = _find_data_root(SCRIPT_DIR)
DATA_DIR = PROJECT_ROOT / "data"
DB_DIR = PROJECT_ROOT / "duckdb"
DB_PATH = DB_DIR / "bls_cps.duckdb"
LOG_DIR = PROJECT_ROOT / "logs"
STATE_CSV_PATH = DATA_DIR / "state_measures.csv"

LOG_DIR.mkdir(parents=True, exist_ok=True)

if load_dotenv is not None:
    load_dotenv(dotenv_path=PROJECT_ROOT / "pdp_config.env")

LOG = logging.getLogger("bls_cps_pipeline")
LOG.setLevel(os.getenv("LOG_LEVEL", "INFO"))
logging.basicConfig(
    level=LOG.level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


# ---------------------------------------------------------------------------
# Configuration: files, columns, value maps
# ---------------------------------------------------------------------------
# Source file -> reporting month label.
MONTHLY_FILES = {
    "jan26pub.csv": "2026-01",
    "feb26pub.csv": "2026-02",
    "mar26pub.csv": "2026-03",
    "apr26pub.csv": "2026-04",
    "may26pub.csv": "2026-05",
    "jun26pub.csv": "2026-06",
    "jul26pub.csv": "2026-07",
    "aug26pub.csv": "2026-08",
    "sep26pub.csv": "2026-09",
    "oct26pub.csv": "2026-10",
}

# Uppercase BLS names to keep. Corrections from the data_parser output are
# baked in here: PEHRRSN1 (not PEHRRSN) and PTIO1OCD (not PEIO1OCD).
# PRDISC is BLS's marginally-attached/discouraged recode used for U-4..U-6.
KEEP_COLS = [
    "PWCMPWGT", "HRHHID", "HRHHID2", "PULINENO",
    "PEMLR", "PUWK", "PRUNTYPE", "PRUNEDUR", "PULAY", "PULAY6M",
    "PRWKSTAT", "PEHRUSLT", "PEHRACTT", "PEHRWANT", "PEHRRSN1", "PEMJOT",
    "PEDWWNTO", "PEDWAVL", "PEDWLKWK", "PEDWRSN", "PRDISC",
    "PTIO1OCD", "PEIO1ICD",
    "PRTAGE", "PTDTRACE", "PESEX", "PEMARITL", "PEEDUCA", "PEHSPNON",
    "PRCITSHP", "PENATVTY", "PRFAMNUM",
    "GESTFIPS", "GTCBSA", "HRNUMHOU",
]

# Uppercase BLS name -> snake_case output name. Names from relevant_cols.txt
# are reused; new columns get descriptive snake_case names per the spec.
RENAME_MAP = {
    "PWCMPWGT": "weight",
    "HRHHID": "hhid",
    "HRHHID2": "hhid2",
    "PULINENO": "person_line",
    "PEMLR": "employment_status",
    "PUWK": "worked_lastweek",
    "PRUNTYPE": "unemploy_reason",
    "PRUNEDUR": "unemploy_duration",
    "PULAY": "layoff_status",
    "PULAY6M": "expected_recall_wk",
    "PRWKSTAT": "ft_pt_wkstatus",
    "PEHRUSLT": "usual_hrs",
    "PEHRACTT": "actual_hrs",
    "PEHRWANT": "desire_ft_wk",
    "PEHRRSN1": "reason_pt_wk",
    "PEMJOT": "multi_job_status",
    "PEDWWNTO": "wants_job",
    "PEDWAVL": "available_to_work",
    "PEDWLKWK": "looked_12mo",
    "PEDWRSN": "reason_not_looking",
    "PRDISC": "discouraged_status",
    "PTIO1OCD": "occ_code",
    "PEIO1ICD": "ind_code",
    "PRTAGE": "age",
    "PTDTRACE": "race",
    "PESEX": "sex",
    "PEMARITL": "marital_status",
    "PEEDUCA": "highest_ed_comp",
    "PEHSPNON": "hisp_nonhisp",
    "PRCITSHP": "citizenship_status",
    "PENATVTY": "birth_country",
    "PRFAMNUM": "num_fam_househld",
    "GESTFIPS": "state_fips",
    "GTCBSA": "cbsa",
    "HRNUMHOU": "hh_size",
}

# Human-readable label maps (extracted from bls_microdata_analysis.py), keyed
# by the uppercase BLS name so they can be applied before the rename.
VALUE_MAPS = {
    "PEMLR": {
        1: "Employed - At Work",
        2: "Employed - Absent",
        3: "Unemployed - On Layoff",
        4: "Unemployed - Looking",
        5: "Not in Labor Force - Retired",
        6: "Not in Labor Force - Disabled",
        7: "Not in Labor Force - Other",
    },
    "PUWK": {
        1: "Yes", 2: "No", 3: "Retired", 4: "Disabled", 5: "Unable to Work",
    },
    "PEHRWANT": {
        1: "Yes", 2: "No", 3: "Regular Hours are Full-Time",
    },
    "PEHRRSN1": {
        1: "Slack Work/Business Conditions",
        2: "Could Only Find Part-Time Work",
        3: "Seasonal Work",
        4: "Child Care Problems",
        5: "Other Family/Personal Obligations",
        6: "Health/Medical Limitations",
        7: "School/Training",
        8: "Retired/Social Security Limit on Earnings",
        9: "Full-Time Workweek is Less Than 35 Hrs",
        10: "Other - Specify",
    },
    "PRWKSTAT": {
        1: "Not in Labor Force",
        2: "FT Hours (35+), Usually FT",
        3: "PT for Economic Reasons, Usually FT",
        4: "PT for Non-Economic Reasons, Usually FT",
        5: "Not at Work, Usually FT",
        6: "PT Hrs, Usually PT for Economic Reasons",
        7: "PT Hrs, Usually PT for Non-Economic Reasons",
        8: "FT Hrs, Usually PT for Economic Reasons",
        9: "FT Hrs, Usually PT for Non-Economic",
        10: "Not at Work, Usually Part-Time",
        11: "Unemployed FT",
        12: "Unemployed PT",
    },
    "PRUNTYPE": {
        1: "Job Loser On Layoff",
        2: "Other Job Loser",
        3: "Temporary Job Ended",
        4: "Job Leaver",
        5: "Re-Entrant",
        6: "New-Entrant",
    },
    "PEMJOT": {1: "Yes", 2: "No"},
    "PRDISC": {
        1: "Discouraged Worker",
        2: "Conditionally Interested",
        3: "Not Available",
    },
    "PULAY": {
        1: "Yes", 2: "No", 3: "Retired", 4: "Disabled", 5: "Unable to Work",
    },
    "PULAY6M": {1: "Yes", 2: "No"},
    "PESEX": {1: "Male", 2: "Female"},
    "PEMARITL": {
        1: "Married - Spouse Present",
        2: "Married - Spouse Absent",
        3: "Widowed",
        4: "Divorced",
        5: "Separated",
        6: "Never Married",
    },
    "PEEDUCA": {
        31: "Less than 1st Grade",
        32: "1st-4th Grade",
        33: "5th or 6th Grade",
        34: "7th or 8th Grade",
        35: "9th Grade",
        36: "10th Grade",
        37: "11th Grade",
        38: "12th Grade No Diploma",
        39: "High School Grad - Diploma or GED",
        40: "Some College, No Degree",
        41: "Associate Degree - Occupational/Vocational",
        42: "Associate Degree - Academic Program",
        43: "Bachelor's Degree (BA, AB, BS)",
        44: "Master's Degree (MA, MS, MEng, MEd, MSW)",
        45: "Professional School Degree (MD, DDS, DVM)",
        46: "Doctorate Degree (PhD, EdD)",
    },
    "PTDTRACE": {
        1: "White Only",
        2: "Black Only",
        3: "American Indian, Alaskan Native Only",
        4: "Asian Only",
        5: "Hawaiian/Pacific Islander Only",
        6: "White-Black",
        7: "White-AI",
        8: "White-Asian",
        9: "White-HP",
        10: "Black-AI",
        11: "Black-Asian",
        12: "Black-HP",
        13: "AI-Asian",
        14: "AI-HP",
        15: "Asian-HP",
        16: "W-B-AI",
        17: "W-B-A",
        18: "W-B-HP",
        19: "W-AI-A",
        20: "W-AI-HP",
        21: "W-A-HP",
        22: "B-AI-A",
        23: "W-B-AI-A",
        24: "W-AI-A-HP",
        25: "Other 3 Race Combinations",
        26: "Other 4 and 5 Race Combinations",
    },
    "PRCITSHP": {
        1: "Native, Born in the United States",
        2: "Native, Born in Puerto Rico or Other U.S. Island Areas",
        3: "Native, Born Abroad of American Parent(s)",
        4: "Foreign Born, U.S. Citizen by Naturalization",
        5: "Foreign Born, Not a Citizen of the United States",
    },
    "PEHSPNON": {1: "Hispanic", 2: "Non-Hispanic"},
    "PENATVTY": {
        57: "United States",
        60: "American Samoa",
        66: "Guam",
        73: "Puerto Rico",
        78: "U.S. Virgin Islands",
        96: "Other U.S. Island Area",
        555: "Elsewhere",
    },
}


# ---------------------------------------------------------------------------
# 1. Load & pool
# ---------------------------------------------------------------------------
def load_pooled() -> pd.DataFrame:
    """Load each monthly CSV (only KEEP_COLS), uppercase columns, tag with
    MONTH, and concatenate into one pooled frame.

    Pooling stabilizes small state cells but blends seasonal factors, so the
    output is a pooled (NSA) snapshot, not a trend.
    """
    keep = set(KEEP_COLS)
    frames = []
    for filename, month in MONTHLY_FILES.items():
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing CPS file: {path}")
        df = pd.read_csv(path, usecols=lambda c: c.upper() in keep)
        df.columns = df.columns.str.upper()
        missing = keep - set(df.columns)
        if missing:
            raise KeyError(f"{filename} missing columns: {sorted(missing)}")
        df = df[KEEP_COLS].assign(MONTH=month)
        frames.append(df)
        LOG.info("Loaded %s (%s rows) as month %s", filename, len(df), month)

    pooled = pd.concat(frames, ignore_index=True)
    LOG.info("Pooled %s monthly files into %s records",
             len(frames), len(pooled))
    return pooled


# ---------------------------------------------------------------------------
# 2. Clean / rename (person-level records for cps_microdata)
# ---------------------------------------------------------------------------
def clean_records(pooled: pd.DataFrame) -> pd.DataFrame:
    """Apply human-readable value maps and rename to snake_case.

    Operates on a copy so the numeric uppercase frame remains available for
    weighted measure computation, which needs the raw codes.
    """
    df = pooled.copy()
    for col, mapping in VALUE_MAPS.items():
        if mapping and col in df.columns:
            df[col] = df[col].replace(mapping)

    df = df.rename(columns={**RENAME_MAP, "MONTH": "month"})
    LOG.info("Cleaned/renamed person-level records: %s rows, %s cols",
             len(df), df.shape[1])
    return df


# ---------------------------------------------------------------------------
# 3. Per-state + national measures (math lives in cps_underemp.py)
# ---------------------------------------------------------------------------
def compute_state_measures(pooled: pd.DataFrame) -> pd.DataFrame:
    """Compute all measures per GESTFIPS plus a national row (state_fips = 0).

    Levels are average monthly levels (weights divided by the number of
    pooled months). Occupation norms for overqualification are national, so
    every state is judged against the same benchmark.
    """
    months = sorted(pooled["MONTH"].unique())
    contrib = person_contributions(pooled, n_months=len(months))

    levels = pd.concat([
        contrib.sum().to_frame(0).T,                 # national
        contrib.groupby(pooled["GESTFIPS"]).sum(),   # states
    ])
    out = summarize(levels).round({
        "u3": 2, "u4": 2, "u5": 2, "u6": 2, "total_hrs_gap": 0,
        "avg_hrs_gap": 1, "overqualification_rate": 2,
    })
    out = out.rename_axis("state_fips").reset_index()
    out.insert(1, "month_range", f"{months[0]} to {months[-1]}")
    LOG.info("Computed measures for %s states + 1 national row", len(out) - 1)
    return out


# ---------------------------------------------------------------------------
# 4. DuckDB load
# ---------------------------------------------------------------------------
def make_table(df: pd.DataFrame, table: str,
               con: duckdb.DuckDBPyConnection) -> None:
    """Create (replace) a DuckDB table from a DataFrame."""
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.register("temp_df", df)
    con.execute(f"CREATE TABLE {table} AS SELECT * FROM temp_df")
    con.unregister("temp_df")
    count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    LOG.info("Created %s table with %s records", table, count)


def load_duckdb(records: pd.DataFrame, measures: pd.DataFrame) -> None:
    """Load the person-level records and aggregated measures into DuckDB."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        make_table(records, "cps_microdata", con)
        make_table(measures, "state_measures", con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 5. Output
# ---------------------------------------------------------------------------
def report_national(measures: pd.DataFrame) -> None:
    """Print the national pooled summary (state_fips = 0)."""
    nat = measures.loc[measures["state_fips"] == 0].iloc[0]
    print("\n" + "=" * 60)
    print("NATIONAL POOLED CPS UNDEREMPLOYMENT SUMMARY (NSA)")
    print(f"Period: {nat['month_range']}")
    print("=" * 60)
    print(f"U-3 (official unemployment):   {nat['u3']:.2f}%")
    print(f"U-4 / U-5:                     {nat['u4']:.2f}% / {nat['u5']:.2f}%")
    print(f"U-6 (broad underutilization):  {nat['u6']:.2f}%")
    print(f"Involuntary PT hours gap:      {nat['avg_hrs_gap']:.1f} hrs/wk "
          f"across {nat['affected_workers']:,.0f} workers "
          f"({nat['total_hrs_gap']:,.0f} weekly hrs short, monthly avg)")
    print(f"Overqualification rate:        "
          f"{nat['overqualification_rate']:.2f}% "
          f"({nat['overqualified_workers']:,.0f} of "
          f"{nat['employed_in_scope']:,.0f} employed in scope)")
    print("=" * 60 + "\n")


def save_state_csv(measures: pd.DataFrame) -> Path:
    """Save the state_measures table to CSV."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    measures.to_csv(STATE_CSV_PATH, index=False)
    LOG.info("Saved state measures CSV to %s", STATE_CSV_PATH)
    return STATE_CSV_PATH


# ---------------------------------------------------------------------------
# 6. Orchestration
# ---------------------------------------------------------------------------
def main() -> None:
    """Orchestrate the full ETL pipeline with timing."""
    start = time.perf_counter()
    try:
        LOG.info("Pipeline started at %s",
                 dt.now().strftime("%Y-%m-%d %H:%M:%S"))

        pooled = load_pooled()
        records = clean_records(pooled)
        measures = compute_state_measures(pooled)

        load_duckdb(records, measures)
        report_national(measures)
        save_state_csv(measures)

        elapsed = time.perf_counter() - start
        LOG.info("Pipeline completed successfully in %.2fs", elapsed)
        print(f"Pipeline completed successfully in {elapsed:.2f}s")

    except (FileNotFoundError, KeyError, ValueError, duckdb.Error) as e:
        LOG.error("Pipeline failed: %s", str(e))
        sys.exit(1)
    finally:
        LOG.info("Pipeline run ended at %s",
                 dt.now().strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    main()
