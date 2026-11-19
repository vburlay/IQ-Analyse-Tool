"""Exploratory weekly severity diagnostics for the selected forecast cohort."""

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

from pack.data_access import load_anchor_cohort_case_data
from pack.ui.components import make_grid
from pack.ui.styles import BG_COLOR


def weekly_case_severity(cases, start=None, end=None):
    if cases.empty:
        return pd.DataFrame()
    data = cases.copy()
    data["week"] = data["erstes_ladedatum"].dt.to_period("W-SUN").dt.start_time
    cutoff = cases["erstes_ladedatum"].max()
    if start is not None:
        data = data.loc[data["week"] >= pd.Timestamp(start)]
    if end is not None:
        data = data.loc[data["week"] <= pd.Timestamp(end)]
        cutoff = min(cutoff, pd.Timestamp(end))
    if data.empty:
        return pd.DataFrame()
    weekly = data.groupby("week").agg(
        cases=("lueckentage", "size"),
        days=("lueckentage", "sum"),
        median=("lueckentage", "median"),
        largest=("lueckentage", "max"),
    )
    weekly = weekly.reindex(pd.date_range(weekly.index.min(), weekly.index.max(), freq="W-MON"))
    weekly["mean"] = weekly["days"].div(weekly["cases"])
    weekly["largest_share"] = weekly["largest"].div(weekly["days"].replace(0, float("nan"))) * 100
    weekly["complete"] = weekly.index + pd.Timedelta(days=6) <= cutoff
    # Retrospective comparison across the displayed cohort, including later weeks.
    reference = weekly.loc[weekly["complete"], "mean"].dropna()
    q1, q3 = reference.quantile([.25, .75])
    weekly["reference"] = reference.median()
    weekly["upper"] = q3 + 3.0 * (q3 - q1) if len(reference) >= 4 else float("nan")
    weekly["mean_flagged"] = weekly["mean"].gt(weekly["upper"]) & weekly["complete"]
    totals = weekly.loc[weekly["complete"], "days"].dropna()
    total_q1, total_q3 = totals.quantile([.25, .75])
    weekly["days_upper"] = (
        total_q3 + 3.0 * (total_q3 - total_q1) if len(totals) >= 4 else float("nan")
    )
    weekly["days_flagged"] = weekly["days"].gt(weekly["days_upper"]) & weekly["complete"]
    case_q1, case_q3 = data["lueckentage"].quantile([.25, .75])
    case_upper = case_q3 + 3.0 * (case_q3 - case_q1)
    data["large_case"] = data["lueckentage"].gt(case_upper) & data["lueckentage"].notna()
    weekly["large_cases"] = data.groupby("week")["large_case"].sum().reindex(weekly.index).fillna(0).astype(int)
    weekly["case_flagged"] = weekly["large_cases"].gt(0)
    weekly["flagged"] = weekly["days_flagged"]
    weekly["volume_flagged"] = weekly["days_flagged"]
    return weekly


def build_severity_section(sachverhalt, year, start=None, end=None):
    cases = load_anchor_cohort_case_data(sachverhalt, year)
    weekly = weekly_case_severity(cases, start=start, end=end)
    if weekly.empty:
        return html.Div("Für diese Auswahl sind keine Falldaten verfügbar.")

    fig = go.Figure()
    hover = (
        "Woche ab %{x|%d.%m.%Y}<br>"
        "<b>Neue Lückentage: %{y:.0f}</b><br>"
        "Neue Fälle: %{customdata[0]:.0f}<br>"
        "Lückentage des größten Falls: %{customdata[1]:.0f}<extra></extra>"
    )
    fig.add_scatter(
        x=weekly.index, y=weekly["days"], mode="lines",
        line={"color": "#167c9c", "width": 2.4},
        name="Neue Lückentage je Woche", connectgaps=False,
        customdata=weekly[["cases", "largest"]].to_numpy(), hovertemplate=hover,
    )
    points = weekly.loc[weekly["days_flagged"]]
    fig.add_scatter(
        x=points.index, y=points["days"], mode="markers",
        marker={"color": "#e88b35", "size": 11, "line": {"color": "#ffffff", "width": 2}},
        name="Ungewöhnlich hoher Zuwachs",
        customdata=points[["cases", "largest"]].to_numpy(), hovertemplate=hover,
    )
    flagged = weekly.loc[weekly["flagged"]]
    visible_start = pd.Timestamp(start) if start is not None else weekly.index.min()
    visible_end = min(pd.Timestamp(end), weekly.index.max()) if end is not None else weekly.index.max()
    if visible_end <= visible_start:
        visible_end = visible_start + pd.Timedelta(days=1)
    fig.update_xaxes(
        range=[visible_start, visible_end], showgrid=False, tickformat="%m.%Y",
        nticks=7, showline=True, linecolor="#dce8ed",
    )
    fig.update_xaxes(title_text="Woche der ersten Ladung")
    fig.update_yaxes(rangemode="tozero", gridcolor="#cfdee3", zeroline=False)
    fig.update_yaxes(title_text="Neue Lückentage je Woche")
    fig.update_layout(
        template="plotly_white", height=390,
        paper_bgcolor=BG_COLOR, plot_bgcolor=BG_COLOR,
        font={"family": "Georgia, 'Times New Roman', serif", "color": "#304b5b", "size": 14},
        hovermode="closest",
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        legend={"orientation": "h", "y": 1.15, "x": 0},
    )
    rows = []
    for week, row in flagged.sort_index(ascending=False).iterrows():
        rows.append({
            "week": week.strftime("%d.%m.%Y"),
            "cases": int(row["cases"]), "days": round(row["days"], 2),
            "mean": round(row["mean"], 2), "median": round(row["median"], 2),
            "share": round(row["largest_share"], 1),
            "largest": round(row["largest"], 2),
            "large_cases": int(row["large_cases"]),
            "reason": "Ungewöhnlich hoher Wochenzuwachs",
            "note": "Kleine Fallzahl" if row["cases"] < 10 else "Prüfung empfohlen",
        })
    columns = [
        {"headerName": label, "field": field, "minWidth": width}
        for field, label, width in [
            ("week", "Woche ab", 130), ("cases", "Neue Fälle", 120),
            ("days", "Lückentage neuer Fälle", 180),
            ("share", "Größter Fall (%)", 150),
            ("largest", "Lückentage größter Fall", 180),
            ("reason", "Auffälligkeit", 190), ("note", "Hinweis", 180),
        ]
    ]
    return html.Div([
        html.Div(f"Ankerjahr: {year} · Sachverhalt: {sachverhalt}", className="severity-selection"),
        html.P(
            "Die Linie zeigt, wie viele zusätzliche Lückentage die neuen Fälle je Woche "
            "mitgebracht haben. Orange Punkte markieren außergewöhnlich hohe Wochensummen. "
            "Die Tabelle zeigt die betroffenen Wochen und den Beitrag des größten Falls.",
            className="severity-description",
        ),
        dcc.Graph(figure=fig, config={"displaylogo": False, "displayModeBar": "hover"}),
        html.Details([
            html.Summary("Wie werden die orangefarbenen Punkte bestimmt?"),
            html.P(
                "Verglichen werden die Wochensummen im angezeigten tatsächlichen Zeitraum. "
                "Bei mindestens 4 abgeschlossenen Wochen gilt: oberes Quartil plus "
                "3 Quartilsabstände. Nur Wochensummen über diesem Grenzwert werden markiert. "
                "Die letzte möglicherweise unvollständige Woche bleibt ohne Markierung. "
                "Ein einzelner großer Fall allein löst keine Wochenmarkierung aus."

            ),
        ], style={"marginBottom": "12px"}),
        make_grid("forecast-severity-grid", rows, columns, auto_height=True)
        if rows else html.Div("Keine auffälligen Wochen nach dieser Regel."),
    ], className="severity-panel")
