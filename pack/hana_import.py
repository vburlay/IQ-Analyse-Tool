from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from pack.config import (
    CASE_EXTRA_KEY_COLUMNS,
    CASE_EXTRA_KEY_LENGTH,
    CASE_REQUIRED_KEY_COLUMNS,
    DB_PATH,
    HANA_HOST,
    HANA_PORT,
    HANA_SCHEMA,
    HANA_TABLE,
    MSD_COLUMN,
    RAW_BESTAND_COLUMNS,
    TABLE_RAW,
    WVGRD_COLUMN,
    WVGRD_LENGTH,
)

from pack.data_access import database_connection
from pack.forecast_runs import SCHEMA, clear_forecast_runs
from pack.services.quality_service import HANA_CONTROL_TABLE


class HanaImportError(RuntimeError):
    """A user-safe SAP HANA import error."""


def _quote_identifier(identifier: str) -> str:
    if not identifier or not re.fullmatch(r"[A-Za-z0-9_/$#.-]+", identifier):
        raise HanaImportError("SAP-HANA-Schema oder -Tabelle ist ungültig.")
    return '"' + identifier.replace('"', '""') + '"'


def _empty_raw_bestand_frame() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="string") for column in RAW_BESTAND_COLUMNS})


def validate_raw_bestand(data: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in RAW_BESTAND_COLUMNS if column not in data.columns]
    if missing:
        raise HanaImportError("Pflichtfelder fehlen: " + ", ".join(missing))

    validated = data.loc[:, list(RAW_BESTAND_COLUMNS)].copy()
    if validated.empty:
        raise HanaImportError("Die SAP-HANA-Tabelle enthält keine Daten.")

    required_keys = list(CASE_REQUIRED_KEY_COLUMNS)
    missing_key = validated[required_keys].isna() | validated[required_keys].astype(str).apply(
        lambda column: column.str.strip().eq("")
    )
    if missing_key.any(axis=None):
        raise HanaImportError("BWPKKD oder Ankerbeginn enthält Leerwerte.")
    for column in CASE_EXTRA_KEY_COLUMNS:
        values = validated[column].astype("string")
        values = values.mask(values.str.strip().eq("").fillna(False))
        if values.str.len().gt(CASE_EXTRA_KEY_LENGTH).any():
            raise HanaImportError(f"{column}: maximal {CASE_EXTRA_KEY_LENGTH} Zeichen erlaubt.")
        validated[column] = values

    reason = validated[WVGRD_COLUMN].astype("string")
    if reason.str.len().gt(WVGRD_LENGTH).any():
        raise HanaImportError(f"{WVGRD_COLUMN}: maximal {WVGRD_LENGTH} Zeichen erlaubt.")
    validated[WVGRD_COLUMN] = reason

    for column, label in (
        ("/BIC/YBWRAVON", "Ankerbeginn"),
        ("/BIC/YBWRELAD", "Erstes Ladedatum"),
        ("/BIC/YBWRABIS", "Ankerende"),
        (MSD_COLUMN, "YBWRMSD"),
    ):
        values = validated[column].astype("string").str.strip()
        if column == MSD_COLUMN:
            values = values.mask(values.isin(["", "00000000", "0000-00-00", "00.00.0000"]))
        dotted = values.str.fullmatch(r"\d{2}\.\d{2}\.\d{4}", na=False)
        dates = pd.to_datetime(values.where(~dotted), format="mixed", errors="coerce")
        dates.loc[dotted] = pd.to_datetime(
            values.loc[dotted], format="%d.%m.%Y", errors="coerce"
        )
        invalid = dates.isna()
        if column == MSD_COLUMN:
            invalid &= values.notna()
        invalid_count = int(invalid.sum())
        if invalid_count:
            raise HanaImportError(
                f"{label} ({column}): {invalid_count} fehlende oder ungültige Datumswerte."
            )
        validated[column] = dates.dt.date

    ankerbeginn = validated["/BIC/YBWRAVON"]
    erstes_ladedatum = validated["/BIC/YBWRELAD"]
    if (erstes_ladedatum < ankerbeginn).any():
        raise HanaImportError("Erstes Ladedatum liegt vor Ankerbeginn.")

    lueckentage = pd.to_numeric(validated["/BIC/YBWRFTAGE"], errors="coerce")
    if lueckentage.isna().any():
        raise HanaImportError("Das Feld /BIC/YBWRFTAGE enthält ungültige Werte.")
    return validated


def _replace_raw_data(data: pd.DataFrame) -> None:
    controls = data.attrs.get("hana_controls")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with database_connection(path=DB_PATH) as connection:
        connection.begin()
        try:
            connection.register("raw_bestand_import", data)
            columns_sql = ", ".join(
                f'CAST("{column}" AS DATE) AS "{column}"'
                if column in ("/BIC/YBWRAVON", MSD_COLUMN) else f'"{column}"'
                for column in RAW_BESTAND_COLUMNS
            )
            connection.execute(
                f'CREATE OR REPLACE TABLE "{TABLE_RAW}" AS SELECT {columns_sql} FROM raw_bestand_import'
            )
            imported = connection.execute(
                f'SELECT COUNT(*) FROM "{TABLE_RAW}"'
            ).fetchone()[0]
            if imported != len(data):
                raise HanaImportError("Die lokale Datenprüfung ist fehlgeschlagen.")
            connection.execute(f"DROP TABLE IF EXISTS {HANA_CONTROL_TABLE}")
            if controls is not None:
                local = connection.execute(
                    f'SELECT COUNT(*), SUM(CAST("/BIC/YBWRFTAGE" AS DECIMAL(38, 6))) FROM "{TABLE_RAW}"'
                ).fetchone()
                if local != controls:
                    raise HanaImportError(
                        "SAP-HANA-Abgleich fehlgeschlagen. Lokale Daten bleiben unverändert."
                    )
                connection.execute(
                    f"CREATE TABLE {HANA_CONTROL_TABLE} (row_count BIGINT, day_sum DECIMAL(38, 6))"
                )
                connection.execute(f"INSERT INTO {HANA_CONTROL_TABLE} VALUES (?, ?)", controls)
            connection.execute(SCHEMA)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def ensure_local_database() -> bool:
    if not Path(DB_PATH).is_file():
        _replace_raw_data(_empty_raw_bestand_frame())
    else:
        with database_connection(path=DB_PATH) as connection:
            connection.begin()
            try:
                columns = {row[0]: row[1] for row in connection.execute(f'DESCRIBE "{TABLE_RAW}"').fetchall()}
                if "/B99/S_BWSTKZ" in columns:
                    connection.execute(f'ALTER TABLE "{TABLE_RAW}" DROP COLUMN "/B99/S_BWSTKZ"')
                new_columns = {
                    **dict.fromkeys(CASE_EXTRA_KEY_COLUMNS, f"VARCHAR({CASE_EXTRA_KEY_LENGTH})"),
                    WVGRD_COLUMN: f"VARCHAR({WVGRD_LENGTH})",
                    MSD_COLUMN: columns["/BIC/YBWRAVON"],
                }
                for column, column_type in new_columns.items():
                    if column not in columns:
                        connection.execute(
                            f'ALTER TABLE "{TABLE_RAW}" ADD COLUMN "{column}" {column_type}'
                        )
                if new_columns.keys() - columns.keys():
                    connection.execute(f"DROP TABLE IF EXISTS {HANA_CONTROL_TABLE}")
                connection.execute(SCHEMA)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    return True


def clear_local_database() -> None:
    ensure_local_database()
    with database_connection(path=DB_PATH) as connection:
        connection.begin()
        try:
            connection.execute(f'DELETE FROM "{TABLE_RAW}"')
            connection.execute(f"DROP TABLE IF EXISTS {HANA_CONTROL_TABLE}")
            clear_forecast_runs(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _load_hana_frame(username: str, password: str) -> pd.DataFrame:
    if not all((HANA_HOST, HANA_PORT, HANA_SCHEMA, HANA_TABLE)):
        raise HanaImportError(
            "SAP HANA ist noch nicht konfiguriert. Host, Port, Schema und Tabelle fehlen."
        )
    if not username or not password:
        raise HanaImportError("HANA-Benutzer und Passwort sind erforderlich.")

    try:
        from hdbcli import dbapi
    except ImportError as exc:
        raise HanaImportError("Das Python-Paket hdbcli ist nicht installiert.") from exc

    columns_sql = ", ".join(_quote_identifier(column) for column in RAW_BESTAND_COLUMNS)
    source_sql = f"{_quote_identifier(HANA_SCHEMA)}.{_quote_identifier(HANA_TABLE)}"
    query = f'''
        SELECT {columns_sql},
               COUNT(*) OVER () AS CONTROL_ROWS,
               SUM(CAST("/BIC/YBWRFTAGE" AS DECIMAL(38, 6))) OVER () AS CONTROL_DAYS
        FROM {source_sql}
        WHERE "/BIC/YBWRSEL" = ? AND "/BIC/YBWRIQIQ" = ?
          AND CASE
                WHEN "/BIC/YBWRABIS" < "/BIC/YBWRAVON"
                 AND (
                    TRIM(CAST("{WVGRD_COLUMN}" AS NVARCHAR(3))) IN ('101', '106', '107')
                    OR COALESCE(TRIM(CAST("{MSD_COLUMN}" AS NVARCHAR(32))), '')
                       NOT IN ('', '00000000', '0000-00-00', '00.00.0000')
                 )
                THEN 0 ELSE 1
              END = 1
    '''

    connection = None
    cursor = None
    try:
        connection = dbapi.connect(
            address=HANA_HOST,
            port=HANA_PORT,
            user=username,
            password=password,
        )
        cursor = connection.cursor()
        cursor.execute(query, ("4.15.1", "RSA"))
        frames: list[pd.DataFrame] = []
        controls = None
        while True:
            rows = cursor.fetchmany(10_000)
            if not rows:
                break
            controls = tuple(rows[0][-2:])
            frames.append(pd.DataFrame.from_records(
                [row[:-2] for row in rows], columns=RAW_BESTAND_COLUMNS
            ))
        data = pd.concat(frames, ignore_index=True) if frames else _empty_raw_bestand_frame()
        data.attrs["hana_controls"] = controls
        return data
    except Exception as exc:
        raise HanaImportError("Anmeldung oder Datenabruf bei SAP HANA fehlgeschlagen.") from exc
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def refresh_from_hana(username: str, password: str) -> int:
    """Load HANA data and atomically replace local raw_bestand on success."""
    fresh_data = validate_raw_bestand(_load_hana_frame(username, password))
    _replace_raw_data(fresh_data)
    return len(fresh_data)
