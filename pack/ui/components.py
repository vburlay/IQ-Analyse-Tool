from typing import Optional
from dash import html
import dash_ag_grid as dag

from pack.ui.styles import *

# ============================================================
# BASIC UI BLOCKS
# ============================================================

def section_title(text: str):
    return html.Div(text, style=SUBTITLE_STYLE)


def kpi_card(title: str, value: str = "—", value_id: Optional[str] = None):
    value_props = {"style": KPI_VALUE_STYLE}
    if value_id is not None:
        value_props["id"] = value_id

    return html.Div(
        [
            html.Div(title, style=KPI_TITLE_STYLE),
            html.Div(value, **value_props),
        ],
        style=KPI_BASE_STYLE,
    )


# ============================================================
# GRID
# ============================================================

def make_grid(
    id_value: str,
    row_data=None,
    column_defs=None,
    width="100%",
    auto_height: bool = False,
):
    grid_options = {
        "animateRows": True,
        "suppressHorizontalScroll": False,
        "pagination": not auto_height,
        "paginationPageSize": 10,
    }
    if auto_height:
        grid_options["domLayout"] = "autoHeight"

    return dag.AgGrid(
        id=id_value,
        rowData=row_data or [],
        columnDefs=column_defs or [],
        defaultColDef={
            "sortable": True,
            "filter": True,
            "resizable": True,
            "floatingFilter": True,
            "wrapText": True,
            "autoHeight": True,
        },
        dashGridOptions=grid_options,
        columnSize="sizeToFit",
        style={
            "width": width,
            "margin": "0 auto",
            "height": "auto" if auto_height else "520px",
        },
        className="ag-theme-alpine",
    )


# ============================================================
# SIDEBAR
# ============================================================

def sidebar_button_style(tab_id: str, active_tab: str, collapsed: bool = False):
    style = TAB_BUTTON_ACTIVE_STYLE if tab_id == active_tab else TAB_BUTTON_STYLE
    return {
        **style,
        "textAlign": "center" if collapsed else "left",
        "padding": "14px 10px" if collapsed else "14px 16px",
    }
