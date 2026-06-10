"""
Underemployment measures from CPS Basic Monthly public-use microdata (BLS).

Computes four measures in one pass:
  1. U-3   -- official unemployment rate
  2. U-6   -- broadest BLS alternative measure
  3. Hours gap -- involuntary part-time intensity (actual vs. usual hours)
  4. Occupational mismatch -- overqualification (education vs. occupation norm)

Input: a CPS basic monthly public-use file (CSV, uppercase BLS names). For the
fixed-width version, swap read_csv for pd.read_fwf() using the dictionary specs.

These are point-in-time, NOT seasonally adjusted, figures for a single file.
Expect small differences from published SA tables for that reason.

VERIFY every variable name/code against the CPS data dictionary for your file's
year before trusting output -- the PEDW* and PEHRRSN* modules drift over time.
"""

import pandas as pd


# ---------------------------------------------------------------------------
# Loading / pooling
# ---------------------------------------------------------------------------
def load_cps(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.upper()
    return df


def load_pooled(paths) -> pd.DataFrame:
    """Stack several monthly files (e.g. Jan-Apr) into one frame.

    Pooling stabilizes small subgroup cells, but blends months with different
    seasonal factors -- do NOT read trend or make seasonal claims from a pooled
    figure. Weights are summed across files, so divide rates, never use the
    pooled weighted COUNTS as if they were a single month's population.
    """
    frames = []
    for p in paths:
        d = load_cps(p)
        d["SRC_FILE"] = p
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 1 & 2.  U-3 and U-6
# ---------------------------------------------------------------------------
def alt_measures(df: pd.DataFrame, weight_col: str = "PWCMPWGT") -> dict:
    w = df[weight_col] / 10000.0  # 4 implied decimals

    mlr = df["PEMLR"]
    employed    = mlr.isin([1, 2])
    unemployed  = mlr.isin([3, 4])
    nilf        = mlr.isin([5, 6, 7])
    labor_force = employed | unemployed

    # Part time for economic reasons (worked 1-34 hrs for econ reasons)
    pter = df["PRWKSTAT"].isin([3, 6])

    # Marginally attached (includes discouraged): NILF + want job + available
    # + looked in last 12 months (not last 4 weeks).  >>> VERIFY codes <<<
    marg_attached = (
        nilf
        & df["PEDWWNTO"].eq(1)        # wants a job
        & df["PEDWAVL"].eq(1)         # available to work
        & df["PEDWLKWK"].isin([1, 2]) # looked within last 12 months
    )

    s = lambda mask: float(w[mask].sum())
    U, LF = s(unemployed), s(labor_force)
    MA, PT = s(marg_attached), s(pter)

    u3 = U / LF * 100
    u6 = (U + MA + PT) / (LF + MA) * 100
    return {
        "employed": s(employed), "unemployed": U, "labor_force": LF,
        "marginally_attached": MA, "pt_for_econ_reasons": PT,
        "U3": round(u3, 2), "U6": round(u6, 2),
    }


# ---------------------------------------------------------------------------
# 3.  Hours gap -- intensity of involuntary part-time
# ---------------------------------------------------------------------------
def hours_gap(df: pd.DataFrame, weight_col: str = "PWCMPWGT") -> dict:
    """Weighted hours shortfall among PT-for-economic-reasons workers.

    Gap = usual full-time threshold (35) minus usual hours, for those who want
    more work. Reported as total weekly FTE-equivalent hours lost and as an
    average per affected worker. Uses PEHRUSLT (usual); PEHRACTT (actual) is
    available too if you prefer an actual-vs-usual definition.
    """
    w = df[weight_col] / 10000.0
    usual = pd.to_numeric(df["PEHRUSLT"], errors="coerce")

    # Involuntary PT: PT for economic reasons and wants to work more.
    affected = df["PRWKSTAT"].isin([3, 6]) & df["PEHRWANT"].eq(1) & usual.between(1, 34)

    gap_per_person = (35 - usual).clip(lower=0)
    total_gap = float((w[affected] * gap_per_person[affected]).sum())
    n_affected = float(w[affected].sum())
    avg_gap = total_gap / n_affected if n_affected else 0.0

    return {
        "affected_workers": n_affected,
        "total_weekly_hours_gap": round(total_gap, 0),
        "avg_hours_gap_per_worker": round(avg_gap, 1),
    }


# ---------------------------------------------------------------------------
# 4.  Occupational mismatch -- overqualification (realized-matches method)
# ---------------------------------------------------------------------------
def occupational_mismatch(df: pd.DataFrame, weight_col: str = "PWCMPWGT",
                          sd_threshold: float = 1.0) -> dict:
    """Realized-matches overeducation: flag employed workers whose education
    exceeds the occupation's mean by more than `sd_threshold` standard
    deviations of education within that occupation.

    This is a relative, data-driven definition -- no external 'required
    education' table needed. The job-analysis alternative (mapping occupations
    to BLS education-and-training categories or O*NET Job Zones) is more
    defensible but requires an external crosswalk you'd supply separately.

    PEEDUCA is an ordinal code, not years of schooling, so treat the result as
    a rank-based heuristic. Occupation cells with few observations are noisy;
    we skip cells below `min_cell`.
    """
    w = df[weight_col] / 10000.0
    employed = df["PEMLR"].isin([1, 2])
    occ = df["PTIO1OCD"]
    edu = pd.to_numeric(df["PEEDUCA"], errors="coerce")

    emp = df[employed].copy()
    emp["_W"] = w[employed]
    emp["_EDU"] = edu[employed]
    emp["_OCC"] = occ[employed]

    min_cell = 30
    stats = emp.groupby("_OCC")["_EDU"].agg(["mean", "std", "count"])
    stats = stats[stats["count"] >= min_cell]

    emp = emp.join(stats, on="_OCC", rsuffix="_occ")
    emp = emp.dropna(subset=["mean", "std"])
    emp = emp[emp["std"] > 0]

    overqualified = emp["_EDU"] > (emp["mean"] + sd_threshold * emp["std"])

    n_over = float(emp.loc[overqualified, "_W"].sum())
    n_total = float(emp["_W"].sum())
    rate = n_over / n_total * 100 if n_total else 0.0

    return {
        "overqualified_workers": n_over,
        "employed_in_scope": n_total,
        "overqualification_rate": round(rate, 2),
    }


# ---------------------------------------------------------------------------
# Convenience: all four at once, optionally by subgroup
# ---------------------------------------------------------------------------
def all_measures(df: pd.DataFrame, weight_col: str = "PWCMPWGT") -> dict:
    out = {}
    out.update(alt_measures(df, weight_col))
    out.update(hours_gap(df, weight_col))
    out.update(occupational_mismatch(df, weight_col))
    return out


def all_measures_by(df: pd.DataFrame, group_cols, weight_col="PWCMPWGT") -> pd.DataFrame:
    """All four measures broken out by grouping variable(s).

    The payoff of microdata: e.g. group_cols=['PESEX'] or ['GESTFIPS'].
    Small monthly cells get noisy fast -- pool months or widen the group.
    """
    rows = []
    for key, sub in df.groupby(group_cols):
        rec = all_measures(sub, weight_col)
        rec["group"] = key
        rows.append(rec)
    return pd.DataFrame(rows).set_index("group")


if __name__ == "__main__":
    import sys
    # Pass one path, or several (Jan..Apr) to pool.
    paths = sys.argv[1:] or ["cps_basic.csv"]
    df = load_pooled(paths) if len(paths) > 1 else load_cps(paths[0])

    res = all_measures(df)
    print(f"U-3:  {res['U3']}%")
    print(f"U-6:  {res['U6']}%")
    print(f"Hours gap: {res['avg_hours_gap_per_worker']} hrs/wk "
          f"across {res['affected_workers']:,.0f} workers")
    print(f"Overqualification rate: {res['overqualification_rate']}%")
    print()
    print(res)

    # Validation: not-seasonally-adjusted U-3 should closely match the published
    # NSA U-3 for the month. If it does, weights and core coding are sound.
