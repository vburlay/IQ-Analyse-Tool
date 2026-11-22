from typing import Optional
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc

from pack.forecast_runs import ALL_ANCHOR_YEARS, load_forecast_runs
from pack.config import ANALYSIS_START_YEAR
from pack.ui.styles import *
from pack.ui.components import (
    section_title,
    make_grid,
)

from pack.services.forecast_service import (
    calculate_stichtag_forecast,
    prepare_anchor_forecast_plot_dataset,
)


CURRENT_RUN_MESSAGE = " (Die gelb markierte Zeile gehört zur Prognose in den Diagrammen.)"


def forecast_detail_grid_data(runs, current_run_id=None):
    def format_number(value):
        return f"{value:,}".replace(",", ".")

    def format_euro(value):
        return f"{value:,.2f}".translate(str.maketrans(",.", ".,")) + " €"

    def interval(lower, upper, format_value):
        if lower is None or upper is None:
            return "—"
        return f"{format_value(lower)} – {format_value(upper)}"

    rows = [
        {
            "run_id": run.run_id,
            "is_current": run.run_id == current_run_id,
            "BerechnetAm": run.created_at.isoformat(sep=" ", timespec="seconds"),
            "Datenstand": run.data_cutoff.strftime("%d.%m.%Y"),
            "Stichtag": run.stichtag.strftime("%d.%m.%Y"),
            "Ankerjahr": "all" if run.ankerjahr == ALL_ANCHOR_YEARS else str(run.ankerjahr),
            "Sachverhalt": run.sachverhalt,
            "Intervallniveau": f"{run.interval_level:.0%}",
            "FaelleAktuell": format_number(run.cases_actual),
            "FaelleNochErwartet": format_number(run.cases_expected),
            "FaellePrognose": format_number(run.cases_forecast),
            "FaelleIntervall": interval(run.cases_lower, run.cases_upper, format_number),
            "LueckentageAktuell": format_number(run.days_actual),
            "LueckentageNochErwartet": format_number(run.days_expected),
            "LueckentagePrognose": format_number(run.days_forecast),
            "LueckentageIntervall": interval(run.days_lower, run.days_upper, format_number),
            "ErtragAktuell": format_euro(run.revenue_actual),
            "ErtragNochErwartet": format_euro(run.revenue_expected),
            "ErtragPrognose": format_euro(run.revenue_forecast),
            "Prognoseintervall": interval(run.revenue_lower, run.revenue_upper, format_euro),
        }
        for run in runs
    ]
    column_defs = [
        *[
            {"headerName": label, "field": field, "minWidth": width,
             "headerClass": "header-datenstand", "cellDataType": "text"}
            for label, field, width in (
                ("Berechnet am", "BerechnetAm", 185),
                ("Datenstand", "Datenstand", 145),
                ("Stichtag", "Stichtag", 125),
                ("Ankerjahr", "Ankerjahr", 110),
                ("Sachverhalt", "Sachverhalt", 125),
                ("Intervallniveau", "Intervallniveau", 130),
            )
        ],
            {
                "headerName": "Fälle",
                "headerClass": "header-faelle",
                "children": [
                    {"headerName": "Aktuell", "field": "FaelleAktuell", "minWidth": 125, "flex": 1, "headerClass": "header-faelle-sub"},
                    {"headerName": "Noch erwartet", "field": "FaelleNochErwartet", "minWidth": 145, "flex": 1, "headerClass": "header-faelle-sub"},
                    {"headerName": "Zum Stichtag", "field": "FaellePrognose", "minWidth": 145, "flex": 1, "headerClass": "header-faelle-sub"},
                    {"headerName": "Prognoseintervall", "field": "FaelleIntervall", "minWidth": 180, "wrapText": False, "autoHeight": False, "headerClass": "header-faelle-sub"},
                ],
            },
            {
                "headerName": "Lückentage",
                "headerClass": "header-lueckentage",
                "children": [
                    {"headerName": "Aktuell", "field": "LueckentageAktuell", "minWidth": 135, "flex": 1, "headerClass": "header-lueckentage-sub"},
                    {"headerName": "Noch erwartet", "field": "LueckentageNochErwartet", "minWidth": 155, "flex": 1, "headerClass": "header-lueckentage-sub"},
                    {"headerName": "Zum Stichtag", "field": "LueckentagePrognose", "minWidth": 155, "flex": 1, "headerClass": "header-lueckentage-sub"},
                    {"headerName": "Prognoseintervall", "field": "LueckentageIntervall", "minWidth": 205, "flex": 1, "wrapText": False, "autoHeight": False, "headerClass": "header-lueckentage-sub"},
                ],
            },
            {
                "headerName": "Ertragswert",
                "headerClass": "header-ertragswert",
                "children": [
                    {"headerName": "Aktuell (€)", "field": "ErtragAktuell", "minWidth": 155, "flex": 1, "headerClass": "header-ertragswert-sub"},
                    {"headerName": "Noch erwartet (€)", "field": "ErtragNochErwartet", "minWidth": 185, "flex": 1, "headerClass": "header-ertragswert-sub"},
                    {"headerName": "Zum Stichtag (€)", "field": "ErtragPrognose", "minWidth": 180, "flex": 1, "headerClass": "header-ertragswert-sub"},
                    {"headerName": "Prognoseintervall (€)", "field": "Prognoseintervall", "minWidth": 340, "flex": 1, "wrapText": False, "autoHeight": False, "headerClass": "header-ertragswert-sub"},
                ],
            },
    ]
    return rows, column_defs


def prepare_anchor_forecast_display_data(
    cumulative_data: pd.DataFrame,
    selected_year: int | str,
    stichtag: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Limit the chart window without discarding the pre-year cumulative level."""
    data = cumulative_data.copy().sort_values("IPL_dt")
    target_date = pd.to_datetime(stichtag, errors="coerce")

    if selected_year in (None, "all"):
        start_date = data["IPL_dt"].min()
    else:
        start_date = pd.Timestamp(year=int(selected_year), month=1, day=1)

    if pd.isna(target_date):
        target_date = data["IPL_dt"].max()

    visible = data[
        (data["IPL_dt"] >= start_date) & (data["IPL_dt"] <= target_date)
    ].copy()

    if selected_year not in (None, "all") and not (visible["IPL_dt"] == start_date).any():
        earlier = data[data["IPL_dt"] < start_date]
        baseline_cases = (
            float(earlier["Kumulierte_Faelle"].iloc[-1]) if not earlier.empty else 0.0
        )
        baseline_days = (
            float(earlier["Kumulierte_Lueckentage"].iloc[-1]) if not earlier.empty else 0.0
        )
        baseline = pd.DataFrame(
            {
                "IPL_dt": [start_date],
                "Kumulierte_Faelle": [baseline_cases],
                "Kumulierte_Lueckentage": [baseline_days],
            }
        )
        visible = pd.concat([baseline, visible], ignore_index=True, sort=False)

    return visible, start_date, target_date


def build_anchor_forecast_figures(
    df_filtered: pd.DataFrame,
    forecast_result: Optional[dict] = None,
    stichtag: Optional[str] = None,
    selected_year: int | str = "all",
):
    """Build two independent figures with the same erstes-Ladedatum range."""
    if df_filtered.empty:
        return go.Figure(), go.Figure()

    full_data = df_filtered.copy().sort_values("IPL_dt")
    data, x_start, target_date = prepare_anchor_forecast_display_data(
        full_data, selected_year, stichtag,
    )
    cases_fig = go.Figure()
    days_fig = go.Figure()

    cases_fig.add_trace(
        go.Scatter(
            x=data["IPL_dt"], y=data["Kumulierte_Faelle"],
            mode="lines+markers", name="Ist-Wert",
            line=dict(width=3, color="#0b6fb3"), marker=dict(size=5),
            legendrank=1,
        )
    )
    days_fig.add_trace(
        go.Scatter(
            x=data["IPL_dt"], y=data["Kumulierte_Lueckentage"],
            mode="lines+markers", name="Ist-Wert",
            line=dict(width=3, color="#0b6fb3"), marker=dict(size=5),
            legendrank=1,
        )
    )

    if forecast_result and forecast_result.get("forecast") is not None:
        interval_level = int(forecast_result.get("intervallniveau", 80))
        case_future = forecast_result.get("case_future", pd.DataFrame())
        if not case_future.empty:
            case_future = case_future[
                (case_future["date"] >= x_start)
                & (case_future["date"] <= target_date)
            ]
            cases_fig.add_trace(
                go.Scatter(
                    x=case_future["date"], y=case_future["upper"], mode="lines",
                    line=dict(width=0), hoverinfo="skip", showlegend=False,
                )
            )
            cases_fig.add_trace(
                go.Scatter(
                    x=case_future["date"], y=case_future["lower"], mode="lines",
                    line=dict(width=0), fill="tonexty",
                    fillcolor="rgba(242, 142, 43, 0.18)",
                    name=f"{interval_level}-%-Prognoseintervall",
                    legendrank=3,
                )
            )
            cases_fig.add_trace(
                go.Scatter(
                    x=case_future["date"], y=case_future["forecast"],
                    mode="lines+markers", name="Prognose",
                    line=dict(width=3, color="#f28e2b"), marker=dict(size=6),
                    legendrank=2,
                )
            )

        future = forecast_result.get("future", pd.DataFrame())
        if not future.empty:
            future = future[
                (future["date"] >= x_start) & (future["date"] <= target_date)
            ]
            days_fig.add_trace(
                go.Scatter(
                    x=future["date"], y=future["upper"], mode="lines",
                    line=dict(width=0), hoverinfo="skip", showlegend=False,
                )
            )
            days_fig.add_trace(
                go.Scatter(
                    x=future["date"], y=future["lower"], mode="lines",
                    line=dict(width=0), fill="tonexty",
                    fillcolor="rgba(242, 142, 43, 0.18)",
                    name=f"{interval_level}-%-Prognoseintervall",
                    legendrank=3,
                )
            )
            days_fig.add_trace(
                go.Scatter(
                    x=future["date"], y=future["forecast"],
                    mode="lines+markers", name="Prognose",
                    line=dict(width=3, color="#f28e2b"), marker=dict(size=6),
                    legendrank=2,
                )
            )

    x_end = target_date
    figures = (
        (cases_fig, "Kumulierte Fallzahl", "Kumulierte eindeutige Fälle"),
        (
            days_fig,
            "Kumulierte Lückentage",
            "Kumulierte Lückentage",
        ),
    )
    for figure, title, y_title in figures:
        if pd.notna(target_date):
            figure.add_vline(
                x=target_date.timestamp() * 1000,
                line_dash="dot",
                line_color="#6b7785",
                annotation=None,
            )
            figure.add_annotation(
                x=target_date,
                y=0.5,
                xref="x",
                yref="paper",
                text="Stichtag",
                showarrow=False,
                textangle=-90,
                xanchor="center",
                yanchor="middle",
                xshift=-14,
                font=dict(size=15, color=TEXT),
            )
        figure.update_xaxes(
            title_text="Erstes Ladedatum", tickformat="%Y-%m", tickangle=-35,
            range=[x_start, x_end],
        )
        figure.update_yaxes(title_text=y_title, domain=[0.0, 0.82])
        figure.update_layout(
            title=dict(text=title, x=0.5, xanchor="center", y=0.97, yanchor="top"),
            height=540,
            plot_bgcolor=BG_COLOR,
            paper_bgcolor=BG_COLOR,
            font=dict(size=14),
            margin=dict(t=35, b=70, l=75, r=25),
            legend=dict(
                orientation="h", x=0.5, xanchor="center", y=0.89, yanchor="middle",
            ),
        )
    return cases_fig, days_fig


def anchor_graph_components(df_filtered, forecast_result, stichtag, selected_year):
    cases_fig, days_fig = build_anchor_forecast_figures(
        df_filtered, forecast_result, stichtag, selected_year,
    )
    return [
        html.Div(
            dcc.Graph(
                figure=cases_fig,
                config={"displayModeBar": "hover", "displaylogo": False},
            ),
            style={"flex": "1 1 520px", "minWidth": "0"},
        ),
        html.Div(
            dcc.Graph(
                figure=days_fig,
                config={"displayModeBar": "hover", "displaylogo": False},
            ),
            style={"flex": "1 1 520px", "minWidth": "0"},
        ),
    ]


def render_forecast_tab():
    default_sachverhalt = "000"
    default_year = pd.Timestamp.today().year
    df_init = prepare_anchor_forecast_plot_dataset(default_sachverhalt, default_year)
    last_date = pd.to_datetime(df_init.get("IPL"), errors="coerce").max() if not df_init.empty else pd.Timestamp.today()
    stichtag_default = (
        pd.Timestamp(year=pd.Timestamp.today().year + 1, month=2, day=1)
        + pd.offsets.MonthEnd(0)
    ).date()
    stichtag_max = (pd.Timestamp(stichtag_default) + pd.Timedelta(days=365)).date()
    initial_result = calculate_stichtag_forecast(
        default_sachverhalt,
        str(stichtag_default),
        80,
        default_year,
    )
    detail_rows, detail_cols = forecast_detail_grid_data(load_forecast_runs())

    return html.Div(
        [
            dcc.Store(id="forecast-form-id", data=str(uuid4())),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Stichtag", style={"fontWeight": "bold", "color": TEXT}),
                            dcc.DatePickerSingle(
                                id="forecast-stichtag", date=stichtag_default,
                                min_date_allowed=(last_date + pd.Timedelta(days=1)).date(),
                                max_date_allowed=stichtag_max,
                                display_format="DD.MM.YYYY",
                            ),
                        ],
                        style={**CONTROL_CARD_STYLE, "flex": "1", "minWidth": "220px"},
                    ),
                    html.Div(
                        [
                            html.Label("Intervallniveau", style={"fontWeight": "bold", "color": TEXT}),
                            dcc.Dropdown(
                                id="forecast-intervallniveau",
                                options=[{"label": f"{value} %", "value": value} for value in (80, 90, 95)],
                                value=80, clearable=False,
                            ),
                        ],
                        style={**CONTROL_CARD_STYLE, "flex": "1", "minWidth": "180px"},
                    ),
                    html.Div(
                        [
                            html.Label("Sachverhalt", style={"fontWeight": "bold", "color": TEXT}),
                            dcc.Dropdown(
                                id="forecast-sachverhalt",
                                options=[
                                    {"label": value, "value": value}
                                    for value in [
                                        "000",
                                        "004",
                                        "005",
                                        "01a",
                                        "01b",
                                        "01c",
                                        "04a",
                                        "04b",
                                        "04c",
                                        "all",
                                    ]
                                ],
                                value=default_sachverhalt,
                                placeholder="Sachverhalt auswählen",
                                clearable=False,
                            ),
                        ],
                        style={**CONTROL_CARD_STYLE, "flex": "1", "minWidth": "200px"},
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Ankerjahr",
                                style={"fontWeight": "bold", "color": TEXT},
                            ),
                            dcc.Dropdown(
                                id="forecast-year-filter",
                                options=[
                                    *[
                                        {"label": str(year), "value": year}
                                        for year in range(default_year, ANALYSIS_START_YEAR - 1, -1)
                                    ],
                                    {"label": f"Alle ab {ANALYSIS_START_YEAR}", "value": "all"},
                                ],
                                value=default_year,
                                clearable=False,
                            ),
                        ],
                        style={**CONTROL_CARD_STYLE, "flex": "1", "minWidth": "160px"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Prognose berechnen", id="forecast-calculate-btn", n_clicks=0,
                                style={**REFRESH_BUTTON_STYLE, "height": "42px", "fontSize": "15px"},
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                ],
                style=CONTROL_ROW_STYLE,
            ),
            html.Div(id="forecast-calculation-message", role="alert"),

            html.Div(
                [
                    html.Div(
                        f"Prognose für Fälle mit Bezug zum Ankerjahr {default_year}",
                        id="forecast-section-title",
                        style=SUBTITLE_STYLE,
                    ),
                    html.Div(
                        "Eindeutige Fälle, deren Ankerzeitraum das ausgewählte Jahr überschneidet. "
                        "Die Zeitachse zeigt das erste Ladedatum. Illustrative Prognose auf Basis "
                        "der lokal gespeicherten Daten.",
                        id="forecast-section-description",
                        style={
                            "fontSize": "15px",
                            "color": TEXT_MUTED,
                            "marginBottom": "10px",
                        },
                    ),
                    html.Div(
                        anchor_graph_components(
                            df_init, initial_result, str(stichtag_default), default_year,
                        ),
                        id="graph-forecast-container",
                        style={
                            **CHART_CARD_STYLE,
                            "display": "flex",
                            "gap": "14px",
                            "flexWrap": "wrap",
                        },
                    ),
                ],
                style=SECTION_STYLE,
            ),

            html.Details(
                [
                    html.Summary(
                        "Neue Fälle mit auffälligen Lückentagen",
                        style={**SUBTITLE_STYLE, "cursor": "pointer"},
                    ),
                    html.Div(id="forecast-severity-content", style=CHART_CARD_STYLE),
                ],
                id="forecast-severity-details",
                open=False,
                style=SECTION_STYLE,
            ),

            html.Div(
                [
                    html.Div(
                        [
                            "Prognosehistorie",
                            html.Em(
                                id="current-forecast-note",
                                role="status",
                                style={"fontSize": "0.8em", "fontWeight": "normal"},
                            ),
                        ],
                        style=SUBTITLE_STYLE,
                    ),
                    html.Div(
                        make_grid(
                            "forecast-detail-grid",
                            detail_rows,
                            detail_cols,
                            row_class_rules={"forecast-current-run": "params.data && params.data.is_current"},
                            get_row_id="params.data.run_id",
                        ),
                        style=CHART_CARD_STYLE,
                    ),
                ],
                style=SECTION_STYLE,
            ),
        ],
        style=PAGE_STYLE,
    )
