"""Data-quality workspace and evidence tables."""
from datetime import date, timedelta

import plotly.graph_objects as go
from dash import dcc, html
import dash_ag_grid as dag

from pack.config import ANALYSIS_START_YEAR, RAW_BESTAND_COLUMNS
from pack.ui.styles import PAGE_STYLE, CHART_CARD_STYLE, SECTION_STYLE, SUBTITLE_STYLE, BG_COLOR


QUALITY_GRID_HEIGHT = "clamp(680px, 72vh, 1100px)"


def render_quality_tab():
    layout = html.Div([
        dcc.Store(id="quality-year", data="all"),
        dcc.Store(id="quality-sv", data="all"),
        html.Details([
            html.Summary("Welche Daten werden geprüft?", style={"cursor": "pointer", "fontWeight": "600"}),
            html.Div(id="quality-scope-note", role="status", style={"marginTop": "8px"}),
        ], style={"fontSize": "13px", "color": "#607286", "marginBottom": "20px", "lineHeight": "1.6"}),
        dcc.Tabs(id="quality-subtab", value="overview", children=[
            dcc.Tab(label="Überblick", value="overview", children=[
                dcc.Loading(
                    html.Div(id="quality-overview", style={"width": "100%", "margin": "0"}),
                    target_components={"quality-overview": "children"},
                    delay_show=200,
                    parent_style={"minHeight": "120px"},
                    custom_spinner=html.Div([
                        html.Span("⏳", className="quality-loading-hourglass", **{"aria-hidden": "true"}),
                        html.Span("Datenqualität wird geprüft …"),
                    ], className="quality-loading-status", role="status"),
                ),
            ]),
            dcc.Tab(label="Prüfungen", value="checks", children=[
                html.P("Wählen Sie eine Prüfung, um die betroffenen Datensätze und ihre Bedeutung zu sehen."),
                dag.AgGrid(
                    id="quality-checks", rowData=[], columnDefs=[
                        {"headerName": field, "field": field, "minWidth": width, "flex": flex,
                         "headerClass": "pivot-index-header" if field == "Prüfung" else "pivot-highlighted-header",
                         **({"cellClassRules": {
                             "quality-status-error": "params.value === 'Fehler'",
                             "quality-status-warning": "params.value === 'Prüfen'",
                             "quality-status-info": "params.value === 'Information'",
                             "quality-status-ok": "params.value === 'OK'",
                         }} if field == "Status" else {})}
                        for field, width, flex in [
                            ("Prüfung", 240, 3), ("Status", 115, 1), ("Zeilen", 85, 1),
                            ("Fälle", 85, 1), ("Sachverhalte", 145, 1.5),
                            ("Ankerbeginn-Jahre", 175, 1.5), ("Auswirkung", 300, 5),
                        ]
                    ],
                    defaultColDef={"sortable": True, "filter": True, "resizable": True,
                                   "wrapText": True, "autoHeight": True},
                    dashGridOptions={"pagination": True, "paginationPageSize": 50,
                                     "paginationPageSizeSelector": [20, 50, 100],
                                     "rowHeight": 30, "headerHeight": 42},
                    getRowId="params.data.id", className="ag-theme-alpine",
                    rowClassRules={
                        "quality-error-row": "params.data && params.data.Status === 'Fehler'",
                        "quality-warning-row": "params.data && params.data.Status === 'Prüfen'",
                    },
                    style={"height": QUALITY_GRID_HEIGHT},
                ),
            ]),
            dcc.Tab(label="Rohdaten", value="raw", children=[
                html.Label("Prüfung"),
                dcc.Dropdown(id="quality-check-choice", options=[], value="load_conflict", clearable=False,
                             style={"width": "100%", "maxWidth": "520px", "marginBottom": "14px"}),
                html.Div(id="quality-evidence-note", role="status", className="quality-note"),
                dag.AgGrid(
                    id="quality-evidence", rowData=[],
                    columnDefs=[{"headerName": c, "field": c, "minWidth": 190,
                                 "cellDataType": False, "headerClass": "pivot-highlighted-header"}
                                for c in RAW_BESTAND_COLUMNS],
                    defaultColDef={"resizable": True, "sortable": True, "wrapText": True, "autoHeight": True,
                                   "flex": 1,
                                   "comparator": {"function": "0"},
                                   "filter": "agTextColumnFilter", "floatingFilter": True,
                                   "filterParams": {"debounceMs": 350}},
                    className="ag-theme-alpine", style={"height": QUALITY_GRID_HEIGHT},
                ),
                html.Div([
                    html.Button("Zurück", id="quality-prev", disabled=True, className="quality-page-button"),
                    html.Span(id="quality-page-label", style={"margin": "0 16px"}),
                    html.Button("Weiter", id="quality-next", disabled=True, className="quality-page-button"),
                ], style={"marginTop": "12px"}),
                dcc.Store(id="quality-page", data=0),
                dcc.Store(id="quality-sort", data=[]),
            ]),
        ]),
    ], style={**PAGE_STYLE, "width": "90%", "margin": "0 auto",
              "fontFamily": "Inter, Arial, sans-serif", "fontSize": "14px",
              "lineHeight": "1.6", "color": "#2c3e50"}, className="quality-page")
    tabs = layout.children[-1]
    panels = [
        html.Div(tab.children, id=f"quality-panel-{tab.value}",
                 className="quality-panel",
                 style={"display": "block" if tab.value == "overview" else "none",
                        "width": "100%", "boxSizing": "border-box", "padding": "22px",
                        "margin": "16px 0 24px", "backgroundColor": "#ffffff",
                        "border": "1px solid #d9e2ec", "borderRadius": "10px"})
        for tab in tabs.children
    ]
    tabs.className = "quality-tabs"
    tabs.style = {"width": "100%", "margin": "0"}
    tabs.children = [
        dcc.Tab(label=tab.label, value=tab.value,
                style={"padding": "13px 24px", "border": "none", "backgroundColor": "transparent",
                       "color": "#647787", "fontWeight": "600", "fontSize": "16px"},
                selected_style={"padding": "13px 24px", "border": "none",
                                "borderBottom": "3px solid #0b6fb3", "backgroundColor": "#eaf3fa",
                                "color": "#0b6fb3", "fontWeight": "700", "fontSize": "16px"})
        for tab in tabs.children
    ]
    layout.children.extend(panels)
    return layout


def forecast_readiness_card(report):
    statuses = {
        "passed": ("✓ Bereit", "✓ Erfüllt", "#307450"),
        "pending": ("Vorläufig", "Offen / prüfen", "#895d16"),
        "failed": ("Nicht bereit", "Nicht erfüllt", "#a33838"),
    }
    scores = report.forecast_readiness()
    return html.Div([
        html.Div("Regelmäßige Prognose", className="quality-kpi-label"),
        *[
            html.Div([
                html.Strong(metric),
                html.Span(
                    statuses[criteria[0][1]][1],
                    style={"color": statuses[criteria[0][1]][2]},
                    title="Datenqualität",
                ),
                html.Span(
                    f"{sum(state == 'passed' for _, state, _ in criteria)}/"
                    f"{len(criteria)} · {statuses[status][0]}",
                    style={"color": statuses[status][2]},
                    title="Bestätigte Prüfungen",
                ),
            ], className="quality-readiness-row")
            for metric, _, status, criteria in scores
        ],
        html.Details([
            html.Summary("Bewertung ansehen"),
            html.P("Vorprüfung für einen Prognosehorizont von 13 Wochen."),
            *[
                html.Div([
                    html.Strong(metric),
                    html.Ul([
                        html.Li(f"{label} · {statuses[status][1]}. {detail}")
                        for label, status, detail in criteria
                    ]),
                ])
                for metric, _, _, criteria in scores
            ],
        ], className="quality-readiness-details"),
    ], className="quality-readiness-card")


def overview_content(report):
    hana_check = next(check for check in report.checks if check["id"] == "hana_reconciliation")
    source = report.source
    keys = source[["/B99/S_BWPKKD", "/BIC/YBWRAVON"]]
    valid = keys.notna().all(axis=1) & keys.iloc[:, 0].astype(str).str.strip().ne("")
    total_cases = len(keys.loc[valid].drop_duplicates())
    affected_cases = len(keys.loc[valid & report.affected].drop_duplicates())
    active = [check for check in report.checks if check["Zeilen"] and check["Status"] != "Information"]
    active.sort(key=lambda c: (c["Status"] != "Fehler", -c["Fälle"], -c["Zeilen"]))
    fig = go.Figure(go.Bar(
        x=[check["Fälle"] for check in reversed(active)],
        y=[check["Prüfung"] for check in reversed(active)],
        orientation="h",
        marker_color=["#be5757" if check["Status"] == "Fehler" else "#df9a37"
                      for check in reversed(active)],
    ))
    fig.update_layout(
        title="Betroffene Fälle je Prüfung", height=max(300, 32 * len(active) + 100),
        paper_bgcolor=BG_COLOR, plot_bgcolor=BG_COLOR, margin={"l": 320, "t": 50, "b": 50},
        xaxis_title="Fälle (Mehrfachzählung zwischen Prüfungen möglich)",
    )
    weeks = report.dates["/BIC/YBWRELAD"].loc[report.affected]
    start_date = date(ANALYSIS_START_YEAR, 1, 1)
    week_counts = {}
    for value in weeks.dropna():
        if value >= start_date:
            start = max(start_date, value - timedelta(days=value.weekday()))
            week_counts[start] = week_counts.get(start, 0) + 1
    times = sorted(week_counts)
    timeline = go.Figure(go.Bar(x=times, y=[week_counts[t] for t in times], marker_color="#0b6fb3"))
    timeline.update_layout(
        title=f"Zeilen mit Fehlern oder Prüfhinweisen nach erstem Ladedatum ab {ANALYSIS_START_YEAR}",
        height=320, paper_bgcolor=BG_COLOR, plot_bgcolor=BG_COLOR,
        xaxis_title="Woche", yaxis_title="Datensätze",
    )
    for chart in (fig, timeline):
        chart.update_layout(
            font={"family": "Inter, Arial, sans-serif", "size": 14, "color": "#304b5b"},
            title_font={"size": 19}, bargap=0.3,
        )
        chart.update_xaxes(gridcolor="#e1e8ef", zeroline=False)
        chart.update_yaxes(gridcolor="#e1e8ef", zeroline=False)
    if times:
        timeline.update_xaxes(range=[start_date, times[-1] + timedelta(days=7)])
    def metric(label, value, detail, color):
        return html.Div([
            html.Div(label, className="quality-kpi-label"),
            html.Div(value, className="quality-kpi-value", style={"color": color}),
            html.Div(detail, className="quality-kpi-detail"),
        ], className="quality-kpi-card")

    cards = html.Div([
        metric("Geprüfte Daten", f"{len(source):,}".replace(",", "."),
               f"{total_cases:,} Fälle mit gültigem Schlüssel".replace(",", "."), "#245778"),
        metric("Fälle mit Prüfbedarf", f"{affected_cases:,}".replace(",", "."),
               f"{int(report.affected.sum()):,} betroffene Datensätze".replace(",", "."), "#a26c20" if active else "#307450"),
        metric("Anteil betroffener Fälle",
               f"{affected_cases / total_cases:.1%}".replace(".", ",") if total_cases else "—",
               "Fehler und Prüfhinweise", "#a26c20" if active else "#307450"),
        forecast_readiness_card(report),
    ], className="quality-kpi-strip")
    findings = []
    for check in active[:3]:
        findings.append(html.Div([
            html.Div(check["Prüfung"], style={"fontWeight": "600", "fontSize": "15px"}),
            html.Div(check["Auswirkung"], style={"marginTop": "5px", "fontSize": "14px"}),
            html.Div(f"Betroffene Fälle: {check['Fälle']:,}".replace(",", "."),
                     style={"marginTop": "8px", "fontSize": "12px", "color": "#607286"}),
        ], style={"padding": "15px 18px", "marginBottom": "10px", "borderRadius": "8px",
                  "borderLeft": "4px solid " + ("#c86c65" if check["Status"] == "Fehler" else "#dba54c"),
                  "backgroundColor": "#fff1ef" if check["Status"] == "Fehler" else "#fff8e8"}))
    if not active:
        findings.append(html.Div([
            html.Strong("Keine Fehler oder Prüfhinweise gefunden."),
            html.Div("Informationen zu offenen Zeiträumen und Änderungen zwischen Datenständen finden Sie unter Prüfungen.",
                     style={"marginTop": "6px", "fontSize": "13px"}),
        ], style={"padding": "16px 18px", "backgroundColor": "#edf7f0",
                  "border": "1px solid #cfe5d6", "borderRadius": "8px", "color": "#36664a"}))
    return html.Div([
        cards,
        html.H3("Was sollte geprüft werden?", style={"fontSize": "19px", "margin": "0 0 14px", "fontWeight": "600"}),
        *findings,
        html.P("Unter Prüfungen sehen Sie alle Ergebnisse. Eine Prüfung öffnet die zugehörigen Rohdaten.",
               style={"color": "#607286", "fontSize": "13px", "margin": "14px 0 22px"}),
        *([dcc.Graph(figure=fig, config={"displaylogo": False})] if active else []),
        *([dcc.Graph(figure=timeline, config={"displaylogo": False})] if times else []),
        html.H3("Zeitliche Abdeckung je Sachverhalt",
                style={"fontSize": "19px", "margin": "24px 0 10px", "fontWeight": "600"}),
        html.P(
            "Pausen beziehen sich auf erstes Ladedatum zwischen erster und letzter Beobachtung. "
            "IPL-Rückstand vergleicht Datenstände innerhalb der Auslieferung. "
            "Pausen sind reguläre Wochen ohne neue Fälle. Ein älterer IPL allein ist kein Datenfehler. "
            "Beide Angaben sind rein informativ und erhöhen den Anteil betroffener Fälle nicht.",
            style={"fontSize": "13px", "color": "#607286"}),
        dag.AgGrid(
            id="quality-coverage", rowData=report.coverage,
            columnDefs=[{"headerName": c, "field": c, "minWidth": width,
                         "flex": 3 if c == "Zeiträume ohne Einträge" else 1,
                         "headerClass": "pivot-highlighted-header"}
                        for c, width in [
                            ("Sachverhalt", 140), ("Erste Ladung", 150), ("Letzte erste Ladung", 180),
                            ("Letzter IPL", 150), ("IPL-Rückstand (Tage)", 180),
                            ("Wochen ohne Einträge", 190), ("Längste Pause (Wochen)", 200),
                            ("Zeiträume ohne Einträge", 400),
                        ]],
            defaultColDef={"sortable": True, "filter": True, "resizable": True},
            dashGridOptions={"pagination": True, "paginationPageSize": 20,
                             "paginationPageSizeSelector": [10, 20, 50], "rowHeight": 32},
            className="ag-theme-alpine", style={"height": "clamp(420px, 55vh, 740px)"},
        ),
        html.Div([
            html.Strong(f"{hana_check['Prüfung']} · {hana_check['Status']}"),
            html.Div(hana_check["Auswirkung"]),
        ], style={"marginTop": "18px", "padding": "14px 18px", "backgroundColor": "#f0f5fa",
                  "borderRadius": "8px", "fontSize": "13px"}),
        html.Details([
            html.Summary("Wie sind die Ergebnisse einzuordnen?", style={"cursor": "pointer", "fontWeight": "600"}),
            html.P("Eine Datensatz oder ein Fall kann mehrere Prüfungen betreffen. "
                   "Offene Zeiträume und fachliche Änderungen sind Informationsmeldungen. "
                   "Ihr tatsächlicher Einfluss auf die Prognosegenauigkeit muss durch historische "
                   "Prognosevergleiche geprüft werden."),
            html.P("Wochen ohne Einträge belegen keine fehlende Lieferung. Für die Vollständigkeit "
                   "der bereitgestellten Daten ist ein Abgleich mit SAP HANA erforderlich. "
                   "Zeilen ohne lesbares erstes Ladedatum fehlen im Zeitdiagramm."),
        ], style={"fontSize": "13px", "lineHeight": "1.7", "color": "#607286", "marginTop": "12px"}),
    ])
