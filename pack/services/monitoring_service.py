from __future__ import annotations

import numpy as np
import pandas as pd

from pack.config import EURO_PER_LUECKENTAG
from pack.data_access import (
    duck_query_df,
    get_latest_elad_value,
    get_latest_ipl_value,
    load_raw_weekly_data,
)


def get_monitoring_stand_text(
    last_stand: str | None = None,
    date_basis: str = "elad",
) -> str:
    if date_basis == "ipl":
        return f"Stand IPL: {get_latest_ipl_value()}"
    stand = last_stand or get_latest_elad_value()
    return f"Stand erstes Ladedatum: {stand}"


def get_monitoring_kpis(year: int | None, date_basis: str = "elad") -> dict[str, str]:
    rows, _ = get_monitoring_grid_data(year, date_basis)
    if not rows:
        return {
            "potential": "—",
            "ertrag": "—",
            "ertragsanteil": "—",
            "euro_pro_tag": "—",
            "potential_ertragsanteil": "—",
        }

    total = rows[-1]

    def format_integer(value: int | float) -> str:
        return f"{int(round(value)):,}".replace(",", ".")

    def format_euro(value: int | float) -> str:
        return f"{format_integer(value)} €"

    return {
        "potential": format_integer(total["Potential"]),
        "ertrag": format_integer(total["Ertrag"]),
        "ertragsanteil": f"{int(total['Ertragsanteil'])} %",
        "euro_pro_tag": format_euro(total["11 Euro pro Tag"]),
        "potential_ertragsanteil": format_integer(total["Potential_x_Ertragsanteil"]),
    }


def get_monitoring_chart_data(
    year: int | None = None,
    date_basis: str = "elad",
) -> pd.DataFrame:
    selected_year = int(year) if year is not None else pd.Timestamp.today().year
    if date_basis == "ipl":
        df = load_raw_weekly_data("all")
        if df.empty:
            return pd.DataFrame(
                columns=["x", "TAGEN", "Kalendertage", "Vollstaendige_Woche"]
            )
        df = df[df["IPL_dt"].dt.year == selected_year].copy()
        df = df.rename(columns={"IPL_dt": "x"})
        df["Kalendertage"] = 7
        df["Vollstaendige_Woche"] = True
        return df[["x", "TAGEN", "Kalendertage", "Vollstaendige_Woche"]]

    start_date = f"{selected_year}-01-01"
    end_date = f"{selected_year + 1}-01-01"
    df = duck_query_df(
        '''
        SELECT
            MIN(CAST("/BIC/YBWRELAD" AS DATE)) AS x,
            SUM(TRY_CAST("/BIC/YBWRFTAGE" AS DOUBLE)) AS TAGEN,
            COUNT(DISTINCT CAST("/BIC/YBWRELAD" AS DATE)) AS Kalendertage
        FROM raw_bestand
        WHERE CAST("/BIC/YBWRELAD" AS DATE) >= CAST(? AS DATE)
          AND CAST("/BIC/YBWRELAD" AS DATE) < CAST(? AS DATE)
        GROUP BY DATE_TRUNC('week', CAST("/BIC/YBWRELAD" AS DATE))
        ORDER BY 1
        ''',
        [start_date, end_date],
    )
    if df.empty:
        return pd.DataFrame(columns=["x", "TAGEN", "Kalendertage", "Vollstaendige_Woche"])
    df["x"] = pd.to_datetime(df["x"], errors="coerce")
    df["TAGEN"] = pd.to_numeric(df["TAGEN"], errors="coerce").fillna(0)
    df["Kalendertage"] = pd.to_numeric(df["Kalendertage"], errors="coerce").fillna(0).astype(int)
    df["Vollstaendige_Woche"] = df["Kalendertage"] == 7
    return df


def get_monitoring_grid_data(
    year: int | None,
    date_basis: str = "elad",
) -> tuple[list[dict], list[dict]]:
    selected_year = int(year) if year is not None else pd.Timestamp.today().year
    start_date = f"{selected_year}-01-01"
    end_date = f"{selected_year + 1}-01-01"
    if date_basis == "ipl":
        date_filter = 'SUBSTR(CAST("/BIC/YBWRIPL" AS VARCHAR), 1, 4) = ?'
        date_params = [str(selected_year)]
    else:
        date_filter = '''
            CAST("/BIC/YBWRELAD" AS DATE) >= CAST(? AS DATE)
            AND CAST("/BIC/YBWRELAD" AS DATE) < CAST(? AS DATE)
        '''
        date_params = [start_date, end_date]
    ergebnis_map = {
        "001": "offen",
        "002": "Fami erfasst",
        "003": "Mitgliedschaft erfasst",
        "004": "storno",
        "005": "Meldegründe",
        "006": "FIM erfasst",
        "007": "sonstige",
        "008": "Versichertenart 5XX erfasst",
    }
    category_columns = [
        "FIM erfasst",
        "Fami erfasst",
        "Meldegründe",
        "Mitgliedschaft erfasst",
        "Versichertenart 5XX erfasst",
        "offen",
        "sonstige",
        "storno",
    ]

    aggregated = duck_query_df(
        f'''
        SELECT
            CAST("/BIC/YBWRSV" AS VARCHAR) AS SV,
            CAST("/BIC/YBWRERGBN" AS VARCHAR) AS Ergebnis,
            SUM(TRY_CAST("/BIC/YBWRFTAGE" AS BIGINT)) AS Lueckentage
        FROM raw_bestand
        WHERE {date_filter}
        GROUP BY 1, 2
        ORDER BY 1, 2
        ''',
        date_params,
    )

    if aggregated.empty:
        return [], []

    aggregated["Ergebnisschlüssel"] = aggregated["Ergebnis"].map(ergebnis_map)
    aggregated = aggregated.dropna(subset=["Ergebnisschlüssel"])
    pivot = aggregated.pivot_table(
        index="SV",
        columns="Ergebnisschlüssel",
        values="Lueckentage",
        aggfunc="sum",
        fill_value=0,
    ).reindex(columns=category_columns, fill_value=0)

    pivot.loc["Gesamtergebnis"] = pivot.sum(axis=0)
    pivot["Potential"] = pivot[category_columns].sum(axis=1)
    pivot["Ertrag"] = pivot[
        ["Versichertenart 5XX erfasst", "Mitgliedschaft erfasst", "Fami erfasst"]
    ].sum(axis=1)
    pivot["Ertragsanteil"] = (
        pivot["Ertrag"].div(pivot["Potential"].replace(0, np.nan)).mul(100).fillna(0).round(0)
    )
    pivot["11 Euro pro Tag"] = pivot["Ertrag"] * EURO_PER_LUECKENTAG
    pivot["Potential_x_Ertragsanteil"] = (
        pivot["Potential"] * pivot["Ertragsanteil"] / 100
    ).round(0)
    pivot["Potential_x_Ertragsanteil-Euro"] = (
        pivot["Potential_x_Ertragsanteil"] * EURO_PER_LUECKENTAG
    )

    display_df = pivot.reset_index()
    integer_columns = [
        *category_columns,
        "Potential",
        "Ertrag",
        "11 Euro pro Tag",
        "Potential_x_Ertragsanteil",
        "Potential_x_Ertragsanteil-Euro",
    ]
    for column in integer_columns:
        display_df[column] = display_df[column].round(0).astype("int64")
    display_df["Ertragsanteil"] = display_df["Ertragsanteil"].astype("int64")

    total_row_style = {
        "styleConditions": [
            {
                "condition": "params.data && params.data.SV === 'Gesamtergebnis'",
                "style": {
                    "fontWeight": "bold",
                    "backgroundColor": "#e8f1f8",
                    "borderTop": "2px solid #1179b8",
                },
            }
        ]
    }

    def numeric_column(column: str) -> dict:
        definition = {
            "headerName": column,
            "field": column,
            "minWidth": 165,
            "wrapHeaderText": True,
            "autoHeaderHeight": True,
            "type": "numericColumn",
            "cellStyle": total_row_style,
        }
        if column == "Ertragsanteil":
            definition["valueFormatter"] = {
                "function": "params.value == null ? '' : params.value + ' %'"
            }
        if column in {"11 Euro pro Tag", "Potential_x_Ertragsanteil-Euro"}:
            definition["valueFormatter"] = {
                "function": "params.value == null ? '' : params.value.toLocaleString('de-DE') + ' €'"
            }
        if column == "11 Euro pro Tag":
            definition["headerName"] = "11 € pro Tag"
        if column == "Potential_x_Ertragsanteil-Euro":
            definition["headerName"] = "Erwarteter Ertrag (€)"
        if column in category_columns:
            definition["headerClass"] = "pivot-highlighted-header"
        else:
            definition["headerClass"] = "pivot-summary-header"
        return definition

    column_defs = [
        {
            "headerName": "RSA 4.15.1 - SV",
            "field": "SV",
            "minWidth": 190,
            "pinned": "left",
            "headerClass": "pivot-index-header",
            "cellStyle": total_row_style,
        },
        {
            "headerName": "Ergebnisschlüssel",
            "headerClass": "pivot-highlighted-header-group",
            "marryChildren": True,
            "children": [numeric_column(column) for column in category_columns],
        },
        *[
            numeric_column(column)
            for column in [
                "Potential",
                "Ertrag",
                "Ertragsanteil",
                "11 Euro pro Tag",
                "Potential_x_Ertragsanteil",
                "Potential_x_Ertragsanteil-Euro",
            ]
        ],
    ]
    return display_df.to_dict("records"), column_defs
