from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from pack.config import (
    DB_PATH,
    HANA_HOST,
    HANA_PORT,
    HANA_SCHEMA,
    HANA_TABLE,
    RAW_BESTAND_COLUMNS,
    TABLE_RAW,
)


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

    required_keys = ["/B99/S_BWPKKD", "/BIC/YBWRAVON"]
    missing_key = validated[required_keys].isna() | validated[required_keys].astype(str).apply(
        lambda column: column.str.strip().eq("")
    )
    if missing_key.any(axis=None):
        raise HanaImportError("Der Fallschlüssel BWPKKD + Ankerbeginn enthält Leerwerte.")

    for column, label in (
        ("/BIC/YBWRAVON", "Ankerbeginn"),
        ("/BIC/YBWRELAD", "Erstes Ladedatum"),
        ("/BIC/YBWRABIS", "Ankerende"),
    ):
        values = validated[column].astype("string").str.strip()
        dotted = values.str.fullmatch(r"\d{2}\.\d{2}\.\d{4}", na=False)
        dates = pd.to_datetime(values.where(~dotted), format="mixed", errors="coerce")
        dates.loc[dotted] = pd.to_datetime(
            values.loc[dotted], format="%d.%m.%Y", errors="coerce"
        )
        invalid_count = int(dates.isna().sum())
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


def _write_replacement_database(data: pd.DataFrame) -> None:
    target = Path(DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".db", dir=target.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    try:
        connection = duckdb.connect(str(temporary_path))
        try:
            connection.register("raw_bestand_import", data)
            connection.execute(
                f'CREATE TABLE "{TABLE_RAW}" AS SELECT * FROM raw_bestand_import'
            )
            imported = connection.execute(
                f'SELECT COUNT(*) FROM "{TABLE_RAW}"'
            ).fetchone()[0]
            if imported != len(data):
                raise HanaImportError("Die lokale Datenprüfung ist fehlgeschlagen.")
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def ensure_local_database() -> bool:
    """Create a structurally valid but empty local database if none exists."""
    if Path(DB_PATH).is_file():
        return True
    _write_replacement_database(_empty_raw_bestand_frame())
    return True


def clear_local_database() -> None:
    """Replace DuckDB with a new database containing an empty raw_bestand."""
    _write_replacement_database(_empty_raw_bestand_frame())


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
        SELECT {columns_sql}
        FROM {source_sql}
        WHERE "/BIC/YBWRSEL" = ? AND "/BIC/YBWRIQIQ" = ?
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
        while True:
            rows = cursor.fetchmany(10_000)
            if not rows:
                break
            frames.append(pd.DataFrame.from_records(rows, columns=RAW_BESTAND_COLUMNS))
        return pd.concat(frames, ignore_index=True) if frames else _empty_raw_bestand_frame()
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
    _write_replacement_database(fresh_data)
    return len(fresh_data)
