from datetime import date

import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc

from pack.ui.styles import *
from pack.ui.components import (
    kpi_card,
    make_grid,
)

from pack.services.monitoring_service import (
    get_monitoring_kpis,
    get_monitoring_stand_text,
    get_monitoring_grid_data,
    get_monitoring_chart_data,
)


def build_monitoring_main_fig(
    year: int | None = None,
    date_basis: str = "elad",
):
    plot_df = get_monitoring_chart_data(year=year, date_basis=date_basis)

    fig = go.Figure()
    if plot_df.empty:
        return fig

    if date_basis == "ipl":
        fig.add_scatter(
            x=plot_df["x"], y=plot_df["TAGEN"], mode="lines+markers",
            name="Lückentage je IPL", line={"width": 3}, marker={"size": 8},
            hovertemplate="IPL: %{x|%d.%m.%Y}<br>Lückentage: %{y:,.0f}<extra></extra>",
        )
    else:
        dates = pd.to_datetime(plot_df["x"])
        week_start = dates.dt.to_period("W-SUN").dt.start_time
        plot_df = plot_df.assign(week_start=week_start).set_index("week_start").reindex(
            pd.date_range(week_start.min(), week_start.max(), freq="W-MON")
        )
        week_start = pd.Series(plot_df.index, index=plot_df.index)
        selected_year = int(year) if year is not None else date.today().year
        period_start = week_start.clip(lower=pd.Timestamp(selected_year, 1, 1))
        period_end = (week_start + pd.Timedelta(days=6)).clip(
            upper=pd.Timestamp(selected_year, 12, 31)
        )
        fig.add_scatter(
            x=week_start + pd.Timedelta(days=3), y=plot_df["TAGEN"],
            mode="lines+markers", connectgaps=False,
            line={"color": ACCENT, "width": 2.5},
            marker={"color": ACCENT, "size": 6}, name="Lückentage je Woche",
            customdata=list(zip(
                period_start.dt.strftime("%d.%m.%Y"),
                period_end.dt.strftime("%d.%m.%Y"),
                plot_df["Kalendertage"],
            )),
            hovertemplate=(
                "Zeitraum: %{customdata[0]} – %{customdata[1]}<br>"
                "<b>Lückentage: %{y:,.0f}</b><br>"
                "Tage mit erstmals geladenen Datensätzen: %{customdata[2]}"
                "<extra></extra>"
            ),
        )

    fig.update_layout(
        template="plotly_white",
        height=520,
        font=dict(size=20),
        plot_bgcolor=BG_COLOR,
        paper_bgcolor=BG_COLOR,
        margin=dict(t=60, b=60, l=80, r=50),
        showlegend=False,
        legend=dict(
            orientation="h",
            x=1,
            xanchor="right",
            y=1.08,
            yanchor="bottom",
        ),
    )

    fig.update_yaxes(
        title_text=(
            "Lückentage je IPL"
            if date_basis == "ipl"
            else "Lückentage je Woche"
        ),
        title_font=dict(size=20),
        tickfont=dict(size=15),
        showgrid=True,
        gridcolor="rgba(44, 62, 80, 0.12)",
        zeroline=False,
    )
    fig.update_xaxes(
        title_text="IPL-Datum" if date_basis == "ipl" else "Woche der ersten Ladung",
        title_font=dict(size=20),
        tickfont=dict(size=15),
        tickangle=-35,
        tickformat="%Y-%m",
    )

    return fig


def render_monitoring_tab(get_refresh_state_fn):
    date_basis = "elad"
    kpi = get_monitoring_kpis(date.today().year, date_basis)

    monitoring_rows, monitoring_cols = get_monitoring_grid_data(
        date.today().year, date_basis
    )
    refresh_state = get_refresh_state_fn()

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                "Anmeldung bei SAP HANA (RBP)",
                                className="hana-login-title",
                            ),
                            dcc.Input(
                                id="hana-username",
                                type="text",
                                placeholder="Benutzername",
                                autoComplete="username",
                                className="hana-login-input",
                            ),
                            dcc.Input(
                                id="hana-password",
                                type="password",
                                placeholder="Passwort",
                                autoComplete="current-password",
                                className="hana-login-input",
                            ),
                            html.Button(
                                "Anmelden und aktualisieren",
                                id="refresh-data-btn",
                                n_clicks=0,
                                style=REFRESH_BUTTON_STYLE,
                            ),
                        ],
                        className="monitoring-toolbar-login",
                    ),
                    html.Div(
                        [
                            html.Div(
                                id="monitoring-stand-line",
                                children=get_monitoring_stand_text(
                                    refresh_state["last_stand"] if date_basis == "elad" else None,
                                    date_basis,
                                ),
                                style={
                                    **MONITORING_INFO_LINE_STYLE,
                                    "width": "auto",
                                    "margin": "0",
                                    "fontSize": "21px",
                                    "justifyContent": "flex-start",
                                },
                                className="monitoring-toolbar-stand",
                            ),
                            html.Div(
                                id="hana-login-message",
                                className="hana-login-message",
                                role="status",
                            ),
                        ],
                        className="monitoring-toolbar-status",
                    ),
                    html.Div(
                        [
                            dcc.RadioItems(
                                id="monitoring-date-basis",
                                options=[
                                    {"label": "Erstes Ladedatum", "value": "elad"},
                                    {"label": "IPL", "value": "ipl"},
                                ],
                                value="elad",
                                inline=True,
                                labelStyle={"marginRight": "16px", "cursor": "pointer"},
                                style={
                                    "whiteSpace": "nowrap",
                                    "fontSize": "16px",
                                    "color": TEXT,
                                    "height": "34px",
                                    "boxSizing": "border-box",
                                    "padding": "0 14px",
                                    "display": "flex",
                                    "alignItems": "center",
                                    "background": "white",
                                    "border": "1px solid #8893b2",
                                    "borderRadius": "8px",
                                },
                            ),
                            dcc.Dropdown(
                                id="monitoring-year-filter",
                                className="monitoring-year-dropdown",
                                options=[
                                    {"label": str(year), "value": year}
                                    for year in range(date.today().year, date.today().year - 3, -1)
                                ],
                                value=date.today().year,
                                placeholder="Ankerjahr",
                                clearable=False,
                                style={"width": "180px", "height": "34px"},
                            ),
                        ],
                        className="monitoring-toolbar-filters",
                    ),
                ],
                className="monitoring-toolbar",
            ),
            html.Div(
                [
                    kpi_card(
                        "Potential",
                        kpi["potential"],
                        value_id="monitoring-kpi-potential",
                    ),
                    kpi_card(
                        "Ertrag",
                        kpi["ertrag"],
                        value_id="monitoring-kpi-ertrag",
                    ),
                    kpi_card(
                        "Ertragsanteil",
                        kpi["ertragsanteil"],
                        value_id="monitoring-kpi-ertragsanteil",
                    ),
                    kpi_card(
                        "Ertragswert (11 € je Lückentag)",
                        kpi["euro_pro_tag"],
                        value_id="monitoring-kpi-euro-pro-tag",
                    ),
                    kpi_card(
                        "Erwartete Lückentage",
                        kpi["potential_ertragsanteil"],
                        value_id="monitoring-kpi-potential-ertragsanteil",
                    ),
                ],
                style=KPI_CONTAINER_STYLE,
            ),
            html.Div(
                [
                    html.Div(
                        make_grid(
                            "monitoring-risk-grid",
                            monitoring_rows,
                            monitoring_cols,
                            auto_height=True,
                        ),
                        style=CHART_CARD_STYLE,
                    ),
                ],
                style=SECTION_STYLE,
            ),
            html.Div(
                [
                    html.Div(
                        "Aktuelle Entwicklung der Lückentage je IPL"
                        if date_basis == "ipl"
                        else "Lückentage nach Woche der ersten Ladung",
                        id="monitoring-chart-title",
                        style=SUBTITLE_STYLE,
                    ),
                    html.Div(
                        (
                            "Periodenwert je IPL (nicht kumuliert). Der Wert kann zwischen zwei IPL-Terminen steigen oder sinken."
                            if date_basis == "ipl"
                            else "Ein Punkt zeigt die Lückentage der in dieser Woche erstmals geladenen Datensätze. Wochenwerte, nicht kumuliert. Wochen ohne Datensätze erscheinen als Lücken in der Linie."
                        ),
                        id="monitoring-chart-description",
                        style={
                            "fontSize": "15px",
                            "color": TEXT_MUTED,
                            "marginBottom": "10px",
                        },
                    ),
                    html.Div(
                        dcc.Graph(
                            id="monitoring-main-graph",
                            figure=build_monitoring_main_fig(
                                year=date.today().year,
                                date_basis=date_basis,
                            ),
                        ),
                        style=CHART_CARD_STYLE,
                    ),
                ],
                style=SECTION_STYLE,
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Datenverwaltung", className="data-management-title"),
                                    html.Div(
                                        "Entfernt ausschließlich die lokal gespeicherten DuckDB-Daten.",
                                        className="data-management-description",
                                    ),
                                ]
                            ),
                            html.Button(
                                "DuckDB leeren",
                                id="clear-local-data-btn",
                                n_clicks=0,
                                className="danger-outline-button",
                            ),
                        ],
                        className="data-management-row",
                    ),
                ],
                style=SECTION_STYLE,
            ),
        ],
        style=PAGE_STYLE,
    )
