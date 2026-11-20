from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
import time
from typing import Any, Optional

import duckdb
import pandas as pd

from pack.config import (
    DB_PATH,
    DB_RETRIES,
    DB_RETRY_DELAY,
    TABLE_RAW,
)


DATABASE_LOCK = RLock()


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
        anchor_filter = ""

    sql = f'''
    WITH selected AS (
        SELECT
            "/B99/S_BWPKKD" AS bw_case_id,
            CAST("/BIC/YBWRAVON" AS DATE) AS ankerbeginn,
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
        GROUP BY bw_case_id, ankerbeginn
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

    anchor_filter = ""
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
        ANY_VALUE(erstes_ladedatum) AS erstes_ladedatum,
        ARG_MAX(lueckentage, ipl_datum) AS lueckentage
    FROM selected
    GROUP BY bw_case_id, ankerbeginn
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
            ARG_MAX(ankerende, ipl_datum) AS ankerende,
            ANY_VALUE(erstes_ladedatum) AS erstes_ladedatum
        FROM selected
        GROUP BY bw_case_id, ankerbeginn
    )
    SELECT
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
    from pack.config import RAW_BESTAND_COLUMNS

    week_start = pd.Timestamp(week).normalize()
    if week_start.weekday() != 0:
        raise ValueError("Die ausgewählte Anomalie muss eine Kalenderwoche bezeichnen.")
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
    columns = ", ".join(f'"{column}"' for column in RAW_BESTAND_COLUMNS)
    where = " AND ".join(filters) or "TRUE"
    source = duck_query_df(f'SELECT {columns} FROM {TABLE_RAW} WHERE {where}', params)
    if source.empty:
        raise ValueError("Die Daten wurden geändert. Bitte die Prognose erneut berechnen.")
    keys = ["/B99/S_BWPKKD", "/BIC/YBWRAVON"]
    source["_anchor"] = pd.to_datetime(source[keys[1]], errors="raise")
    source["_load"] = pd.to_datetime(source["/BIC/YBWRELAD"], errors="raise")
    source["_ipl"] = pd.to_datetime(source["/BIC/YBWRIPL"].astype(str), format="%Y%m%d", errors="raise")
    group_keys = [keys[0], "_anchor"]
    load_counts = source.groupby(group_keys)["_load"].transform("nunique")
    in_week = source["_load"].ge(week_start) & source["_load"].lt(week_start + pd.Timedelta(days=7))
    if (load_counts[in_week] > 1).any():
        raise ValueError("Erstes Ladedatum ist je Fallschlüssel nicht eindeutig. Eine genaue Zuordnung ist nicht möglich.")
    cohort = source.loc[in_week].copy()
    latest = cohort.groupby(group_keys)["_ipl"].transform("max")
    cohort = cohort.loc[cohort["_ipl"].eq(latest)]
    result = cohort.loc[:, list(RAW_BESTAND_COLUMNS)].drop_duplicates()
    if result.duplicated(keys).any():
        raise ValueError(
            "Mehrere unterschiedliche Rohdatensätze im letzten IPL je Fall. "
            "Für eine eindeutige Auswahl fehlt ein zusätzlicher Quellschlüssel."
        )
    days = pd.to_numeric(result["/BIC/YBWRFTAGE"], errors="raise").sum()
    if len(result) != int(expected_cases) or abs(float(days) - float(expected_days)) > 0.001:
        raise ValueError(
            "Rohdaten und ausgewählte Anomalie stimmen nicht überein. "
            "Bitte die Prognose mit dem aktuellen Datenstand erneut berechnen."
        )
    return result
