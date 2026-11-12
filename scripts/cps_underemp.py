"""
Underemployment measures from CPS Basic Monthly public-use microdata (BLS).

Computes, in one vectorized pass:
  1. U-3..U-6 -- official unemployment rate and BLS alternative measures
  2. Hours gap -- hours short of full time (35) among involuntary part-timers
  3. Occupational mismatch -- overqualification (education vs. occupation norm)

Design: `person_contributions` turns each record into weighted counts
(weight x indicator), so any group's levels are a plain column sum and
`summarize` turns summed levels into rates. That makes national and
per-state figures one `groupby().sum()` with no per-group re-computation.

Input: CPS basic monthly public-use CSV(s) with uppercase BLS names.
Figures are NOT seasonally adjusted. Validated against BLS Table A-15 NSA
(May 2026: U-3 4.07 vs 4.1, U-4 4.32 vs 4.3, U-5 4.98 vs 5.0, U-6 7.67 vs 7.7).
"""

import numpy as np
import pandas as pd

WEIGHT_COL = "PWCMPWGT"  # composited final weight, 4 implied decimals
FT_HOURS = 35            # BLS full-time threshold
MIN_OCC_CELL = 30        # min unweighted employed records per occupation
SD_THRESHOLD = 1.0

# PEEDUCA is an ordinal code (31-46), not a scale: code steps are 1 grade at
# the low end but 2+ years at the top. Approximate years of schooling make the
# mean/SD meaningful (Jaeger 1997-style mapping).
EDU_YEARS = {
    31: 0, 32: 2.5, 33: 5.5, 34: 7.5, 35: 9, 36: 10, 37: 11, 38: 12,
    39: 12, 40: 13, 41: 14, 42: 14, 43: 16, 44: 18, 45: 20, 46: 20,
}

INPUT_COLS = [WEIGHT_COL, "PEMLR", "PRDISC", "PRWKSTAT", "PEHRACTT",
              "PEEDUCA", "PTIO1OCD"]


# ---------------------------------------------------------------------------
# Loading / pooling
# ---------------------------------------------------------------------------
def load_cps(path, usecols=INPUT_COLS) -> pd.DataFrame:
    keep = {c.upper() for c in usecols} if usecols else None
    df = pd.read_csv(path, usecols=(lambda c: str(c).upper() in keep) if keep else None)
    df.columns = df.columns.str.upper()
    return df


def load_pooled(paths) -> pd.DataFrame:
    """Stack monthly files, tagging each with its source as MONTH.

    Pooling stabilizes small cells but blends seasonal factors -- do NOT read
    trend from a pooled figure. Pass n_months to `person_contributions` so
    levels are average monthly levels rather than sums across months.
    """
    return pd.concat([load_cps(p).assign(MONTH=str(p)) for p in paths],
                     ignore_index=True)


# ---------------------------------------------------------------------------
# Person-level weighted contributions
# ---------------------------------------------------------------------------
def overqualified(df: pd.DataFrame, employed: pd.Series, w: pd.Series,
                  sd_threshold: float = SD_THRESHOLD,
                  min_cell: int = MIN_OCC_CELL) -> tuple[pd.Series, pd.Series]:
    """Realized-matches overeducation.

    Flags employed workers whose years of schooling exceed their occupation's
    weighted mean by more than `sd_threshold` weighted SDs. Occupation norms
    come from whatever frame is passed in -- pass the national pool so every
    state is judged against the same norm. Returns (in_scope, overqualified).
    """
    edu = df["PEEDUCA"].map(EDU_YEARS)
    occ = df["PTIO1OCD"].where(employed & edu.notna() & (df["PTIO1OCD"] > 0))

    g = pd.DataFrame({"w": w, "wx": w * edu, "wxx": w * edu ** 2}).groupby(occ)
    s, n = g.sum(), g.size()
    mean = s["wx"] / s["w"]
    sd = np.sqrt((s["wxx"] / s["w"] - mean ** 2).clip(lower=0))  # Var = E[x²] - E[x]²

    cutoff = (mean + sd_threshold * sd).where((n >= min_cell) & (sd > 0))
    occ_cutoff = occ.map(cutoff)
    in_scope = occ_cutoff.notna()
    return in_scope, in_scope & (edu > occ_cutoff)


def person_contributions(df: pd.DataFrame, n_months: int = 1,
                         weight_col: str = WEIGHT_COL) -> pd.DataFrame:
    """Weighted per-person counts; sum any column over a group for its level.

    Dividing weights by n_months turns pooled sums into average monthly
    levels (each month's weights already sum to the full population).
    """
    w = df[weight_col].clip(lower=0) / 10_000 / n_months
    mlr = df["PEMLR"]
    employed = mlr.isin([1, 2])

    # PRDISC is BLS's own recode (universe: NILF who want a job, PRJOBSEA 1-4):
    # 1 = discouraged, 2 = conditionally interested (other marginally attached).
    discouraged = df["PRDISC"].eq(1)
    marg_attached = df["PRDISC"].isin([1, 2])

    # Part time for economic reasons: at work 1-34 actual hrs, either usually
    # FT (3) or usually PT (6). Gap uses actual hours, which define PTER.
    pter = df["PRWKSTAT"].isin([3, 6])
    hrs_gap = (FT_HOURS - df["PEHRACTT"]).clip(lower=0).where(pter, 0)

    in_scope, overq = overqualified(df, employed, w)

    return pd.DataFrame({
        "employed": employed,
        "unemployed": mlr.isin([3, 4]),
        "discouraged": discouraged,
        "marginally_attached": marg_attached,
        "affected_workers": pter,
        "total_hrs_gap": hrs_gap,
        "employed_in_scope": in_scope,
        "overqualified_workers": overq,
    }).mul(w, axis=0)


# ---------------------------------------------------------------------------
# Levels -> rates
# ---------------------------------------------------------------------------
def summarize(levels: pd.DataFrame) -> pd.DataFrame:
    """Add rates to a frame of summed levels (one row per group)."""
    U, MA, D, PT = (levels[c] for c in
                    ["unemployed", "marginally_attached", "discouraged",
                     "affected_workers"])
    LF = levels["employed"] + U
    return levels.assign(
        labor_force=LF,
        u3=100 * U / LF,
        u4=100 * (U + D) / (LF + D),
        u5=100 * (U + MA) / (LF + MA),
        u6=100 * (U + MA + PT) / (LF + MA),
        avg_hrs_gap=levels["total_hrs_gap"] / PT,
        overqualification_rate=100 * levels["overqualified_workers"]
        / levels["employed_in_scope"],
    )


def all_measures(df: pd.DataFrame, n_months: int = 1) -> pd.Series:
    """All measures for the whole frame."""
    return summarize(person_contributions(df, n_months).sum().to_frame().T).iloc[0]


def all_measures_by(df: pd.DataFrame, group_cols, n_months: int = 1) -> pd.DataFrame:
    """All measures by subgroup (e.g. ['PESEX'] or ['GESTFIPS']).

    Occupation norms are computed on the full frame, not within each group.
    """
    return summarize(person_contributions(df, n_months).groupby(
        [df[c] for c in np.atleast_1d(group_cols)]).sum())


if __name__ == "__main__":
    import sys
    paths = sys.argv[1:] or ["cps_basic.csv"]
    res = all_measures(load_pooled(paths), n_months=len(paths))
    for k in ["u3", "u4", "u5", "u6"]:
        print(f"{k.upper()}: {res[k]:.2f}%")
    print(f"Hours gap: {res['avg_hrs_gap']:.1f} hrs/wk across "
          f"{res['affected_workers']:,.0f} workers")
    print(f"Overqualification rate: {res['overqualification_rate']:.2f}%")
