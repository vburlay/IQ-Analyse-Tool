from __future__ import annotations

import json
import numpy as np
import pandas as pd

from pack.config import CASE_KEY_COLUMNS, EURO_PER_LUECKENTAG, RAW_BESTAND_COLUMNS, TABLE_RAW
from pack.data_access import (
    ANCHOR_YEAR_OVERLAP_SQL,
    duck_query_df,
    get_latest_elad_value,
    get_latest_ipl_value,
    load_anchor_monitoring_cases,
    load_raw_weekly_data,
)
from pack.services.quality_service import DISPLAY_DATES, parse_source_date, text_filter_mask

DSO_PAGE_SIZE = 100
DSO_PAGE_SIZES = (25, 50, 100, 200)


def get_monitoring_stand_text(
    last_stand: str | None = None,
    date_basis: str = "elad",
    *,
    year: int | None = None,
) -> str:
    if date_basis == "anchor":
        return f"Stand Ankerjahr: {year if year is not None else pd.Timestamp.today().year}"
    if date_basis == "ipl":
        return f"Stand IPL: {get_latest_ipl_value()}"
    stand = last_stand or get_latest_elad_value()
    return f"Stand erstes Ladedatum: {stand}"


def get_monitoring_kpis(year: int | None, date_basis: str = "elad", *, rows=None) -> dict[str, str]:
    if rows is None:
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
    *,
    anchor_cases: pd.DataFrame | None = None,
) -> pd.DataFrame:
    selected_year = int(year) if year is not None else pd.Timestamp.today().year
    if date_basis == "anchor":
        cases = load_anchor_monitoring_cases(selected_year) if anchor_cases is None else anchor_cases
        week = pd.to_datetime(cases["first_load"]).dt.to_period("W-SUN").dt.start_time
        chart = cases.groupby(week).agg(TAGEN=("days", "sum"), Kalendertage=("first_load", "nunique"))
        chart["Vollstaendige_Woche"] = chart["Kalendertage"].eq(7)
        return chart.rename_axis("x").reset_index()
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


RESULT_LABELS = {
    "001": "offen",
    "002": "Fami erfasst",
    "003": "Mitgliedschaft erfasst",
    "004": "storno",
    "005": "Meldegründe",
    "006": "FIM erfasst",
    "007": "sonstige",
    "008": "Versichertenart 5XX erfasst",
}
RESULT_CODES = {label: code for code, label in RESULT_LABELS.items()}
ERTRAG_CODES = ("002", "003", "008")
OTHER_RESULTS = "Sonstige Ergebnisschlüssel"


def monitoring_date_filter(year, date_basis):
    if date_basis == "ipl":
        return 'SUBSTR(CAST("/BIC/YBWRIPL" AS VARCHAR), 1, 4) = ?', [str(year)]
    if date_basis == "anchor":
        return ANCHOR_YEAR_OVERLAP_SQL, [f"{year}-12-31", f"{year}-01-01"]
    return (
        '''CAST("/BIC/YBWRELAD" AS DATE) >= CAST(? AS DATE)
           AND CAST("/BIC/YBWRELAD" AS DATE) < CAST(? AS DATE)''',
        [f"{year}-01-01", f"{year + 1}-01-01"],
    )


def get_monitoring_source_rows(year, date_basis, sachverhalt, field="SV"):
    date_filter, params = monitoring_date_filter(year, date_basis)
    if date_basis == "anchor" and field != "SV":
        return anchor_dso_cell_rows(year, sachverhalt, field)

    if sachverhalt != "Gesamtergebnis":
        date_filter += """ AND COALESCE(CAST("/BIC/YBWRSV" AS VARCHAR), '(leer)') = ?"""
        params.append(sachverhalt)
    result = 'CAST("/BIC/YBWRERGBN" AS VARCHAR)'
    if field in RESULT_CODES:
        date_filter += f" AND {result} = ?"
        params.append(RESULT_CODES[field])
    elif field == OTHER_RESULTS:
        placeholders = ", ".join("?" for _ in RESULT_LABELS)
        date_filter += f" AND COALESCE({result}, '') NOT IN ({placeholders})"
        params.extend(RESULT_LABELS)
    elif field in ("Ertrag", "11 Euro pro Tag"):
        placeholders = ", ".join("?" for _ in ERTRAG_CODES)
        date_filter += f" AND {result} IN ({placeholders})"
        params.extend(ERTRAG_CODES)
    elif field not in ("SV", "Potential", "Ertragsanteil", "Potential_x_Ertragsanteil",
                      "Potential_x_Ertragsanteil-Euro"):
        raise ValueError("Unbekannte Spalte der Kreuztabelle")
    if field != "SV":
        placeholders = ", ".join("?" for _ in RESULT_LABELS)
        date_filter += f' AND "/BIC/YBWRSV" IS NOT NULL AND {result} IN ({placeholders})'
        params.extend(RESULT_LABELS)
    columns = ", ".join(f'"{column}"' for column in RAW_BESTAND_COLUMNS)
    day_projection = ', TRY_CAST("/BIC/YBWRFTAGE" AS BIGINT) AS days' if field != "SV" else ""
    return duck_query_df(
        f'SELECT {columns}{day_projection} FROM "{TABLE_RAW}" WHERE {date_filter}',
        params,
    )


def anchor_dso_cell_rows(year, sachverhalt, field):
    # Select the latest version first, just as in the pivot, before filtering the cell.
    cases = load_anchor_monitoring_cases(year, raw=True)
    if sachverhalt != "Gesamtergebnis":
        cases = cases.loc[cases["SV"].fillna("(leer)").eq(sachverhalt)]
    if field in RESULT_CODES:
        return cases.loc[cases["Ergebnis"].eq(RESULT_CODES[field])]
    if field == OTHER_RESULTS:
        return cases.loc[~cases["Ergebnis"].isin(RESULT_LABELS)]
    if field in ("Ertrag", "11 Euro pro Tag"):
        return cases.loc[cases["Ergebnis"].isin(ERTRAG_CODES)]
    if field in ("Potential", "Ertragsanteil", "Potential_x_Ertragsanteil",
                 "Potential_x_Ertragsanteil-Euro"):
        return cases
    raise ValueError("Unbekannte Spalte der Kreuztabelle")


def get_monitoring_dso_page(year, date_basis, sachverhalt, page, filters=None, *,
                            page_size=DSO_PAGE_SIZE, field="SV"):
    source = get_monitoring_source_rows(year, date_basis, sachverhalt, field)
    total_days = (
        pd.to_numeric(source["/BIC/YBWRFTAGE"], errors="coerce").sum()
        if field == "SV" else round(source["days"].sum())
    )

    def date_label(value):
        parsed = parse_source_date(value)
        return parsed.strftime("%d.%m.%Y") if parsed else value

    visible = source
    for column, model in (filters or {}).items():
        if column not in RAW_BESTAND_COLUMNS:
            raise ValueError("Ungültige Filterspalte")
        values = visible[column]
        if column in DISPLAY_DATES:
            values = values.map(date_label)
        visible = visible.loc[text_filter_mask(values, model)]
    pages = max(1, (len(visible) + page_size - 1) // page_size)
    page = pages - 1 if page is None else min(max(page, 0), pages - 1)
    visible = visible.sort_values([*CASE_KEY_COLUMNS, "/BIC/YBWRIPL"], kind="stable")
    batch = visible.iloc[page * page_size:(page + 1) * page_size][list(RAW_BESTAND_COLUMNS)].copy()
    for column in DISPLAY_DATES:
        batch[column] = batch[column].map(date_label)
    records = json.loads(batch.to_json(orient="records", date_format="iso"))
    return records, total_days, page, pages, len(visible), len(source)


def get_monitoring_grid_data(
    year: int | None,
    date_basis: str = "elad",
    *,
    anchor_cases: pd.DataFrame | None = None,
) -> tuple[list[dict], list[dict]]:
    selected_year = int(year) if year is not None else pd.Timestamp.today().year
    date_filter, date_params = monitoring_date_filter(selected_year, date_basis)
    if date_basis == "anchor":
        cases = load_anchor_monitoring_cases(selected_year) if anchor_cases is None else anchor_cases
        aggregated = cases.groupby(["SV", "Ergebnis"], as_index=False, dropna=False).agg(Lueckentage=("days", "sum"))
    else:
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

    return monitoring_pivot(aggregated, date_basis)


def monitoring_pivot(aggregated, date_basis):
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

    if aggregated.empty:
        return [], []

    aggregated["Ergebnisschlüssel"] = aggregated["Ergebnis"].map(RESULT_LABELS)
    if date_basis == "anchor":
        aggregated["SV"] = aggregated["SV"].fillna("(leer)")
        if aggregated["Ergebnisschlüssel"].isna().any():
            category_columns.append(OTHER_RESULTS)
            aggregated["Ergebnisschlüssel"] = aggregated["Ergebnisschlüssel"].fillna(OTHER_RESULTS)
    else:
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
        [RESULT_LABELS[code] for code in ERTRAG_CODES]
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
