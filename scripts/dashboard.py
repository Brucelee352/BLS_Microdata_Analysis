# -*- coding: utf-8 -*-
"""U.S. Underemployment Dashboard.

A self-contained Plotly Dash app that visualizes state-level underemployment
measures derived from BLS CPS Basic Monthly Microdata (Jan-May 2026).

Data is loaded once at startup from the project's DuckDB database into a pandas
DataFrame; all callbacks operate on that in-memory frame.

Run (from the worktree root):
    uv run python scripts/dashboard.py
Then open http://localhost:8050
"""

from __future__ import annotations

from pathlib import Path

import dash
import dash_bootstrap_components as dbc
import duckdb
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent

# Sequential color scale used across the choropleth (light = low, dark = high).
COLOR_SCALE = "YlOrRd"

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

# BLS Table A-15 NSA averages (Jan-May 2026) used as reference benchmarks.
BLS_NSA_U3_AVG = 4.36
BLS_NSA_U6_AVG = 8.10

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
                    marker_line_color="#0d1b2a",
                    marker_line_width=2.5,
                    hoverinfo="skip",
                )
            )

    fig.update_layout(
        geo=dict(scope="usa", lakecolor="white", bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=10, b=0),
        height=420,
        paper_bgcolor="rgba(240,246,255,0.4)",
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

    fig = go.Figure()
    if not is_national:
        fig.add_trace(
            go.Bar(
                name=state_label,
                x=measures,
                y=state_vals,
                marker_color="#2c7fb8",
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
            marker_color="#9ecae1" if not is_national else "#2c7fb8",
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
            line=dict(color="#fd8d3c", width=2, dash="dot"),
            xref="x", yref="y",
        )
        fig.add_annotation(
            x=idx, y=ref_val,
            xref="x", yref="y",
            text=f"BLS NSA: {ref_val:.2f}%",
            showarrow=False,
            font=dict(size=9, color="#e6550d"),
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
        paper_bgcolor="rgba(240,246,255,0.4)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(title="value (% or hrs)", gridcolor="#e9ecef"),
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
                bar=dict(color="#2c7fb8"),
                steps=[
                    dict(range=[0, nat_u6], color="#d4edda"),
                    dict(range=[nat_u6, axis_max], color="#f8d7da"),
                ],
                threshold=dict(
                    line=dict(color="#0d1b2a", width=3),
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
        paper_bgcolor="rgba(240,246,255,0.4)",
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
    """Scatter of all states: x=U-3, y=Overqualification Rate."""
    states = STATES.copy()
    sel = int(selected_fips)

    is_sel = states["state_fips"] == sel
    others = states[~is_sel]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=others["u3"],
            y=others["overqualification_rate"],
            mode="markers",
            marker=dict(size=9, color="#9ecae1", line=dict(color="#4292c6", width=0.5)),
            text=others["state_name"],
            hovertemplate="<b>%{text}</b><br>U-3: %{x:.2f}%<br>Overqual: %{y:.2f}%<extra></extra>",
            name="States",
        )
    )

    fig.add_vline(x=float(NATIONAL["u3"]), line_dash="dot", line_color="#adb5bd")
    fig.add_hline(y=float(NATIONAL["overqualification_rate"]), line_dash="dot", line_color="#adb5bd")

    sel_rows = states[is_sel]
    if not sel_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=sel_rows["u3"],
                y=sel_rows["overqualification_rate"],
                mode="markers+text",
                marker=dict(size=16, color="#e6550d", line=dict(color="#0d1b2a", width=1.5)),
                text=sel_rows["state_abbr"],
                textposition="top center",
                hovertemplate="<b>%{customdata}</b><br>U-3: %{x:.2f}%<br>Overqual: %{y:.2f}%<extra></extra>",
                customdata=sel_rows["state_name"],
                name="Selected",
            )
        )

    fig.update_layout(
        title=dict(text="States: U-3 vs Overqualification Rate", x=0.5, font=dict(size=14)),
        margin=dict(l=10, r=10, t=70, b=10),
        height=460,
        showlegend=False,
        paper_bgcolor="rgba(240,246,255,0.4)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="U-3 (%)", gridcolor="#e9ecef"),
        yaxis=dict(title="Overqualification Rate (%)", gridcolor="#e9ecef"),
    )
    return fig


def build_rankings(selected_fips: int, metric_key: str) -> go.Figure:
    """Horizontal bar chart of all states ranked by the selected map metric."""
    col, axis_label, _ = MAP_METRICS[metric_key]
    sel = int(selected_fips)

    states = STATES.copy().sort_values(col, ascending=True)
    nat_val = float(NATIONAL[col])

    colors = [
        "#e6550d" if int(f) == sel else "#2c7fb8"
        for f in states["state_fips"]
    ]

    fig = go.Figure(
        go.Bar(
            x=states[col],
            y=states["state_name"],
            orientation="h",
            marker_color=colors,
            customdata=states["state_name"],
            hovertemplate="<b>%{customdata}</b><br>" + axis_label + ": %{x:.2f}%<extra></extra>",
        )
    )

    fig.add_vline(
        x=nat_val,
        line_dash="dash",
        line_color="#0d1b2a",
        annotation_text=f"National {nat_val:.2f}%",
        annotation_position="top",
    )

    fig.update_layout(
        title=dict(text=f"State Rankings: {axis_label}", x=0.5, font=dict(size=14)),
        margin=dict(l=10, r=10, t=70, b=10),
        height=520,
        showlegend=False,
        paper_bgcolor="rgba(240,246,255,0.4)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title=axis_label, gridcolor="#e9ecef"),
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
            dbc.Card(dbc.CardBody(body, className="py-3 px-3"), className="shadow-sm h-100", style={"background": "linear-gradient(135deg, #f0f6ff 0%, #ffffff 100%)"}),
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
# App layout
# ---------------------------------------------------------------------------

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="U.S. Underemployment Dashboard",
)
server = app.server

title_block = dbc.Card(
    dbc.CardBody(
        [
            html.H2("U.S. Underemployment Report", className="fw-bold mb-3"),
            html.P(
                "Author: Bruce A. Lee",
                className="small text-secondary mb-2",
            ),
            html.P(
                "Dataset: CPS Basic Monthly Microdata | Jan-May 2026",
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
    style={"background": "linear-gradient(135deg, #ffffff 0%, #fff0f0 100%)"},
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
    style={"background": "linear-gradient(135deg, #f0f6ff 0%, #ffffff 100%)"},
)

app.layout = dbc.Container(
    [
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
                        "education (PEEDUCA) exceeds their occupation mean by >1 SD are flagged;"
                        "occupations with <30 observations excluded.",
                        className="small text-muted mb-1",
                    ),
                    html.P(
                        "BLS validation (Table A-15 NSA, Jan-May 2026): "
                        "U-3 CPS 4.36% = BLS NSA avg 4.36% (exact match). "
                        "U-6 CPS 7.44% vs BLS NSA avg 8.10% (-0.66pp; "
                        "orange dotted lines on bar chart mark BLS reference levels).",
                        className="small text-muted mb-0",
                    ),
                ]
            ),
            className="mt-3 pt-3 border-top",
        ),
    ],
    fluid=True,
    className="py-4 px-2",
    style={"background": "linear-gradient(135deg, #f0f4ff 0%, #fff5f5 100%)"},
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8050, debug=True)
