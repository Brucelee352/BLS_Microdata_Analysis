# -*- coding: utf-8 -*-
"""
BLS Public Data API v2 pull: Table A-15 Alternative Measures of Labor
Underutilization (U-1 through U-6), both seasonally adjusted (SA) and
not seasonally adjusted (NSA).

Usage:
    export BLS_API_KEY="your_key_here"       # register free at data.bls.gov/registrationEngine/
    python scripts/bls_api_pull.py

Without BLS_API_KEY set, the script falls back to the unauthenticated v1
endpoint (3 series/request, 25 req/day) with a rate-limit warning.

Output:
    data/bls_a15.csv    -- tidy DataFrame: measure, month, sa, nsa
    prints full series ID map + validation table against CPS microdata estimates
"""

import os
import sys
import time
import logging
from pathlib import Path
from datetime import date

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOG = logging.getLogger("bls_api_pull")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent

def _find_project_root(start: Path) -> Path:
    for parent in [start.parent] + list(start.parents):
        if (parent / "data").exists():
            return parent
    return start.parent

PROJECT_ROOT = _find_project_root(SCRIPT_DIR)
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = DATA_DIR / "bls_a15.csv"


# ---------------------------------------------------------------------------
# Series ID map  (all verified against BLS API live data)
#
# Anchors (BLS-confirmed): U-3 SA LNS14000000, U-4 SA LNS13327707,
#                           U-6 SA LNS13327709
# NSA pattern: replace "LNS1" with "LNU0" (S->U, 1->0 in chars 3-4)
#
# U-1/U-2 NOTE: LNS13023621 / LNS13023569 are LEVEL series (thousands).
# Rate series use position-4 digit = 4: LNS14023621 / LNS14023569.
# Verified live: U-1 SA ~2.0%, U-1 NSA 1.8-2.4%; U-2 SA/NSA ~0.4-0.5%.
# ---------------------------------------------------------------------------
SERIES_MAP = {
    "U1": {
        "label": "Persons unemployed 15+ weeks",
        "sa":  "LNS14023621",   # rate (% CLF); NOT LNS13023621 which is the level
        "nsa": "LNU04023621",
    },
    "U2": {
        "label": "Job losers + completed temporary jobs",
        "sa":  "LNS14023569",   # rate (% CLF); NOT LNS13023569 which is the level
        "nsa": "LNU04023569",
    },
    "U3": {
        "label": "Official unemployment rate",
        "sa":  "LNS14000000",   # BLS anchor
        "nsa": "LNU04000000",
    },
    "U4": {
        "label": "U-3 + discouraged workers",
        "sa":  "LNS13327707",   # BLS anchor
        "nsa": "LNU03327707",
    },
    "U5": {
        "label": "U-4 + all marginally attached",
        "sa":  "LNS13327708",
        "nsa": "LNU03327708",
    },
    "U6": {
        "label": "U-5 + part-time for economic reasons",
        "sa":  "LNS13327709",   # BLS anchor
        "nsa": "LNU03327709",
    },
}

# My CPS-microdata estimates (NSA, Jan-May 2026 pooled) for validation
MY_ESTIMATES = {
    "U3": 4.36,
    "U6": 7.44,
}

FLAG_THRESHOLD = 0.3  # percentage points


# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("BLS_API_KEY", "").strip()
USE_V2 = bool(API_KEY)

BLS_V2_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_V1_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"

# v1 unauthenticated limit: 3 series per request
V1_BATCH_SIZE = 3
# v2 authenticated limit: 50 series per request
V2_BATCH_SIZE = 50

START_YEAR = "2026"
END_YEAR   = str(date.today().year)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def print_series_map() -> None:
    """Print the full resolved series ID map for user verification."""
    print("\n" + "=" * 72)
    print("RESOLVED SERIES ID MAP — Table A-15 Alternative Measures")
    print("=" * 72)
    print(f"{'Measure':<6}  {'SA Series ID':<16}  {'NSA Series ID':<16}  Label")
    print("-" * 72)
    for m, info in SERIES_MAP.items():
        print(f"{m:<6}  {info['sa']:<16}  {info['nsa']:<16}  {info['label']}")
    print("=" * 72)
    if USE_V2:
        print(f"API mode: v2 (authenticated, key ending ...{API_KEY[-4:]})")
    else:
        print("API mode: v1 (unauthenticated fallback - 25 req/day, 3 series/req)")
        print("  Set BLS_API_KEY env var to use v2 (500 req/day, 50 series/req)")
    print()


def _period_to_date(year: str, period: str) -> date | None:
    """Convert BLS period code 'M01'..'M12' to a date (first of month)."""
    if not period.startswith("M") or period == "M13":
        return None  # M13 = annual average; skip
    month = int(period[1:])
    return date(int(year), month, 1)


def _fetch_batch(series_ids: list[str], start: str, end: str) -> dict:
    """POST a batch of series to the BLS API. Returns parsed JSON."""
    payload = {
        "seriesid": series_ids,
        "startyear": start,
        "endyear": end,
        "calculations": False,
        "annualaverage": False,
    }
    if USE_V2:
        payload["registrationkey"] = API_KEY
        url = BLS_V2_URL
    else:
        url = BLS_V1_URL

    LOG.info("POST %s series %s", url, series_ids)
    resp = requests.post(url, json=payload, timeout=30)

    if resp.status_code == 429:
        LOG.error("BLS rate limit hit (HTTP 429). Wait until tomorrow or supply BLS_API_KEY.")
        sys.exit(1)
    resp.raise_for_status()

    data = resp.json()
    status = data.get("status", "UNKNOWN")
    if status != "REQUEST_SUCCEEDED":
        msgs = data.get("message", [])
        LOG.error("BLS API error (status=%s): %s", status, msgs)
        sys.exit(1)

    return data


def fetch_all_series(series_ids: list[str]) -> list[dict]:
    """Fetch all series in batches, respecting v1/v2 limits."""
    batch_size = V2_BATCH_SIZE if USE_V2 else V1_BATCH_SIZE
    all_series = []
    for i in range(0, len(series_ids), batch_size):
        batch = series_ids[i : i + batch_size]
        data = _fetch_batch(batch, START_YEAR, END_YEAR)
        all_series.extend(data["Results"]["series"])
        if i + batch_size < len(series_ids):
            time.sleep(0.5)  # be a polite API citizen
    return all_series


# ---------------------------------------------------------------------------
# Parse BLS JSON -> flat records
# ---------------------------------------------------------------------------
def parse_series(raw_series: list[dict]) -> pd.DataFrame:
    """Flatten raw BLS series JSON to tidy rows: series_id, date, value."""
    rows = []
    for s in raw_series:
        sid = s.get("seriesID", "")
        for obs in s.get("data", []):
            d = _period_to_date(obs["year"], obs["period"])
            if d is None:
                continue
            val_str = obs.get("value", "")
            try:
                val = float(val_str)
            except (ValueError, TypeError):
                val = float("nan")
            rows.append({"series_id": sid, "month": d, "value": val})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Assemble tidy A-15 DataFrame
# ---------------------------------------------------------------------------
def build_tidy(raw_series: list[dict]) -> pd.DataFrame:
    """Return tidy DataFrame: measure, month, sa, nsa."""
    flat = parse_series(raw_series)
    if flat.empty:
        LOG.error("No data parsed from BLS response.")
        sys.exit(1)

    # Build reverse lookup: series_id -> (measure, "sa"/"nsa")
    id_to_key: dict[str, tuple[str, str]] = {}
    for measure, info in SERIES_MAP.items():
        id_to_key[info["sa"]]  = (measure, "sa")
        id_to_key[info["nsa"]] = (measure, "nsa")

    flat["measure"] = flat["series_id"].map(lambda x: id_to_key.get(x, (None, None))[0])
    flat["adj"]     = flat["series_id"].map(lambda x: id_to_key.get(x, (None, None))[1])
    flat = flat.dropna(subset=["measure", "adj"])

    pivot = flat.pivot_table(
        index=["measure", "month"], columns="adj", values="value", aggfunc="first"
    ).reset_index()
    pivot.columns.name = None

    # Ensure both SA and NSA columns exist even if one variant had no data
    for col in ("sa", "nsa"):
        if col not in pivot.columns:
            pivot[col] = float("nan")

    pivot = pivot.sort_values(["measure", "month"]).reset_index(drop=True)
    LOG.info("Assembled tidy table: %s rows, measures=%s",
             len(pivot), sorted(pivot["measure"].unique()))
    return pivot


# ---------------------------------------------------------------------------
# Validation: my microdata estimates vs BLS published NSA
# ---------------------------------------------------------------------------
def print_validation(tidy: pd.DataFrame) -> None:
    """Compare CPS microdata pooled estimates against BLS published NSA."""
    print("\n" + "=" * 72)
    print("VALIDATION: CPS Microdata Estimates vs BLS Published NSA")
    print("Period covered by my estimates: Jan-May 2026 (pooled, NSA)")
    print("=" * 72)
    print(f"{'Month':<12} {'Measure':<8} {'My NSA':>8} {'BLS NSA':>9} {'Gap':>8}  {'Flag'}")
    print("-" * 72)

    for measure, my_val in MY_ESTIMATES.items():
        subset = tidy[
            (tidy["measure"] == measure) &
            (tidy["month"] >= date(2026, 1, 1)) &
            (tidy["month"] <= date(2026, 5, 31))
        ].sort_values("month")

        if subset.empty:
            print(f"{'(no data)':<12} {measure:<8} {my_val:>8.2f}%  {'N/A':>8}  {'N/A':>8}")
            continue

        for _, row in subset.iterrows():
            bls_nsa = row["nsa"]
            if pd.isna(bls_nsa):
                flag = "no BLS value"
                gap_str = "N/A"
            else:
                gap = my_val - bls_nsa
                gap_str = f"{gap:+.2f}pp"
                flag = "*** FLAG ***" if abs(gap) > FLAG_THRESHOLD else "OK"
            month_str = row["month"].strftime("%Y-%m")
            print(f"{month_str:<12} {measure:<8} {my_val:>8.2f}%  {bls_nsa:>8.2f}%  {gap_str:>8}  {flag}")

    print("=" * 72)
    print(f"Flag threshold: +/-{FLAG_THRESHOLD} percentage points")
    print("Note: my estimates are pooled Jan-May 2026 NSA; BLS values are monthly NSA.")
    print("      Pool-vs-point differences are expected; gaps > 0.3pp warrant review.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print_series_map()

    # Collect all 12 series IDs in a stable order
    sa_ids  = [info["sa"]  for info in SERIES_MAP.values()]
    nsa_ids = [info["nsa"] for info in SERIES_MAP.values()]
    all_ids = sa_ids + nsa_ids

    LOG.info("Fetching %s series from BLS API (years %s-%s)",
             len(all_ids), START_YEAR, END_YEAR)

    raw = fetch_all_series(all_ids)
    tidy = build_tidy(raw)

    # Echo source, series ID, and SA/NSA flag for every value pulled
    print("\n" + "=" * 72)
    print("RAW PULL ECHO (source / series_id / adjustment / month / value)")
    print("=" * 72)
    id_label = {
        info["sa"]:  f"{m} SA  {info['label']}"
        for m, info in SERIES_MAP.items()
    }
    id_label.update({
        info["nsa"]: f"{m} NSA {info['label']}"
        for m, info in SERIES_MAP.items()
    })

    flat_echo = parse_series(raw)
    flat_echo = flat_echo.sort_values(["series_id", "month"])
    for _, row in flat_echo.iterrows():
        sid   = row["series_id"]
        label = id_label.get(sid, sid)
        month = row["month"].strftime("%Y-%m") if pd.notna(row["month"]) else "N/A"
        val   = f"{row['value']:.2f}" if pd.notna(row["value"]) else "NaN"
        src   = "BLS Public API v2" if USE_V2 else "BLS Public API v1"
        print(f"  {src}  |  {sid}  |  {label:<40}  |  {month}  |  {val}%")

    tidy.to_csv(OUTPUT_CSV, index=False)
    LOG.info("Saved tidy A-15 data to %s (%s rows)", OUTPUT_CSV, len(tidy))

    print(f"\nTidy DataFrame preview (head):\n{tidy.to_string(index=False)}\n")

    print_validation(tidy)

    print(f"Output written to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
