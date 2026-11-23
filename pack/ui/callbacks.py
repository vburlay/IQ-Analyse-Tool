from pack.ui.quality import render_quality_tab, overview_content
from pack.services.quality_service import load_quality_report
from pack.data_access import load_anomaly_raw_records
from pack.ui.anomalies import build_severity_section
import logging
import threading
from collections import OrderedDict
from datetime import date

from pack.forecast_runs import ForecastRun, save_forecast_run
from pack.config import ANALYSIS_START_YEAR

from dash import ALL, Input, Output, State, ctx, html, no_update

from pack.services.forecast_service import (
    calculate_stichtag_forecast,
    prepare_anchor_forecast_plot_dataset,
)
from pack.services.monitoring_service import get_monitoring_grid_data, get_monitoring_kpis, get_monitoring_stand_text
from pack.ui.components import sidebar_button_style
from pack.ui.forecast import (
    anchor_graph_components,
    forecast_detail_grid_data,
    CURRENT_RUN_MESSAGE,
    render_forecast_tab,
)
from pack.ui.monitoring import build_monitoring_main_fig, render_monitoring_tab
from pack.hana_import import clear_local_database
from pack.ui.refresh import get_refresh_state, run_hana_refresh, set_refresh_state
from pack.ui.styles import PAGE_STYLE, SIDEBAR_COLLAPSED_STYLE, SIDEBAR_STYLE


SIDEBAR_ITEMS = [
    ("tab-monitoring", "📊 Analyse"),
    ("tab-anchor-forecast", "📈 Prognose"),
    ("tab-quality", "🔎 Datenqualität"),
]


def register_callbacks(app):
    forecast_lock = threading.Lock()
    last_forecast_click = OrderedDict()

    @app.callback(
        Output("quality-panel-overview", "style"),
        Output("quality-panel-checks", "style"),
        Output("quality-panel-raw", "style"),
        Input("quality-subtab", "value"),
    )
    def show_quality_panel(selected):
        return tuple({
            "display": "block" if selected == name else "none",
            "width": "100%", "boxSizing": "border-box", "padding": "22px",
            "margin": "16px 0 24px", "backgroundColor": "#ffffff",
            "border": "1px solid #d9e2ec", "borderRadius": "10px",
        } for name in ("overview", "checks", "raw"))

    @app.callback(
        Output("quality-overview", "children"),
        Output("quality-checks", "rowData"),
        Output("quality-check-choice", "options"),
        Output("quality-scope-note", "children"),
        Input("quality-year", "data"),
        Input("quality-sv", "data"),
        Input("data-version-store", "data"),
        State("active-tab-store", "data"),
    )
    def update_quality(year, sv, _version, tab):
        if tab != "tab-quality":
            return (no_update,) * 4
        try:
            report = load_quality_report(year, sv)
            return (
                overview_content(report), report.checks,
                [{"label": f"{c['Prüfung']} ({c['Zeilen']} Zeilen)", "value": c["id"]} for c in report.checks],
                f"Die Qualitätsprüfung zeigt Zeilen mit erstem Ladedatum von {ANALYSIS_START_YEAR} "
                "bis zum laufenden Jahr. Fehlende oder nicht lesbare erste Ladedaten bleiben zur Prüfung enthalten. "
                "Frühere Versionen können zur Erkennung von Widersprüchen herangezogen werden. "
                "Sachverhalte und Ankerbeginn-Jahre zeigen, wo die Hinweise auftreten. "
                "Ein Fall kann mehrere Ankerjahre betreffen. Die angezeigten Jahre bezeichnen "
                "den Beginn seines Ankerzeitraums. Die genauen Datumsangaben stehen unter Rohdaten.",
            )
        except Exception:
            logging.exception("Data quality report failed")
            return "Die Datenqualität konnte nicht geprüft werden.", [], [], ""

    @app.callback(
        Output("quality-check-choice", "value"),
        Output("quality-subtab", "value"),
        Input("quality-checks", "cellClicked"),
        prevent_initial_call=True,
    )
    def select_quality_check(event):
        if not event or not event.get("rowId"):
            return no_update, no_update
        return event["rowId"], "raw"

    @app.callback(
        Output("quality-evidence", "rowData"),
        Output("quality-evidence", "columnDefs"),
        Output("quality-evidence-note", "children"),
        Output("quality-page", "data"),
        Output("quality-page-label", "children"),
        Output("quality-prev", "disabled"),
        Output("quality-next", "disabled"),
        Output("quality-sort", "data"),
        Input("quality-evidence", "columnState"),
        Input("quality-evidence", "filterModel"),
        Input("quality-check-choice", "value"),
        Input("quality-year", "data"),
        Input("quality-sv", "data"),
        Input("data-version-store", "data"),
        Input("quality-prev", "n_clicks"),
        Input("quality-next", "n_clicks"),
        State("quality-page", "data"),
        State("quality-sort", "data"),
        State("active-tab-store", "data"),
    )
    def quality_evidence(column_state, filter_model, check_id, year, sv, _version, _prev, _next, page, previous_sort, tab):
        if tab != "tab-quality":
            return (no_update,) * 8
        sort_model = [
            {"colId": c["colId"], "sort": c["sort"]}
            for c in sorted(column_state or [], key=lambda c: c.get("sortIndex") or 0)
            if c.get("sort") in ("asc", "desc")
        ]
        if "quality-evidence.columnState" in ctx.triggered_prop_ids and "quality-evidence.filterModel" not in ctx.triggered_prop_ids and sort_model == (previous_sort or []):
            return (no_update,) * 8
        try:
            report = load_quality_report(year, sv)
            check = next((c for c in report.checks if c["id"] == check_id), None)
            if check is None:
                return [], [], "Bitte Prüfung auswählen.", 0, "", True, True, sort_model
            requested = 0
            if ctx.triggered_id == "quality-prev":
                requested = max(0, (page or 0) - 1)
            elif ctx.triggered_id == "quality-next":
                requested = (page or 0) + 1
            records, count, page = report.records(check_id, requested, sort_model=sort_model, filter_model=filter_model)
            from pack.config import RAW_BESTAND_COLUMNS
            columns = [{
                "headerName": c, "field": c, "minWidth": 190, "cellDataType": False,
                "headerClass": "pivot-highlighted-header",
                **({"cellStyle": {"backgroundColor": "#fff4ce"}} if c in check["columns"] else {}),
            } for c in RAW_BESTAND_COLUMNS]
            pages = max(1, (count + 99) // 100)
            return (
                records, columns if ctx.triggered_id == "quality-check-choice" or not column_state else no_update,
                f"{check['Prüfung']} · {count} Datensätze. {check['Auswirkung']} "
                "Gelb markierte Felder gehören zur ausgewählten Prüfung. "
                "Vergleichen Sie bei widersprüchlichen Angaben die Datenstände desselben Falls.",
                page, f"Seite {page + 1} / {pages}", page == 0, page + 1 >= pages, sort_model,
            )
        except Exception:
            logging.exception("Data quality evidence failed")
            return [], [], "Die Datensätze konnten nicht geladen werden.", 0, "", True, True, no_update

    @app.callback(
        Output("active-tab-store", "data"),
        Input({"type": "sidebar-tab", "index": ALL}, "n_clicks"),
        State("active-tab-store", "data"),
        prevent_initial_call=True,
    )
    def switch_tab(n_clicks_list, current_tab):
        triggered_id = ctx.triggered_id
        return triggered_id["index"] if triggered_id else current_tab

    @app.callback(
        Output("sidebar-collapsed-store", "data"),
        Input("sidebar-toggle-btn", "n_clicks"),
        State("sidebar-collapsed-store", "data"),
        prevent_initial_call=True,
    )
    def toggle_sidebar(n_clicks, collapsed):
        return not collapsed

    @app.callback(
        Output("sidebar", "style"),
        Output("sidebar-toggle-btn", "children"),
        Input("sidebar-collapsed-store", "data"),
    )
    def update_sidebar_style(collapsed):
        if collapsed:
            return SIDEBAR_COLLAPSED_STYLE, "➡"
        return SIDEBAR_STYLE, "⬅ Ausblenden"

    @app.callback(
        Output({"type": "sidebar-tab", "index": ALL}, "children"),
        Output({"type": "sidebar-tab", "index": ALL}, "style"),
        Output({"type": "sidebar-tab", "index": ALL}, "title"),
        Input("active-tab-store", "data"),
        Input("sidebar-collapsed-store", "data"),
    )
    def update_sidebar_tabs(active_tab, collapsed):
        children, styles, titles = [], [], []
        for tab_id, label in SIDEBAR_ITEMS:
            children.append(label.split(" ")[0] if collapsed else label)
            styles.append(sidebar_button_style(tab_id, active_tab, collapsed))
            titles.append(label)
        return children, styles, titles

    @app.callback(
        Output("tabs-content", "children"),
        Output("current-forecast-run-id", "data"),
        Input("active-tab-store", "data"),
    )
    def render_tab(tab):
        if tab == "tab-monitoring":
            return render_monitoring_tab(get_refresh_state_fn=get_refresh_state), None
        if tab == "tab-anchor-forecast":
            return render_forecast_tab(), None
        if tab == "tab-quality":
            return render_quality_tab(), None
        return html.Div(style=PAGE_STYLE), None

    @app.callback(
        Output("app-section-title", "children"),
        Output("app-section-title", "className"),
        Input("active-tab-store", "data"),
    )
    def update_section_title(tab):
        if tab == "tab-quality":
            return "Datenqualität und Auswirkungen auf die Prognose", "app-section-title app-section-title--monitoring"
        if tab == "tab-monitoring":
            return (
                "Potential und Ertrag",
                "app-section-title app-section-title--monitoring",
            )
        if tab == "tab-anchor-forecast":
            return "Lückentage nach Ankerjahr: Ist-Wert und Prognose", "app-section-title app-section-title--monitoring"
        return "", "app-section-title app-section-title--monitoring"

    @app.callback(
        Output("refresh-poll-interval", "disabled"),
        Output("hana-password", "value"),
        Input("refresh-data-btn", "n_clicks"),
        State("active-tab-store", "data"),
        State("hana-username", "value"),
        State("hana-password", "value"),
        prevent_initial_call=True,
    )
    def start_data_refresh(n_clicks, active_tab, username, password):
        if active_tab != "tab-monitoring" or not n_clicks:
            return True, ""
        with forecast_lock:
            if get_refresh_state()["running"]:
                return False, ""
            if not username or not password:
                set_refresh_state(
                    success=False,
                    message="Bitte HANA-Benutzer und Passwort eingeben.",
                )
                return False, ""
            worker = threading.Thread(
                target=run_hana_refresh,
                args=(username, password),
                daemon=True,
            )
            set_refresh_state(running=True)
            try:
                worker.start()
            except RuntimeError:
                set_refresh_state(
                    running=False,
                    success=False,
                    message="Die SAP-HANA-Aktualisierung konnte nicht gestartet werden.",
                )
        return False, ""

    @app.callback(
        Output("monitoring-stand-line", "children"),
        Output("refresh-poll-interval", "disabled", allow_duplicate=True),
        Output("data-version-store", "data"),
        Output("hana-login-message", "children"),
        Input("refresh-poll-interval", "n_intervals"),
        State("data-version-store", "data"),
        State("active-tab-store", "data"),
        State("monitoring-date-basis", "value"),
        prevent_initial_call=True,
    )
    def poll_refresh_status(n_intervals, current_version, active_tab, date_basis):
        state = get_refresh_state()
        if state["running"]:
            icon = "⏳" if n_intervals % 2 == 0 else "⌛"
            return f"{icon} Daten werden aktualisiert ...", False, current_version, "Verbindung mit SAP HANA wird hergestellt …"
        date_basis = date_basis or "elad"
        stand_text = get_monitoring_stand_text(
            state["last_stand"] if date_basis == "elad" else None,
            date_basis,
        )
        if state["success"] is True:
            message = html.Span(
                [
                    html.Span("✓ ", **{"aria-hidden": "true"}),
                    state["message"] or "Aktualisierung aus SAP HANA abgeschlossen.",
                ],
                className="hana-login-success",
            )
            return stand_text, True, current_version + 1, message
        return stand_text, True, current_version, state["message"] or "Lokale Daten bleiben unverändert."

    @app.callback(
        Output("confirm-clear-local-data", "displayed"),
        Input("clear-local-data-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def request_local_data_clear(n_clicks):
        return bool(n_clicks)

    @app.callback(
        Output("data-version-store", "data", allow_duplicate=True),
        Output("hana-login-message", "children", allow_duplicate=True),
        Output("current-forecast-run-id", "data", allow_duplicate=True),
        Input("confirm-clear-local-data", "submit_n_clicks"),
        State("data-version-store", "data"),
        prevent_initial_call=True,
    )
    def clear_local_data(submit_n_clicks, current_version):
        if not submit_n_clicks:
            return no_update, no_update, no_update
        with forecast_lock:
            if get_refresh_state()["running"]:
                return no_update, "Bitte warten Sie, bis die SAP-HANA-Aktualisierung abgeschlossen ist.", no_update
            clear_local_database()
            set_refresh_state(
                running=False,
                success=None,
                last_stand=None,
                message="Lokale DuckDB wurde vollständig geleert.",
            )
        return (current_version or 0) + 1, "Lokale DuckDB wurde vollständig geleert.", None

    @app.callback(
        Output("monitoring-stand-line", "children", allow_duplicate=True),
        Input("active-tab-store", "data"),
        Input("refresh-poll-interval", "n_intervals"),
        Input("monitoring-date-basis", "value"),
        Input("data-version-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_stand_line_on_open(active_tab, n_intervals, date_basis, _data_version):
        if active_tab != "tab-monitoring":
            return no_update
        state = get_refresh_state()
        if state["running"]:
            icon = "⏳" if n_intervals % 2 == 0 else "⌛"
            return f"{icon} Daten werden aktualisiert ..."
        date_basis = date_basis or "elad"
        return get_monitoring_stand_text(
            state["last_stand"] if date_basis == "elad" else None,
            date_basis,
        )

    @app.callback(
        Output("monitoring-kpi-potential", "children"),
        Output("monitoring-kpi-ertrag", "children"),
        Output("monitoring-kpi-ertragsanteil", "children"),
        Output("monitoring-kpi-euro-pro-tag", "children"),
        Output("monitoring-kpi-potential-ertragsanteil", "children"),
        Output("monitoring-main-graph", "figure"),
        Output("monitoring-risk-grid", "rowData"),
        Output("monitoring-risk-grid", "columnDefs"),
        Output("monitoring-chart-title", "children"),
        Output("monitoring-chart-description", "children"),
        Input("data-version-store", "data"),
        Input("monitoring-year-filter", "value"),
        Input("monitoring-date-basis", "value"),
        State("active-tab-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_monitoring_tab_after_load(
        data_version, selected_year, date_basis, active_tab,
    ):
        if active_tab != "tab-monitoring":
            return tuple(no_update for _ in range(10))
        date_basis = date_basis or "elad"
        kpi = get_monitoring_kpis(selected_year, date_basis)
        rows, cols = get_monitoring_grid_data(selected_year, date_basis)
        return (
            kpi["potential"], kpi["ertrag"], kpi["ertragsanteil"],
            kpi["euro_pro_tag"], kpi["potential_ertragsanteil"],
            build_monitoring_main_fig(selected_year, date_basis),
            rows, cols,
            (
                "Aktuelle Entwicklung der Lückentage je IPL"
                if date_basis == "ipl"
                else "Lückentage nach Woche der ersten Ladung"
            ),
            (
                "Periodenwert je IPL (nicht kumuliert). Der Wert kann zwischen zwei IPL-Terminen steigen oder sinken."
                if date_basis == "ipl"
                else "Ein Punkt zeigt die Lückentage der in dieser Woche erstmals geladenen Datensätze. Wochenwerte, nicht kumuliert. Wochen ohne Datensätze erscheinen als Lücken in der Linie."
            ),
        )

    @app.callback(
        Output("forecast-severity-content", "children"),
        Input("graph-forecast-container", "children"),
        State("forecast-sachverhalt", "value"),
        State("forecast-year-filter", "value"),
    )
    def update_severity_section(graphs, sachverhalt, year):
        if not sachverhalt or year is None:
            return "Bitte Ankerjahr und Sachverhalt auswählen."
        try:
            figure = graphs[0]["props"]["children"]["props"]["figure"]
            start, end = figure["layout"]["xaxis"]["range"]
            return build_severity_section(sachverhalt, year, start=start, end=end)
        except Exception:
            logging.exception("Weekly case severity diagnostics failed")
            return "Die Auffälligkeiten konnten nicht geladen werden."

    @app.callback(
        Output("forecast-severity-grid", "getRowStyle"),
        Output("forecast-severity-grid", "scrollTo"),
        Output("forecast-anomaly-raw-details", "open"),
        Output("forecast-anomaly-raw-grid", "rowData"),
        Output("forecast-anomaly-raw-message", "children"),
        Input("forecast-severity-graph", "clickData"),
        State("forecast-severity-grid", "rowData"),
        State("forecast-anomaly-context", "data"),
        prevent_initial_call=True,
    )
    def highlight_severity_week(click_data, rows, context):
        if not click_data or not click_data.get("points"):
            return (no_update,) * 5
        point = click_data["points"][0]
        if point.get("curveNumber") != 1:
            return (no_update,) * 5
        week_id = str(point.get("x", ""))[:10]
        if not any(row.get("week_id") == week_id for row in (rows or [])):
            return (no_update,) * 5
        selected = next(row for row in rows if row["week_id"] == week_id)
        try:
            raw = load_anomaly_raw_records(
                week_id, context["year"], context["sachverhalt"],
                selected["cases"], selected["days"],
            )
            # Format date columns for the grid without changing the DuckDB records.
            import json
            raw_rows = json.loads(raw.to_json(orient="records", date_format="iso"))
            for record in raw_rows:
                for column in ("/BIC/YBWRAVON", "/BIC/YBWRELAD", "/BIC/YBWRABIS", "/BIC/YBWRIPL"):
                    value = record.get(column)
                    if value is None:
                        continue
                    text = str(value)
                    compact = text[:10].replace("-", "")
                    if len(compact) == 8 and compact.isdigit():
                        record[column] = f"{compact[6:8]}.{compact[4:6]}.{compact[:4]}"
            message = (
                f"Woche ab {selected['week']} · {len(raw_rows)} Fälle · "
                f"{selected['days']:,.0f} Lückentage. "
                "Originalspalten des letzten IPL je Fall; identische doppelte Zeilen einmal angezeigt."
            )
        except ValueError as exc:
            raw_rows, message = [], str(exc)
        except Exception:
            logging.exception("Anomaly raw records could not be loaded")
            raw_rows, message = [], "Die Rohdaten konnten nicht geladen werden."
        return (
            {"styleConditions": [{
                "condition": f"params.data && params.data.week_id === '{date.fromisoformat(week_id).isoformat()}'",
                "style": {"backgroundColor": "#fff4ce", "fontWeight": "600"},
            }]},
            {"rowId": week_id, "rowPosition": "middle"},
            True,
            raw_rows,
            message,
        )

    @app.callback(
        Output("graph-forecast-container", "children"),
        Output("forecast-section-title", "children"),
        Output("forecast-detail-grid", "rowData"),
        Output("current-forecast-run-id", "data", allow_duplicate=True),
        Output("current-forecast-note", "children"),
        Output("forecast-calculation-message", "children"),
        Input("forecast-calculate-btn", "n_clicks"),
        State("forecast-form-id", "data"),
        State("active-tab-store", "data"),
        State("forecast-sachverhalt", "value"),
        State("forecast-year-filter", "value"),
        State("forecast-stichtag", "date"),
        State("forecast-intervallniveau", "value"),
        prevent_initial_call=True,
        running=[(Output("forecast-calculate-btn", "disabled"), True, False)],
    )
    def update_forecast_graph(n_clicks, form_id, tab, sachverhalt, year, stichtag, intervallniveau):
        unchanged = (no_update,) * 5
        if tab != "tab-anchor-forecast" or not n_clicks or not form_id:
            return (*unchanged, no_update)

        with forecast_lock:
            if n_clicks <= last_forecast_click.get(form_id, 0):
                return (*unchanged, no_update)
            last_forecast_click[form_id] = n_clicks
            last_forecast_click.move_to_end(form_id)
            if len(last_forecast_click) > 1024:
                last_forecast_click.popitem(last=False)

            try:
                target = date.fromisoformat(stichtag)
                if not sachverhalt or year is None or intervallniveau not in (80, 90, 95):
                    raise ValueError
                if year != "all":
                    year = int(year)
                    if not 1 <= year <= 9999:
                        raise ValueError
            except (TypeError, ValueError):
                return (*unchanged, "Bitte gültige Prognoseparameter eingeben.")

            try:
                dataset = prepare_anchor_forecast_plot_dataset(sachverhalt, year)
                result = calculate_stichtag_forecast(
                    sachverhalt, target.isoformat(), intervallniveau, year, dataset=dataset,
                )
                if result["forecast"] is None:
                    return (*unchanged, result["message"])
                if target <= result["data_cutoff"]:
                    return (
                        *unchanged,
                        f"Der Stichtag muss nach dem Datenstand ({result['data_cutoff']:%d.%m.%Y}) liegen.",
                    )

                run = ForecastRun.from_result(result, target, year, sachverhalt)
                graphs = anchor_graph_components(dataset, result, target.isoformat(), year)
                history = save_forecast_run(run)
                rows, _ = forecast_detail_grid_data(history, run.run_id)
            except Exception:
                logging.exception("Forecast calculation or persistence failed")
                return (*unchanged, "Die Prognose konnte nicht berechnet oder gespeichert werden.")

            return (
                graphs,
                f"Prognose für Fälle mit Bezug zum Ankerjahr {year}",
                rows,
                run.run_id,
                CURRENT_RUN_MESSAGE,
                "",
            )
