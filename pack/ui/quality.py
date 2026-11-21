"""Data-quality workspace and evidence tables."""
from datetime import timedelta

import plotly.graph_objects as go
from dash import dcc, html
import dash_ag_grid as dag

from pack.config import RAW_BESTAND_COLUMNS
from pack.ui.styles import PAGE_STYLE, CHART_CARD_STYLE, SECTION_STYLE, SUBTITLE_STYLE, BG_COLOR


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
                html.Div(id="quality-overview", style={"width": "100%", "margin": "0"}),
            ]),
            dcc.Tab(label="Prüfungen", value="checks", children=[
                html.P("Wählen Sie eine Prüfung, um die betroffenen Datensätze und ihre Bedeutung zu sehen."),
                dag.AgGrid(
                    id="quality-checks", rowData=[], columnDefs=[
                        {"headerName": field, "field": field, "minWidth": width,
                         "headerClass": "pivot-index-header" if field == "Prüfung" else "pivot-highlighted-header",
                         **({"cellClassRules": {
                             "quality-status-error": "params.value === 'Fehler'",
                             "quality-status-warning": "params.value === 'Prüfen'",
                             "quality-status-info": "params.value === 'Information'",
                             "quality-status-ok": "params.value === 'OK'",
                         }} if field == "Status" else {})}
                        for field, width in [("Prüfung", 300), ("Status", 130), ("Zeilen", 110),
                                             ("Fälle", 110), ("Sachverhalte", 180), ("Ankerbeginn-Jahre", 180), ("Auswirkung", 450)]
                    ],
                    defaultColDef={"sortable": True, "filter": True, "resizable": True,
                                   "wrapText": True, "autoHeight": True,
                                   "cellStyle": {"lineHeight": "20px", "paddingTop": "5px",
                                                 "paddingBottom": "5px", "fontSize": "14px"}},
                    dashGridOptions={"pagination": True, "paginationPageSize": 20, "rowHeight": 36, "headerHeight": 42},
                    getRowId="params.data.id", className="ag-theme-alpine",
                    rowClassRules={
                        "quality-error-row": "params.data && params.data.Status === 'Fehler'",
                        "quality-warning-row": "params.data && params.data.Status === 'Prüfen'",
                    },
                    style={"height": "650px"},
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
                                   "comparator": {"function": "0"},
                                   "filter": "agTextColumnFilter", "floatingFilter": True,
                                   "filterParams": {"debounceMs": 350}},
                    className="ag-theme-alpine", style={"height": "520px"},
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


def overview_content(report):
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
    week_counts = {}
    for value in weeks.dropna():
        start = value - timedelta(days=value.weekday())
        week_counts[start] = week_counts.get(start, 0) + 1
    times = sorted(week_counts)
    timeline = go.Figure(go.Bar(x=times, y=[week_counts[t] for t in times], marker_color="#0b6fb3"))
    timeline.update_layout(
        title="Zeilen mit Fehlern oder Prüfhinweisen nach erstem Ladedatum",
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
    def metric(label, value, detail, color):
        return html.Div([
            html.Div(label, style={"fontSize": "13px", "fontWeight": "600", "color": "#607286"}),
            html.Div(value, style={"fontSize": "30px", "fontWeight": "700", "color": color, "lineHeight": "1.3", "margin": "9px 0"}),
            html.Div(detail, style={"fontSize": "12px", "color": "#718096"}),
        ], style={"flex": "1 1 220px", "padding": "18px 20px", "backgroundColor": "#f6f8fb",
                  "border": "1px solid #e2e8f0", "borderRadius": "8px", "boxSizing": "border-box"})

    cards = html.Div([
        metric("Geprüfte Daten", f"{len(source):,}".replace(",", "."),
               f"{total_cases:,} eindeutige Fälle mit gültigem Schlüssel".replace(",", "."), "#245778"),
        metric("Fälle mit Prüfbedarf", f"{affected_cases:,}".replace(",", "."),
               f"{int(report.affected.sum()):,} betroffene Datensätze".replace(",", "."), "#a26c20" if active else "#307450"),
        metric("Anteil betroffener Fälle",
               f"{affected_cases / total_cases:.1%}".replace(".", ",") if total_cases else "—",
               "Fehler und Prüfhinweise, ohne Informationsmeldungen", "#a26c20" if active else "#307450"),
    ], style={"display": "flex", "flexWrap": "wrap", "gap": "14px", "marginBottom": "24px"})
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
        *([dcc.Graph(figure=fig, config={"displaylogo": False}),
           dcc.Graph(figure=timeline, config={"displaylogo": False})] if active else []),
        html.H3("Zeitliche Abdeckung je Sachverhalt",
                style={"fontSize": "19px", "margin": "24px 0 10px", "fontWeight": "600"}),
        html.P(
            "Pausen beziehen sich auf erstes Ladedatum zwischen erster und letzter Beobachtung. "
            "IPL-Rückstand vergleicht Datenstände innerhalb der Auslieferung. "
            "Beide Hinweise benötigen fachliche Klärung; sie beweisen keine fehlenden Datensätze.",
            style={"fontSize": "13px", "color": "#607286"}),
        dag.AgGrid(
            id="quality-coverage", rowData=report.coverage,
            columnDefs=[{"headerName": c, "field": c, "minWidth": width,
                         "headerClass": "pivot-highlighted-header"}
                        for c, width in [
                            ("Sachverhalt", 140), ("Erste Ladung", 150), ("Letzte erste Ladung", 180),
                            ("Letzter IPL", 150), ("IPL-Rückstand (Tage)", 180),
                            ("Wochen ohne Einträge", 190), ("Längste Pause (Wochen)", 200),
                            ("Zeiträume ohne Einträge", 400),
                        ]],
            defaultColDef={"sortable": True, "filter": True, "resizable": True},
            dashGridOptions={"pagination": True, "paginationPageSize": 10},
            className="ag-theme-alpine", style={"height": "360px"},
        ),
        html.Div([
            html.Strong("Vollständigkeitsabgleich mit SAP HANA: nicht geprüft."),
            html.Div("Für eine Bestätigung werden Kontrollanzahl und Kontrollsumme aus HANA "
                     "zum selben Abfragezeitpunkt und mit denselben Filtern benötigt."),
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
