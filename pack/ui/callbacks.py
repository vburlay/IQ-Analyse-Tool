import threading

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
    render_forecast_tab,
)
from pack.ui.monitoring import build_monitoring_main_fig, render_monitoring_tab
from pack.hana_import import clear_local_database
from pack.ui.refresh import get_refresh_state, run_hana_refresh, set_refresh_state
from pack.ui.styles import PAGE_STYLE, SIDEBAR_COLLAPSED_STYLE, SIDEBAR_STYLE


SIDEBAR_ITEMS = [
    ("tab-monitoring", "📊 Ladedatum-Analyse"),
    ("tab-anchor-forecast", "📈 Ankerprognose"),
]


def register_callbacks(app):
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

    @app.callback(Output("tabs-content", "children"), Input("active-tab-store", "data"))
    def render_tab(tab):
        if tab == "tab-monitoring":
            return render_monitoring_tab(get_refresh_state_fn=get_refresh_state)
        if tab == "tab-anchor-forecast":
            return render_forecast_tab()
        return html.Div(style=PAGE_STYLE)

    @app.callback(
        Output("app-section-title", "children"),
        Output("app-section-title", "className"),
        Input("active-tab-store", "data"),
    )
    def update_section_title(tab):
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
        if get_refresh_state()["running"]:
            return False, ""
        if not username or not password:
            set_refresh_state(
                running=False,
                success=False,
                message="Bitte HANA-Benutzer und Passwort eingeben.",
            )
            return False, ""
        threading.Thread(
            target=run_hana_refresh,
            args=(username, password),
            daemon=True,
        ).start()
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
        Input("confirm-clear-local-data", "submit_n_clicks"),
        State("data-version-store", "data"),
        prevent_initial_call=True,
    )
    def clear_local_data(submit_n_clicks, current_version):
        if not submit_n_clicks:
            return no_update, no_update
        clear_local_database()
        set_refresh_state(
            running=False,
            success=None,
            last_stand=None,
            message="Lokale DuckDB wurde vollständig geleert.",
        )
        return (current_version or 0) + 1, "Lokale DuckDB wurde vollständig geleert."

    @app.callback(
        Output("monitoring-stand-line", "children", allow_duplicate=True),
        Input("active-tab-store", "data"),
        Input("refresh-poll-interval", "n_intervals"),
        Input("monitoring-date-basis", "value"),
        prevent_initial_call=True,
    )
    def refresh_stand_line_on_open(active_tab, n_intervals, date_basis):
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
                else "Entwicklung nach erstem Ladedatum"
            ),
            (
                "Periodenwert je IPL (nicht kumuliert). Der Wert kann zwischen zwei IPL-Terminen steigen oder sinken."
                if date_basis == "ipl"
                else "Lückentage der im ausgewählten Jahr erstmals geladenen Datensätze. Orange Punkte kennzeichnen unvollständige Kalenderwochen."
            ),
        )

    @app.callback(
        Output("graph-forecast-container", "children"),
        Output("forecast-section-title", "children"),
        Output("forecast-section-description", "children"),
        Output("forecast-detail-grid", "rowData"),
        Output("forecast-detail-grid", "columnDefs"),
        Input("forecast-calculate-btn", "n_clicks"),
        Input("active-tab-store", "data"),
        Input("forecast-sachverhalt", "value"),
        Input("forecast-year-filter", "value"),
        State("forecast-stichtag", "date"),
        State("forecast-intervallniveau", "value"),
    )
    def update_forecast_graph(
        n_clicks, tab, sachverhalt, year, stichtag, intervallniveau,
    ):
        if tab != "tab-anchor-forecast" or not sachverhalt:
            return tuple(no_update for _ in range(5))
        df_local = prepare_anchor_forecast_plot_dataset(sachverhalt, year)
        result = calculate_stichtag_forecast(
            sachverhalt, stichtag, intervallniveau or 80, year,
        )
        rows, cols = forecast_detail_grid_data(result)
        return (
            anchor_graph_components(df_local, result, stichtag, year),
            f"Prognose für Fälle mit Bezug zum Ankerjahr {year}",
            "Eindeutige Fälle, deren Ankerzeitraum das ausgewählte Jahr überschneidet. "
            "Die Zeitachse zeigt das erste Ladedatum. Illustrative Prognose auf Basis "
            "der lokal gespeicherten Daten.",
            rows,
            cols,
        )
