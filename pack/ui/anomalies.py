"""Exploratory weekly severity diagnostics for the selected forecast cohort."""

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

from pack.data_access import load_anchor_cohort_case_data
from pack.ui.components import make_grid
from pack.ui.styles import BG_COLOR


def weekly_case_severity(cases):
    if cases.empty:
        return pd.DataFrame()
    data = cases.copy()
    data["week"] = data["erstes_ladedatum"].dt.to_period("W-SUN").dt.start_time
    weekly = data.groupby("week").agg(
        cases=("lueckentage", "size"),
        days=("lueckentage", "sum"),
        median=("lueckentage", "median"),
        largest=("lueckentage", "max"),
    )
    weekly = weekly.reindex(pd.date_range(weekly.index.min(), weekly.index.max(), freq="W-MON"))
    weekly["mean"] = weekly["days"].div(weekly["cases"])
    weekly["largest_share"] = weekly["largest"].div(weekly["days"].replace(0, float("nan"))) * 100
    cutoff = cases["erstes_ladedatum"].max()
    weekly["complete"] = weekly.index + pd.Timedelta(days=6) <= cutoff
    # Retrospective comparison across the displayed cohort, including later weeks.
    reference = weekly.loc[weekly["complete"], "mean"].dropna()
    q1, q3 = reference.quantile([.25, .75])
    weekly["reference"] = reference.median()
    weekly["upper"] = q3 + 3.0 * (q3 - q1) if len(reference) >= 4 else float("nan")
    weekly["flagged"] = weekly["mean"].gt(weekly["upper"]) & weekly["complete"]
    return weekly


def build_severity_section(sachverhalt, year):
    cases = load_anchor_cohort_case_data(sachverhalt, year)
    weekly = weekly_case_severity(cases)
    if weekly.empty:
        return html.Div("Für diese Auswahl sind keine Falldaten verfügbar.")

    fig = go.Figure()
    hover = (
        "Woche ab %{x|%d.%m.%Y}<br>"
        "<b>%{y:.1f} Lückentage je neuem Fall</b><br>"
        "Neue Fälle: %{customdata[0]:.0f}<br>"
        "Von neuen Fällen eingebrachte Lückentage: %{customdata[1]:.0f}<extra></extra>"
    )
    fig.add_scatter(
        x=weekly.index, y=weekly["mean"], mode="lines",
        name="Durchschnitt je neuem Fall", line={"color": "#167c9c", "width": 2.8},
        fill="tozeroy", fillcolor="rgba(22,124,156,0.07)",
        connectgaps=False, customdata=weekly[["cases", "days"]].to_numpy(),
        hovertemplate=hover,
    )
    flagged = weekly.loc[weekly["flagged"]]
    fig.add_scatter(
        x=flagged.index, y=flagged["mean"], mode="markers",
        marker={"color": "#e88b35", "size": 11, "line": {"color": "#ffffff", "width": 2}},
        name="Auffällige Woche",
        customdata=flagged[["cases", "days"]].to_numpy(), hovertemplate=hover,
    )
    fig.update_layout(
        template="plotly_white", height=390,
        paper_bgcolor=BG_COLOR, plot_bgcolor=BG_COLOR,
        font={"family": "Georgia, 'Times New Roman', serif", "color": "#304b5b", "size": 15},
        hovermode="closest",
        hoverlabel={"bgcolor": "#f0f7f7", "bordercolor": "#b9d2da",
                    "font": {"family": "Georgia, 'Times New Roman', serif",
                             "color": "#243b4b", "size": 14}},
        xaxis_title="Woche der ersten Ladung",
        yaxis_title="Lückentage je neuem Fall (Durchschnitt)",
        yaxis={"rangemode": "tozero", "gridcolor": "#cfdee3", "zeroline": False},
        xaxis={"showgrid": False, "tickformat": "%d.%m.%Y", "nticks": 7,
               "showline": True, "linecolor": "#dce8ed"},
        margin={"l": 65, "r": 25, "t": 60, "b": 55},
        legend={"orientation": "h", "y": 1.16, "x": 0, "font": {"size": 12}},
    )
    rows = []
    for week, row in flagged.sort_index(ascending=False).iterrows():
        rows.append({
            "week": week.strftime("%d.%m.%Y"),
            "cases": int(row["cases"]), "days": round(row["days"], 2),
            "mean": round(row["mean"], 2), "median": round(row["median"], 2),
            "share": round(row["largest_share"], 1),
            "note": "Kleine Fallzahl" if row["cases"] < 10 else "Prüfung empfohlen",
        })
    columns = [
        {"headerName": label, "field": field, "minWidth": width}
        for field, label, width in [
            ("week", "Woche ab", 130), ("cases", "Neue Fälle", 120),
            ("days", "Lückentage neuer Fälle", 180), ("mean", "Durchschnitt je neuem Fall", 200),
            ("median", "Median je neuem Fall", 180), ("share", "Größter Fall (%)", 150),
            ("note", "Hinweis", 180),
        ]
    ]
    return html.Div([
        html.Div(f"Ankerjahr: {year} · Sachverhalt: {sachverhalt}", className="severity-selection"),
        html.P(
            "Neue Fälle bringen zusätzliche Lückentage mit. Die Linie zeigt, wie viele Tage "
            "die erstmals geladenen Fälle einer Woche im Durchschnitt eingebracht haben. "
            "Berechnung: Summe der Lückentage dieser neuen Fälle ÷ Anzahl dieser Fälle. "
            "Orange markiert sind Wochen, deren neue Fälle im Durchschnitt ungewöhnlich viele "
            "Lückentage mitgebracht haben.",
            className="severity-description",
        ),
        dcc.Graph(figure=fig, config={"displaylogo": False, "displayModeBar": "hover"}),
        html.Details([
            html.Summary("Wie werden die orangefarbenen Punkte bestimmt?"),
            html.P(
                "Vergleich über den gesamten ausgewählten Zeitraum, einschließlich späterer Wochen. "
                "Bei mindestens 4 abgeschlossenen Wochen werden Werte über dem oberen Quartil plus "
                "3 Quartilsabständen markiert. Dies sind Prüfhinweise. Die letzte möglicherweise unvollständige Woche "
                "wird nicht markiert. Kleine Fallzahlen können den Durchschnitt stark verändern."
            ),
        ], style={"marginBottom": "12px"}),
        make_grid("forecast-severity-grid", rows, columns, auto_height=True)
        if rows else html.Div("Keine auffälligen Wochen nach dieser Regel."),
    ], className="severity-panel")
