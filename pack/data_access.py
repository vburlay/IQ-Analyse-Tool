from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
import time
from typing import Any, Optional

import duckdb
import pandas as pd

from pack.config import (
    ANALYSIS_START_YEAR,
    CASE_EXTRA_KEY_COLUMNS,
    CASE_KEY_COLUMNS,
    DB_PATH,
    DB_RETRIES,
    DB_RETRY_DELAY,
    TABLE_RAW,
)


DATABASE_LOCK = RLock()
CASE_EXTRA_KEY_SQL = ", ".join(f'"{column}"' for column in CASE_EXTRA_KEY_COLUMNS)
CASE_EXTRA_KEY_SELECT = ", ".join(
    f'''CASE WHEN TRIM(CAST("{column}" AS VARCHAR)) = ''
         THEN NULL ELSE CAST("{column}" AS VARCHAR) END AS "{column}"'''
    for column in CASE_EXTRA_KEY_COLUMNS
)
ANCHOR_YEAR_OVERLAP_SQL = '''CAST("/BIC/YBWRAVON" AS DATE) <= CAST(? AS DATE)
                             AND CAST("/BIC/YBWRABIS" AS DATE) >= CAST(? AS DATE)'''


@contextmanager
def database_connection(*, read_only=False, path=None):
    with DATABASE_LOCK, duckdb.connect(path or DB_PATH, read_only=read_only) as connection:
        yield connection


def duck_query_df(
    sql: str,
    params: Optional[list[Any]] = None,
    retries: int = DB_RETRIES,
    delay: float = DB_RETRY_DELAY,
    read_only: bool = True,
) -> pd.DataFrame:
    """
    Execute SQL against DuckDB and return a pandas DataFrame.
    """
    last_error: Optional[Exception] = None

    for _ in range(retries):
        try:
            with database_connection(read_only=read_only) as connection:
                return connection.execute(sql, params or []).df()
        except Exception as exc:
            last_error = exc
            time.sleep(delay)

    if last_error is not None:
        raise last_error

    raise RuntimeError("DuckDB query failed without captured exception.")


def load_raw_weekly_data(sachverhalt: str = "all") -> pd.DataFrame:
    """Aggregate raw Lückentage by IPL for one Sachverhalt or for all values."""
    selected = str(sachverhalt or "all")
    sql = f"""
    SELECT
        STRFTIME(STRPTIME(CAST("/BIC/YBWRIPL" AS VARCHAR), '%Y%m%d'), '%Y-%m-%d') AS IPL,
        SUM(TRY_CAST("/BIC/YBWRFTAGE" AS DOUBLE)) AS TAGEN
    FROM {TABLE_RAW}
    WHERE (? = 'all' OR CAST("/BIC/YBWRSV" AS VARCHAR) = ?)
    GROUP BY 1
    ORDER BY 1
    """
    df = duck_query_df(sql, [selected, selected])

    if df.empty:
        return df

    out = df.copy()
    out["TAGEN"] = pd.to_numeric(out["TAGEN"], errors="coerce").fillna(0)
    out["IPL_dt"] = pd.to_datetime(out["IPL"], errors="coerce")
    return out


def load_anchor_monitoring_cases(year: int, *, raw: bool = False) -> pd.DataFrame:
    """Select a single source version for each case overlapping the anchor year."""
    source_fields = f"* EXCLUDE ({CASE_EXTRA_KEY_SQL})," if raw else ""
    return duck_query_df(
        f'''
        WITH eligible AS (
            SELECT
                {source_fields}
                "/B99/S_BWPKKD" AS case_id,
                CAST("/BIC/YBWRAVON" AS DATE) AS anchor_start,
                {CASE_EXTRA_KEY_SELECT},
                CAST("/BIC/YBWRELAD" AS DATE) AS first_load,
                CAST("/BIC/YBWRIPL" AS VARCHAR) AS ipl,
                CAST("/BIC/YBWRSV" AS VARCHAR) AS SV,
                CAST("/BIC/YBWRERGBN" AS VARCHAR) AS Ergebnis,
                TRY_CAST("/BIC/YBWRFTAGE" AS DOUBLE) AS days
            FROM {TABLE_RAW}
            WHERE {ANCHOR_YEAR_OVERLAP_SQL}
        )
        SELECT {"*" if raw else "first_load, SV, Ergebnis, days"}
        FROM eligible
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY case_id, anchor_start, {CASE_EXTRA_KEY_SQL}
            ORDER BY ipl DESC NULLS LAST, first_load, days, SV, Ergebnis
        ) = 1
        ''',
        [f"{year}-12-31", f"{year}-01-01"],
    )


def load_anchor_cohort_weekly_data(
    sachverhalt: str = "all",
    anchor_year: int | str | None = "all",
) -> pd.DataFrame:
    """Load unique anchor-overlap cases grouped by their erstes Ladedatum."""
    selected_sachverhalt = str(sachverhalt or "all")
    params: list[Any] = []
    sachverhalt_filter = ""
    if selected_sachverhalt != "all":
        sachverhalt_filter = 'AND CAST("/BIC/YBWRSV" AS VARCHAR) = ?'
        params.append(selected_sachverhalt)
    if anchor_year not in (None, "all"):
        selected_year = int(anchor_year)
        anchor_filter = f'''
          AND CAST("/BIC/YBWRAVON" AS DATE) <= DATE '{selected_year}-12-31'
          AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '{selected_year}-01-01'
        '''
    else:
        anchor_filter = f"""AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '{ANALYSIS_START_YEAR}-01-01'"""

    sql = f'''
    WITH selected AS (
        SELECT
            "/B99/S_BWPKKD" AS bw_case_id,
            CAST("/BIC/YBWRAVON" AS DATE) AS ankerbeginn,
            {CASE_EXTRA_KEY_SELECT},
            CAST("/BIC/YBWRELAD" AS DATE) AS erstes_ladedatum,
            TRY_CAST("/BIC/YBWRFTAGE" AS DOUBLE) AS lueckentage,
            STRPTIME(CAST("/BIC/YBWRIPL" AS VARCHAR), '%Y%m%d') AS ipl_datum
        FROM {TABLE_RAW}
        WHERE TRUE
        {sachverhalt_filter}
        {anchor_filter}
    ), latest_cases AS (
        SELECT
            bw_case_id,
            ankerbeginn,
            ANY_VALUE(erstes_ladedatum) AS erstes_ladedatum,
            MAX(erstes_ladedatum) AS latest_load_date,
            ARG_MAX(lueckentage, ipl_datum) AS lueckentage
        FROM selected
        GROUP BY bw_case_id, ankerbeginn, {CASE_EXTRA_KEY_SQL}
    )
    SELECT
        STRFTIME(
            DATE_TRUNC('week', erstes_ladedatum),
            '%Y-%m-%d'
        ) AS IPL,
        SUM(lueckentage) AS TAGEN,
        COUNT(*) AS FAELLE,
        MAX(latest_load_date) AS data_cutoff
    FROM latest_cases
    GROUP BY 1
    ORDER BY 1
    '''
    df = duck_query_df(sql, params)
    if df.empty:
        return df

    out = df.copy()
    out["TAGEN"] = pd.to_numeric(out["TAGEN"], errors="coerce").fillna(0)
    out["FAELLE"] = pd.to_numeric(out["FAELLE"], errors="coerce").fillna(0).astype(int)
    out["IPL_dt"] = pd.to_datetime(out["IPL"], errors="coerce")
    return out


def load_anchor_cohort_case_data(
    sachverhalt: str = "all",
    anchor_year: int | str | None = "all",
) -> pd.DataFrame:
    """Return one latest record per selected case for diagnostics."""
    selected_sachverhalt = str(sachverhalt or "all")
    params: list[Any] = []
    sachverhalt_filter = ""
    if selected_sachverhalt != "all":
        sachverhalt_filter = 'AND CAST("/BIC/YBWRSV" AS VARCHAR) = ?'
        params.append(selected_sachverhalt)

    anchor_filter = f"""AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '{ANALYSIS_START_YEAR}-01-01'"""
    if anchor_year not in (None, "all"):
        selected_year = int(anchor_year)
        anchor_filter = f'''
          AND CAST("/BIC/YBWRAVON" AS DATE) <= DATE '{selected_year}-12-31'
          AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '{selected_year}-01-01'
        '''

    sql = f'''
    WITH selected AS (
        SELECT
            "/B99/S_BWPKKD" AS bw_case_id,
            CAST("/BIC/YBWRAVON" AS DATE) AS ankerbeginn,
            {CASE_EXTRA_KEY_SELECT},
            CAST("/BIC/YBWRELAD" AS DATE) AS erstes_ladedatum,
            TRY_CAST("/BIC/YBWRFTAGE" AS DOUBLE) AS lueckentage,
            STRPTIME(CAST("/BIC/YBWRIPL" AS VARCHAR), '%Y%m%d') AS ipl_datum
        FROM {TABLE_RAW}
        WHERE TRUE
        {sachverhalt_filter}
        {anchor_filter}
    )
    SELECT
        bw_case_id,
        ankerbeginn,
        {CASE_EXTRA_KEY_SQL},
        ANY_VALUE(erstes_ladedatum) AS erstes_ladedatum,
        ARG_MAX(lueckentage, ipl_datum) AS lueckentage
    FROM selected
    GROUP BY bw_case_id, ankerbeginn, {CASE_EXTRA_KEY_SQL}
    ORDER BY erstes_ladedatum, bw_case_id, ankerbeginn
    '''
    df = duck_query_df(sql, params)
    if not df.empty:
        df["erstes_ladedatum"] = pd.to_datetime(df["erstes_ladedatum"], errors="coerce")
        df["lueckentage"] = pd.to_numeric(df["lueckentage"], errors="coerce")
    return df


def load_anchor_year_diagnostics(anchor_year: int) -> pd.DataFrame:
    """Return the unique overlap cohort with a transparent selection reason."""
    selected_year = int(anchor_year)
    sql = f'''
    WITH selected AS (
        SELECT
            "/B99/S_BWPKKD" AS bw_case_id,
            CAST("/BIC/YBWRAVON" AS DATE) AS ankerbeginn,
            {CASE_EXTRA_KEY_SELECT},
            CAST("/BIC/YBWRABIS" AS DATE) AS ankerende,
            CAST("/BIC/YBWRELAD" AS DATE) AS erstes_ladedatum,
            STRPTIME(CAST("/BIC/YBWRIPL" AS VARCHAR), '%Y%m%d') AS ipl_datum
        FROM {TABLE_RAW}
        WHERE CAST("/BIC/YBWRAVON" AS DATE) <= DATE '{selected_year}-12-31'
          AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '{selected_year}-01-01'
    ), latest_cases AS (
        SELECT
            bw_case_id,
            ankerbeginn,
            {CASE_EXTRA_KEY_SQL},
            ARG_MAX(ankerende, ipl_datum) AS ankerende,
            ANY_VALUE(erstes_ladedatum) AS erstes_ladedatum
        FROM selected
        GROUP BY bw_case_id, ankerbeginn, {CASE_EXTRA_KEY_SQL}
    )
    SELECT
        bw_case_id AS "/B99/S_BWPKKD",
        ankerbeginn AS "/BIC/YBWRAVON",
        {CASE_EXTRA_KEY_SQL},
        ankerbeginn,
        ankerende,
        erstes_ladedatum,
        CASE
            WHEN EXTRACT(YEAR FROM ankerbeginn) = {selected_year}
                THEN 'Ankerbeginn im Jahr {selected_year}'
            WHEN ankerbeginn < DATE '{selected_year}-01-01'
                THEN 'Ankerbeginn vor {selected_year}; Ankerende überschneidet {selected_year}'
            ELSE 'Sonstiger Überschneidungsfall'
        END AS "grund_der_auswahl"
    FROM latest_cases
    ORDER BY ankerbeginn, erstes_ladedatum
    '''
    return duck_query_df(sql)


def get_latest_elad_value() -> str:
    """Return the latest erstes Ladedatum as an ISO date string."""
    sql = f'SELECT MAX(CAST("/BIC/YBWRELAD" AS DATE)) AS max_elad FROM {TABLE_RAW}'
    df = duck_query_df(sql)

    if df.empty:
        return "—"

    value = df.iloc[0]["max_elad"]
    if pd.isna(value):
        return "—"

    dt = pd.to_datetime(value, errors="coerce")
    if pd.notna(dt):
        return str(dt.date())

    return str(value)


def get_latest_ipl_value() -> str:
    """Return the latest IPL snapshot date as an ISO date string."""
    sql = f'SELECT MAX(CAST("/BIC/YBWRIPL" AS VARCHAR)) AS max_ipl FROM {TABLE_RAW}'
    df = duck_query_df(sql)

    if df.empty:
        return "—"

    value = df.iloc[0]["max_ipl"]
    if pd.isna(value):
        return "—"

    dt = pd.to_datetime(str(value), format="%Y%m%d", errors="coerce")
    return str(dt.date()) if pd.notna(dt) else str(value)


def load_anomaly_raw_records(week, anchor_year, sachverhalt, expected_cases, expected_days):
    """Return unaggregated latest source records matching the selected weekly cohort.

    Exact duplicate snapshots are represented once. Conflicting latest records
    cannot be resolved from the graph context and are reported to the caller.
    """
    week_start = pd.Timestamp(week).normalize()
    if week_start.weekday() != 0:
        raise ValueError("Die ausgewählte Anomalie muss eine Kalenderwoche bezeichnen.")
    return load_forecast_actual_records(
        week_start, week_start + pd.Timedelta(days=7), anchor_year, sachverhalt,
        expected_cases, expected_days,
    )


def load_forecast_actual_records(start, end, anchor_year, sachverhalt, expected_cases, expected_days):
    from pack.config import RAW_BESTAND_COLUMNS

    params = []
    filters = []
    if sachverhalt != "all":
        filters.append('CAST("/BIC/YBWRSV" AS VARCHAR) = ?')
        params.append(str(sachverhalt))
    if anchor_year != "all":
        year = int(anchor_year)
        filters.extend([
            'CAST("/BIC/YBWRAVON" AS DATE) <= CAST(? AS DATE)',
            'CAST("/BIC/YBWRABIS" AS DATE) >= CAST(? AS DATE)',
        ])
        params.extend([f"{year}-12-31", f"{year}-01-01"])
    else:
        filters.append('CAST("/BIC/YBWRABIS" AS DATE) >= CAST(? AS DATE)')
        params.append(f"{ANALYSIS_START_YEAR}-01-01")
    columns = ", ".join(f'"{column}"' for column in RAW_BESTAND_COLUMNS)
    where = " AND ".join(filters) or "TRUE"
    source = duck_query_df(f'SELECT {columns} FROM {TABLE_RAW} WHERE {where}', params)
    if source.empty:
        raise ValueError("Die Daten wurden geändert. Bitte die Prognose erneut berechnen.")
    keys = list(CASE_KEY_COLUMNS)
    source["_anchor"] = pd.to_datetime(source[keys[1]], errors="raise")
    source["_load"] = pd.to_datetime(source["/BIC/YBWRELAD"], errors="raise")
    source["_ipl"] = pd.to_datetime(source["/BIC/YBWRIPL"].astype(str), format="%Y%m%d", errors="raise")
    for column in CASE_EXTRA_KEY_COLUMNS:
        values = source[column].astype("string")
        source[column] = values.mask(values.str.strip().eq("").fillna(False))
    group_keys = [keys[0], "_anchor", *CASE_EXTRA_KEY_COLUMNS]
    load_counts = source.groupby(group_keys, dropna=False)["_load"].transform("nunique")
    in_period = source["_load"].lt(pd.Timestamp(end))
    if start is not None:
        in_period &= source["_load"].ge(pd.Timestamp(start))
    if (load_counts[in_period] > 1).any():
        raise ValueError("Erstes Ladedatum ist je Fallschlüssel nicht eindeutig. Eine genaue Zuordnung ist nicht möglich.")
    cohort = source.loc[in_period].copy()
    latest = cohort.groupby(group_keys, dropna=False)["_ipl"].transform("max")
    cohort = cohort.loc[cohort["_ipl"].eq(latest)]
    result = cohort.loc[:, list(RAW_BESTAND_COLUMNS)].drop_duplicates()
    if cohort.drop_duplicates(list(RAW_BESTAND_COLUMNS)).duplicated(group_keys).any():
        raise ValueError(
            "Mehrere unterschiedliche Rohdatensätze im letzten IPL je Fall. "
            "Für eine eindeutige Auswahl fehlt ein zusätzlicher Quellschlüssel."
        )
    days = pd.to_numeric(result["/BIC/YBWRFTAGE"], errors="raise").sum()
    if len(result) != int(expected_cases) or abs(float(days) - float(expected_days)) > 0.001:
        raise ValueError(
            "Rohdaten und ausgewählter Istwert stimmen nicht überein. "
            "Bitte die Prognose mit dem aktuellen Datenstand erneut berechnen."
        )
    return result
