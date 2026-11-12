# -*- coding: utf-8 -*-
"""U.S. Employment Tracker.

A self-contained Plotly Dash app that visualizes state-level underemployment
measures derived from BLS CPS Basic Monthly Microdata (pooled 2026 months).

Data is loaded once at startup from the project's DuckDB database into a pandas
DataFrame; all callbacks operate on that in-memory frame.

Run (from the worktree root):
    uv run python scripts/dashboard.py
Then open http://localhost:8051
"""

from __future__ import annotations

from pathlib import Path

import dash
import dash_bootstrap_components as dbc
import duckdb
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html
from plotly.colors import sample_colorscale

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent

# Palette: shades of light blue and indigo. Indigo marks emphasis (the
# selected state, reference lines); light blues are everything else.
COLOR_SCALE = "sunset"        # choropleth: reversed dense
BAR_SCALE = "teal"              # state vs. national bar chart
SCATTER_SCALE = "Sunsetdark"       # pale yellow (low U-3) -> deep purple (high U-3)
RANK_SCALE = "Temps"         # state rankings
GAUGE_GOOD, GAUGE_BAD, GAUGE_BAR = "#d4edda", "#f8d7da", "cadetblue"
PALE_BLUE = "#eef4fc"          # page and card backgrounds
PALE_INDIGO = "#eeedfb"
LIGHT_BLUE = "#b9d4f1"         # secondary marks (national bars, other states)
SKY_BLUE = "#6f9fdc"           # primary marks
INDIGO = "#4338ca"             # selected state
DEEP_INDIGO = "#1e1b4b"        # outlines and reference lines
PANEL_BG = "rgba(238,244,252,0.6)"
GRID = "#dde6f3"

# employed_in_scope below this is treated as too small to report reliably.
SUPPRESSION_THRESHOLD = 100_000

# Standard FIPS -> USPS abbreviation, and FIPS -> full name.
FIPS_TO_ABBR = {
    1: "AL", 2: "AK", 4: "AZ", 5: "AR", 6: "CA", 8: "CO", 9: "CT",
    10: "DE", 11: "DC", 12: "FL", 13: "GA", 15: "HI", 16: "ID", 17: "IL",
    18: "IN", 19: "IA", 20: "KS", 21: "KY", 22: "LA", 23: "ME", 24: "MD",
    25: "MA", 26: "MI", 27: "MN", 28: "MS", 29: "MO", 30: "MT", 31: "NE",
    32: "NV", 33: "NH", 34: "NJ", 35: "NM", 36: "NY", 37: "NC", 38: "ND",
    39: "OH", 40: "OK", 41: "OR", 42: "PA", 44: "RI", 45: "SC", 46: "SD",
    47: "TN", 48: "TX", 49: "UT", 50: "VT", 51: "VA", 53: "WA", 54: "WV",
    55: "WI", 56: "WY",
}

FIPS_TO_NAME = {
    1: "Alabama", 2: "Alaska", 4: "Arizona", 5: "Arkansas", 6: "California",
    8: "Colorado", 9: "Connecticut", 10: "Delaware",
    11: "District of Columbia", 12: "Florida", 13: "Georgia", 15: "Hawaii",
    16: "Idaho", 17: "Illinois", 18: "Indiana", 19: "Iowa", 20: "Kansas",
    21: "Kentucky", 22: "Louisiana", 23: "Maine", 24: "Maryland",
    25: "Massachusetts", 26: "Michigan", 27: "Minnesota", 28: "Mississippi",
    29: "Missouri", 30: "Montana", 31: "Nebraska", 32: "Nevada",
    33: "New Hampshire", 34: "New Jersey", 35: "New Mexico", 36: "New York",
    37: "North Carolina", 38: "North Dakota", 39: "Ohio", 40: "Oklahoma",
    41: "Oregon", 42: "Pennsylvania", 44: "Rhode Island",
    45: "South Carolina", 46: "South Dakota", 47: "Tennessee", 48: "Texas",
    49: "Utah", 50: "Vermont", 51: "Virginia", 53: "Washington",
    54: "West Virginia", 55: "Wisconsin", 56: "Wyoming",
}

NATIONAL_FIPS = 0
NATIONAL_LABEL = "National"

# BLS Table A-15 NSA averages used as reference benchmarks. These are the
# Jan-Oct 2026 averages: update them when the pipeline's month range changes.
BLS_NSA_U3_AVG = 4.28
BLS_NSA_U6_AVG = 7.96

# Map metric -> (column, display label, hover label).
MAP_METRICS = {
    "u3": ("u3", "U-3 (%)", "U-3"),
    "u6": ("u6", "U-6 (%)", "U-6"),
    "overqualification_rate": (
        "overqualification_rate",
        "Overqualification Rate (%)",
        "Overqual.",
    ),
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _find_data_root(start: Path) -> Path:
    """Walk up from the script to find the checkout that holds the DuckDB file.

    Mirrors the pattern in bls_cps_pipeline.py so the app works both in the
    main checkout and inside a .claude/worktrees/<name> worktree (whose own
    duckdb/ dir may be empty).
    """
    needed = Path("duckdb") / "bls_cps.duckdb"
    for parent in (start, *start.parents):
        if (parent / needed).exists():
            return parent
    return start.parent


PROJECT_ROOT = _find_data_root(SCRIPT_DIR)
DB_PATH = PROJECT_ROOT / "duckdb" / "bls_cps.duckdb"


def load_data() -> pd.DataFrame:
    """Load state_measures into a DataFrame, enriched with name/abbr columns."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Could not locate DuckDB database at {DB_PATH}. "
            "Run the pipeline first to build state_measures."
        )
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = con.execute("SELECT * FROM state_measures").df()
    finally:
        con.close()

    df["state_fips"] = df["state_fips"].astype(int)
    df["state_name"] = df["state_fips"].map(
        lambda f: NATIONAL_LABEL if f == NATIONAL_FIPS else FIPS_TO_NAME.get(f)
    )
    df["state_abbr"] = df["state_fips"].map(FIPS_TO_ABBR)
    return df


DATA = load_data()

# National reference row (single record) and per-state rows (exclude national).
NATIONAL = DATA[DATA["state_fips"] == NATIONAL_FIPS].iloc[0]
STATES = DATA[DATA["state_fips"] != NATIONAL_FIPS].copy()
MONTH_RANGE = str(NATIONAL["month_range"])

# Dropdown options: National first, then states alphabetically by name.
DROPDOWN_OPTIONS = [{"label": NATIONAL_LABEL, "value": NATIONAL_FIPS}] + [
    {"label": name, "value": int(fips)}
    for fips, name in sorted(
        zip(STATES["state_fips"], STATES["state_name"]), key=lambda x: x[1]
    )
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_row(fips: int) -> pd.Series:
    """Return the state_measures row for a FIPS code."""
    return DATA[DATA["state_fips"] == int(fips)].iloc[0]


def is_suppressed(row: pd.Series) -> bool:
    """True when the employed sample is too small to report overqualification."""
    return float(row["employed_in_scope"]) < SUPPRESSION_THRESHOLD


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def fmt_hrs(value: float) -> str:
    return f"{value:.1f} hrs"


def fmt_delta(value: float, national: float, lower_is_better: bool = True) -> tuple[str, str]:
    """Return (text, color) for a value's delta vs. the national average."""
    delta = value - national
    arrow = "+" if delta > 0 else ("-" if delta < 0 else "~")
    favorable = (delta < 0) if lower_is_better else (delta > 0)
    if delta == 0:
        color = "#6c757d"
    else:
        color = "#198754" if favorable else "#dc3545"
    return f"{arrow} {abs(delta):.2f} vs nat'l", color


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------

def build_choropleth(metric_key: str, selected_fips: int) -> go.Figure:
    col, axis_label, hover_label = MAP_METRICS[metric_key]
    states = STATES.copy()

    fig = go.Figure(
        data=go.Choropleth(
            locations=states["state_abbr"],
            z=states[col],
            locationmode="USA-states",
            colorscale=COLOR_SCALE,
            colorbar_title=axis_label,
            marker_line_color="white",
            marker_line_width=0.5,
            customdata=states[["state_name"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                + hover_label
                + ": %{z:.2f}%<extra></extra>"
            ),
        )
    )

    # Highlight the selected state with a thick outline (skip for National).
    if int(selected_fips) != NATIONAL_FIPS:
        sel = states[states["state_fips"] == int(selected_fips)]
        if not sel.empty:
            fig.add_trace(
                go.Choropleth(
                    locations=sel["state_abbr"],
                    z=sel[col],
                    locationmode="USA-states",
                    colorscale=COLOR_SCALE,
                    showscale=False,
                    marker_line_color=DEEP_INDIGO,
                    marker_line_width=3,
                    hoverinfo="skip",
                )
            )

    fig.update_layout(
        geo=dict(scope="usa", lakecolor="white", bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=10, b=0),
        height=420,
        paper_bgcolor=PANEL_BG,
    )
    return fig


def build_bar(row: pd.Series) -> go.Figure:
    """Grouped bar: selected state vs. national for the four headline measures."""
    suppressed = is_suppressed(row)
    measures = ["U-3", "U-6", "Avg Hrs Gap", "Overqual. Rate"]
    state_vals = [
        float(row["u3"]),
        float(row["u6"]),
        float(row["avg_hrs_gap"]),
        float("nan") if suppressed else float(row["overqualification_rate"]),
    ]
    nat_vals = [
        float(NATIONAL["u3"]),
        float(NATIONAL["u6"]),
        float(NATIONAL["avg_hrs_gap"]),
        float(NATIONAL["overqualification_rate"]),
    ]
    state_label = str(row["state_name"])
    is_national = int(row["state_fips"]) == NATIONAL_FIPS
    # Two shades from the ice scale: deep blue for the state, pale ice for
    # national, and near-black from the scale's dark end for reference lines.
    state_color, nat_color, ref_color = sample_colorscale(BAR_SCALE, [0.35, 0.8, 0.0])

    fig = go.Figure()
    if not is_national:
        fig.add_trace(
            go.Bar(
                name=state_label,
                x=measures,
                y=state_vals,
                marker_color=state_color,
                text=[
                    "n/a" if (suppressed and m == "Overqual. Rate") else f"{v:.1f}"
                    for m, v in zip(measures, state_vals)
                ],
                textposition="outside",
            )
        )
    fig.add_trace(
        go.Bar(
            name=NATIONAL_LABEL,
            x=measures,
            y=nat_vals,
            marker_color=nat_color if not is_national else state_color,
            text=[f"{v:.1f}" for v in nat_vals],
            textposition="inside",
        )
    )

    # BLS NSA reference lines drawn inside the U-3 and U-6 bar bodies.
    _cats = ["U-3", "U-6", "Avg Hrs Gap", "Overqual. Rate"]
    for cat, ref_val in [("U-3", BLS_NSA_U3_AVG), ("U-6", BLS_NSA_U6_AVG)]:
        idx = _cats.index(cat)
        fig.add_shape(
            type="line",
            x0=idx - 0.38, x1=idx + 0.38,
            y0=ref_val, y1=ref_val,
            line=dict(color=ref_color, width=2, dash="dot"),
            xref="x", yref="y",
        )
        fig.add_annotation(
            x=idx, y=ref_val,
            xref="x", yref="y",
            text=f"BLS NSA: {ref_val:.2f}%",
            showarrow=False,
            font=dict(size=9, color='#000000'),
            bgcolor="rgba(255,255,255,0.82)",
            borderpad=2,
            yshift=10,
        )

    fig.update_layout(
        barmode="group",
        title=dict(text=f"Measures: {state_label} vs National", x=0.5, font=dict(size=14)),
        margin=dict(l=10, r=10, t=60, b=60),
        height=380,
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
        paper_bgcolor=PANEL_BG,
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(title="value (% or hrs)", gridcolor=GRID),
    )
    return fig


def build_gauge(row: pd.Series) -> go.Figure:
    """Bullet gauge of the selected state's U-6 against the national U-6."""
    state_u6 = float(row["u6"])
    nat_u6 = float(NATIONAL["u6"])
    state_label = str(row["state_name"])
    axis_max = max(float(STATES["u6"].max()), nat_u6) * 1.15

    fig = go.Figure(
        go.Indicator(
            mode="number+gauge+delta",
            value=state_u6,
            number=dict(suffix="%", font=dict(size=22)),
            delta=dict(
                reference=nat_u6,
                increasing=dict(color="#dc3545"),
                decreasing=dict(color="#198754"),
                suffix="%",
            ),
            gauge=dict(
                shape="bullet",
                axis=dict(range=[0, axis_max], ticksuffix="%"),
                bar=dict(color=GAUGE_BAR),
                steps=[
                    dict(range=[0, nat_u6], color=GAUGE_GOOD),
                    dict(range=[nat_u6, axis_max], color=GAUGE_BAD),
                ],
                threshold=dict(
                    line=dict(color=DEEP_INDIGO, width=3),
                    thickness=0.85,
                    value=nat_u6,
                ),
            ),
            title=dict(
                text=(
                    f"U-6: {state_label}"
                    f"<br><sub>dark line = CPS national {nat_u6:.2f}%"
                    f" | BLS NSA avg {BLS_NSA_U6_AVG:.2f}%</sub>"
                ),
                font=dict(size=14),
            ),
        )
    )
    fig.update_layout(
        margin=dict(l=20, r=30, t=90, b=50),
        height=380,
        paper_bgcolor=PANEL_BG,
        annotations=[dict(
            x=0.5, y=-0.18,
            xref="paper", yref="paper",
            text="U-6 Broad Underutilization Rate (%)",
            showarrow=False,
            font=dict(size=12, color="#555555"),
        )],
    )
    return fig


def build_scatter(selected_fips: int) -> go.Figure:
    """Bubble chart of all states: x=U-3, y=Overqualification Rate.

    Bubble size grows with the overqualification rate; color deepens with U-3.
    """
    states = STATES.copy()
    oq = states["overqualification_rate"]
    sizes = 10 + 30 * (oq - oq.min()) / (oq.max() - oq.min())

    fig = go.Figure(
        go.Scatter(
            x=states["u3"],
            y=oq,
            mode="markers",
            marker=dict(
                size=sizes,
                color=states["u3"],
                colorscale=SCATTER_SCALE,
                colorbar=dict(title="U-3 (%)", thickness=12),
                opacity=0.85,
                line=dict(color="white", width=0.8),
            ),
            text=states["state_name"],
            hovertemplate="<b>%{text}</b><br>U-3: %{x:.2f}%<br>Overqual: %{y:.2f}%<extra></extra>",
            name="States",
        )
    )

    fig.add_vline(x=float(NATIONAL["u3"]), line_dash="dot", line_color="#adb5bd")
    fig.add_hline(y=float(NATIONAL["overqualification_rate"]), line_dash="dot", line_color="#adb5bd")

    # Selected state: an open black ring around its bubble, plus a label.
    is_sel = states["state_fips"] == int(selected_fips)
    if is_sel.any():
        fig.add_trace(
            go.Scatter(
                x=states.loc[is_sel, "u3"],
                y=oq[is_sel],
                mode="markers+text",
                marker=dict(size=sizes[is_sel] + 8, color="rgba(0,0,0,0)",
                            line=dict(color="black", width=2.5)),
                text=states.loc[is_sel, "state_abbr"],
                textposition="top center",
                hoverinfo="skip",
                name="Selected",
            )
        )

    fig.update_layout(
        title=dict(
            text="States: U-3 vs Overqualification Rate"
                 "<br><sub>bubble size = overqualification rate, color = U-3</sub>",
            x=0.5, font=dict(size=14),
        ),
        margin=dict(l=10, r=10, t=70, b=10),
        height=460,
        showlegend=False,
        paper_bgcolor=PANEL_BG,
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="U-3 (%)", gridcolor=GRID),
        yaxis=dict(title="Overqualification Rate (%)", gridcolor=GRID),
    )
    return fig


def build_rankings(selected_fips: int, metric_key: str) -> go.Figure:
    """Horizontal bar chart of all states ranked by the selected map metric."""
    col, axis_label, _ = MAP_METRICS[metric_key]
    sel = int(selected_fips)

    states = STATES.copy().sort_values(col, ascending=True)
    nat_val = float(NATIONAL[col])

    # Bars colored by value on Viridis; the selected state gets a black outline.
    is_sel = states["state_fips"].astype(int) == sel

    fig = go.Figure(
        go.Bar(
            x=states[col],
            y=states["state_name"],
            orientation="h",
            marker=dict(
                color=states[col],
                colorscale=RANK_SCALE,
                line=dict(color="black", width=is_sel.map({True: 2.5, False: 0}).tolist()),
            ),
            customdata=states["state_name"],
            hovertemplate="<b>%{customdata}</b><br>" + axis_label + ": %{x:.2f}%<extra></extra>",
        )
    )

    fig.add_vline(
        x=nat_val,
        line_dash="dash",
        line_color=DEEP_INDIGO,
        annotation_text=f"National {nat_val:.2f}%",
        annotation_position="top",
    )

    fig.update_layout(
        title=dict(text=f"State Rankings: {axis_label}", x=0.5, font=dict(size=14)),
        margin=dict(l=10, r=10, t=70, b=10),
        height=520,
        showlegend=False,
        paper_bgcolor=PANEL_BG,
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title=axis_label, gridcolor=GRID),
        yaxis=dict(title="", tickfont=dict(size=9)),
    )
    return fig


def build_kpi_cards(row: pd.Series) -> list:
    """Return a list of dbc.Col KPI cards for the selected state."""
    suppressed = is_suppressed(row)

    def card(title: str, value_text: str, delta_text: str, delta_color: str) -> dbc.Col:
        body = [
            html.Div(title, className="text-muted small text-uppercase"),
            html.H3(value_text, className="my-2 fw-bold", style={"fontSize": "1.6rem"}),
            html.Div(delta_text, style={"color": delta_color, "fontSize": "0.9rem", "marginTop": "4px"}),
        ]
        return dbc.Col(
            dbc.Card(dbc.CardBody(body, className="py-3 px-3"), className="shadow-sm h-100", style={"background": f"linear-gradient(135deg, {PALE_BLUE} 0%, #ffffff 100%)"}),
            xs=12, sm=6, md=3,
        )

    u3_delta, u3_c = fmt_delta(float(row["u3"]), float(NATIONAL["u3"]))
    u6_delta, u6_c = fmt_delta(float(row["u6"]), float(NATIONAL["u6"]))
    hg_delta, hg_c = fmt_delta(float(row["avg_hrs_gap"]), float(NATIONAL["avg_hrs_gap"]))

    if suppressed:
        oq_value, oq_delta, oq_c = "Insufficient sample", "", "#6c757d"
    else:
        oq_value = fmt_pct(float(row["overqualification_rate"]))
        oq_delta, oq_c = fmt_delta(
            float(row["overqualification_rate"]),
            float(NATIONAL["overqualification_rate"]),
        )

    return [
        card("U-3", fmt_pct(float(row["u3"])), u3_delta, u3_c),
        card("U-6", fmt_pct(float(row["u6"])), u6_delta, u6_c),
        card("Avg Hours Gap", fmt_hrs(float(row["avg_hrs_gap"])), hg_delta, hg_c),
        card("Overqualification Rate", oq_value, oq_delta, oq_c),
    ]


# ---------------------------------------------------------------------------
# About (shown in the modal)
# ---------------------------------------------------------------------------

TECH_OVERVIEW_MD = f"""
#### About:

State and national underemployment measures from the Current Population Survey (CPS),
a monthly survey of about 60,000 households, pooled over {MONTH_RANGE}. Not seasonally
adjusted.


#### Sources:

- CPS Basic Monthly public-use files (Census Bureau): 10 monthly CSVs, 1,214,584 person
  records, 35 of 393 variables kept.
- BLS Table A-15 (U-1 to U-6) from the BLS Public Data API v2, used for validation.


#### Pipeline:

Python 3.12 (uv), pandas, DuckDB, Plotly Dash, deployed with Docker and gunicorn.

- `bls_cps_pipeline.py`: load, clean and aggregate; write to DuckDB and CSV.
- `cps_underemp.py`: all measure math.
- `bls_api_pull.py`, `bls_crosscheck.py`: pull BLS figures and compare.
- `dashboard.py`: reads the 52-row `state_measures` table.


#### Process:

1. Load each monthly CSV and stack them.
2. Label codes and rename columns; save person records to `cps_microdata`.
3. Turn each person into weighted counts, then sum by state and nationally.
4. Compute rates and write `state_measures` to DuckDB and CSV.


#### Weights:

>`PWCMPWGT` / 10,000 (four implied decimals), divided again by the number of months, so
>counts are average monthly levels. Rates are unaffected.

#### Unemployment Measures:

- Employed: `PEMLR` 1 to 2. Unemployed: `PEMLR` 3 to 4.
- Discouraged (D): `PRDISC` = 1. Marginally attached (MA): `PRDISC` = 1 or 2.
- Part time for economic reasons (PT): `PRWKSTAT` = 3 or 6.

| Measure | Formula |
|---|---|
| U-3 | U / LF |
| U-4 | (U + D) / (LF + D) |
| U-5 | (U + MA) / (LF + MA) |
| U-6 | (U + MA + PT) / (LF + MA) |


#### Involuntary Hours Gap:

> 35 minus actual hours worked (`PEHRACTT`) for each PT worker, weighted and averaged.


#### Overqualification:

1. Employed workers; education (`PEEDUCA`) converted to years of schooling
   (high school 12, associate 14, bachelor's 16, master's 18, doctorate 20). The raw
   codes are ranks, not years: one step can be one grade or two years of graduate
   school, so averaging them would distort the result.
2. Weighted mean and SD of schooling per occupation (`PTIO1OCD`), national pool,
   occupations with 30 or more sampled workers. National norms hold every state to
   the same benchmark.
3. Overqualified: schooling above the occupation mean + 1 SD.

No BLS equivalent, so not externally validated.


#### Validation:

| Measure: | Dashboard: | BLS Published Avg: |
|---|---|---|
| U-3 | {NATIONAL['u3']:.2f}% | {BLS_NSA_U3_AVG:.2f}% |
| U-6 | {NATIONAL['u6']:.2f}% | {BLS_NSA_U6_AVG:.2f}% |

U-4 and U-5 are within 0.01 points for January to October 2026.


#### Limits:

- Pooled snapshot: no trends or seasonal reading.
- Small states have wide sampling error.
- Overqualification hidden where weighted employment in scope is under 100,000.
"""


# ---------------------------------------------------------------------------
# App layout
# ---------------------------------------------------------------------------

app = dash.Dash(
    __name__,
    assets_folder=str(SCRIPT_DIR / "assets"),
    external_stylesheets=[dbc.themes.FLATLY],
    title="U.S. Employment Tracker",
)
server = app.server

title_block = dbc.Card(
    dbc.CardBody(
        [
            html.H2("U.S. Employment Tracker", className="fw-bold mb-3"),
            html.Div(
                [
                    html.Span("Author: Bruce A. Lee", className="small text", style={"color": '#000000'}),
                    dbc.Button(
                        "About",
                        id="overview-open",
                        n_clicks=0,
                        outline=True,
                        className="ms-2",
                        style={
                            "fontSize": "0.75rem",
                            "lineHeight": "1.5",
                            "padding": "0 0.5rem",
                            "color": INDIGO,
                            "borderColor": INDIGO,
                        },
                    ),
                    dbc.Button(
                        "Home",
                        href="https://brucea-lee.com",
                        external_link=True,
                        target="_self",
                        color="primary",
                        outline=True,
                        className="ms-2",
                        style={
                            "fontSize": "0.75rem",
                            "lineHeight": "1.5",
                            "padding": "0 0.5rem",
                            "color": INDIGO,
                            "borderColor": INDIGO,
                        },
                    ),
                ],
                className="d-flex align-items-center mb-2",
            ),
            html.P(
                f"Dataset: CPS Basic Monthly Microdata | {MONTH_RANGE}",
                className="text-muted mb-2",
                style={"fontSize": "0.90rem"},
            ),
            html.Hr(),
            html.P(
                "Select a state on the map or dropdown to explore measures.",
                className="small text",
            ),
            html.Label("Select a State", className="mt-3 fw-semibold small"),
            dcc.Dropdown(
                id="state-dropdown",
                options=DROPDOWN_OPTIONS,
                value=NATIONAL_FIPS,
                clearable=False,
            ),
        ],
        className="p-4",
    ),
    className="shadow-sm h-100",
    style={"background": f"linear-gradient(135deg, #ffffff 0%, {PALE_INDIGO} 100%)"},
)

map_block = dbc.Card(
    dbc.CardBody(
        [
            html.Div(
                [
                    html.Span("Map metric:", className="fw-semibold me-3 small"),
                    dbc.RadioItems(
                        id="map-metric",
                        options=[
                            {"label": "U-3", "value": "u3"},
                            {"label": "U-6", "value": "u6"},
                            {"label": "Overqualification", "value": "overqualification_rate"},
                        ],
                        value="u3",
                        inline=True,
                        className="d-inline-block",
                    ),
                ],
                className="mb-2",
            ),
            dcc.Graph(id="choropleth", config={"displayModeBar": False}),
        ]
    ),
    className="shadow-sm h-100",
    style={"background": f"linear-gradient(135deg, {PALE_BLUE} 0%, #ffffff 100%)"},
)

overview_modal = dbc.Modal(
    [
        dbc.ModalHeader(dbc.ModalTitle("Overview: Data, Pipeline and Methods")),
        dbc.ModalBody(dcc.Markdown(TECH_OVERVIEW_MD, className="tech-overview px-3")),
        dbc.ModalFooter(
            dbc.Button("Close", id="overview-close", n_clicks=0,
                       style={"backgroundColor": INDIGO, "borderColor": INDIGO})
        ),
    ],
    id="overview-modal",
    size="xl",
    scrollable=True,
    is_open=False,
)

app.layout = dbc.Container(
    [
        overview_modal,
        dbc.Row(
            [
                dbc.Col(title_block, md=4, className="mb-3"),
                dbc.Col(map_block, md=8, className="mb-3"),
            ],
            className="g-3",
        ),
        dbc.Row(id="kpi-cards", className="g-3 mb-3"),
        dbc.Row(
            [
                dbc.Col(dcc.Graph(id="bar-chart", config={"displayModeBar": False}), md=6),
                dbc.Col(dcc.Graph(id="gauge-chart", config={"displayModeBar": False}), md=6),
            ],
            className="g-3 mb-3",
        ),
        dbc.Row(
            [
                dbc.Col(dcc.Graph(id="scatter-chart", config={"displayModeBar": False}), md=6),
                dbc.Col(dcc.Graph(id="rankings-chart", config={"displayModeBar": False}), md=6),
            ],
            className="g-3 mb-3",
        ),
        dbc.Row(
            dbc.Col(
                [
                    html.P(
                        "Small state figures may have high sampling error.",
                        className="small text-muted mb-1",
                    ),
                    html.P(
                        "Overqualification rate suppressed when employed sample < 100,000. ",
                        className="small text-muted mb-1",
                    ),
                    html.P(
                        "Overqualification uses the realized-matches method: workers whose "
                        "years of schooling exceed their occupation's national weighted mean "
                        "by more than 1 SD are flagged; occupations with <30 observations "
                        "are excluded.",
                        className="small text-muted mb-1",
                    ),
                    html.P(
                        f"BLS validation (Table A-15 NSA, CPS {MONTH_RANGE}): "
                        f"U-3 CPS {NATIONAL['u3']:.2f}% vs BLS NSA avg {BLS_NSA_U3_AVG:.2f}%; "
                        f"U-6 CPS {NATIONAL['u6']:.2f}% vs BLS NSA avg {BLS_NSA_U6_AVG:.2f}% "
                        "(dark dotted lines on the bar chart mark BLS reference levels).",
                        className="small text-muted mb-0",
                    ),
                ]
            ),
            className="mt-3 pt-3 border-top",
        ),
    ],
    fluid=True,
    className="py-4 px-2",
    style={"background": f"linear-gradient(135deg, {PALE_BLUE} 0%, {PALE_INDIGO} 100%)"},
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@app.callback(
    Output("state-dropdown", "value"),
    Input("choropleth", "clickData"),
    prevent_initial_call=True,
)
def sync_dropdown_from_map(click_data):
    """Clicking a state on the map selects it in the dropdown."""
    if not click_data or "points" not in click_data:
        return dash.no_update
    abbr = click_data["points"][0].get("location")
    if not abbr:
        return dash.no_update
    abbr_to_fips = {v: k for k, v in FIPS_TO_ABBR.items()}
    fips = abbr_to_fips.get(abbr)
    if fips is None:
        return dash.no_update
    return fips


@app.callback(
    Output("choropleth", "figure"),
    Input("map-metric", "value"),
    Input("state-dropdown", "value"),
)
def update_map(metric_key, selected_fips):
    return build_choropleth(metric_key, selected_fips)


@app.callback(
    Output("kpi-cards", "children"),
    Output("bar-chart", "figure"),
    Output("gauge-chart", "figure"),
    Output("scatter-chart", "figure"),
    Output("rankings-chart", "figure"),
    Input("state-dropdown", "value"),
    Input("map-metric", "value"),
)
def update_panels(selected_fips, metric_key):
    row = get_row(selected_fips)
    cards = build_kpi_cards(row)
    return (
        cards,
        build_bar(row),
        build_gauge(row),
        build_scatter(selected_fips),
        build_rankings(selected_fips, metric_key),
    )


@app.callback(
    Output("overview-modal", "is_open"),
    Input("overview-open", "n_clicks"),
    Input("overview-close", "n_clicks"),
    State("overview-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_overview(_open_clicks: int, _close_clicks: int, is_open: bool) -> bool:
    return not is_open


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8051, debug=True)
