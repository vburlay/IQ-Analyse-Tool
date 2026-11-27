from dash import Dash, html, dcc
from pack.config import APP_TITLE, DEFAULT_REFRESH_INTERVAL_MS
from pack.ui.styles import *
from pack.ui.components import sidebar_button_style
from pack.ui.callbacks import register_callbacks, SIDEBAR_ITEMS
from pack.ui.refresh import ensure_database_exists
from pack.services.monitoring_service import get_monitoring_stand_text

app = Dash(
    __name__, title=APP_TITLE, suppress_callback_exceptions=True,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server

ensure_database_exists()

app.layout = html.Div(
    [
        dcc.Store(id="active-tab-store", data="tab-monitoring"),
        dcc.Store(id="sidebar-collapsed-store", data=False),
        dcc.Store(id="data-version-store", data=0),
        dcc.Store(id="current-forecast-run-id", storage_type="memory"),
        dcc.ConfirmDialog(
            id="confirm-clear-local-data",
            message=(
                "Alle lokalen Daten in DuckDB werden endgültig gelöscht. "
                "Dabei werden auch alle experimentellen Prognoseläufe gelöscht. "
                "Die Tabellenstruktur bleibt erhalten. Fortfahren?"
            ),
        ),

        dcc.Interval(
            id="refresh-poll-interval",
            interval=DEFAULT_REFRESH_INTERVAL_MS,
            n_intervals=0,
            disabled=True,
        ),

        html.Div(
            id="sidebar",
            style=SIDEBAR_STYLE,
            children=[
                html.Button(
                    "⬅ Ausblenden",
                    id="sidebar-toggle-btn",
                    n_clicks=0,
                    style=TOGGLE_BTN_STYLE,
                ),
                html.Div(
                    id="sidebar-tabs",
                    children=[
                        html.Button(
                            label,
                            id={"type": "sidebar-tab", "index": tab_id},
                            n_clicks=0,
                            title=label,
                            style=sidebar_button_style(
                                tab_id,
                                "tab-monitoring",
                                False,
                            ),
                        )
                        for tab_id, label in SIDEBAR_ITEMS
                    ],
                ),
            ],
        ),

        html.Div(
            style=CONTENT_STYLE,
            className="app-content",
            children=[
                html.H1(
                    APP_TITLE,
                    className="app-title",
                    style={
                        "textAlign": "center",
                        "marginTop": "10px",
                        "marginBottom": "5px",
                        "fontSize": "37px",
                        "color": "#404945",
                    },
                ),
                html.Div([
                    html.Div("Potential und Ertrag", id="app-section-title",
                             className="app-section-title app-section-title--monitoring"),
                    html.Div([
                        "(", html.Span(get_monitoring_stand_text(), id="monitoring-stand-line"), ")",
                    ], id="monitoring-stand-label", className="app-section-title--monitoring", role="status"),
                ], className="app-section-heading"),
                html.Div(id="tabs-content"),
            ],
        ),
    ],
    style=APP_SHELL_STYLE,
    className="app-shell",
)
register_callbacks(
    app=app,
)
if __name__ == "__main__":
    app.run(debug=True)
