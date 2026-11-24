import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

from pack.config import RAW_BESTAND_COLUMNS
from pack.hana_import import (
    HanaImportError, _load_hana_frame, clear_local_database, ensure_local_database, validate_raw_bestand,
)


class HanaImportTests(unittest.TestCase):
    def test_hana_filter_excludes_only_reversed_periods_with_reason_or_msd(self):
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE SCHEMA sap")
        columns_sql = ", ".join(f'"{column}" VARCHAR' for column in RAW_BESTAND_COLUMNS)
        connection.execute(f"CREATE TABLE sap.source({columns_sql})")
        connection.executemany(
            '''INSERT INTO sap.source (
                "/BIC/YBWRSEL", "/BIC/YBWRIQIQ", "/BIC/YBWRAVON",
                "/BIC/YBWRABIS", "/BIC/YBWRWVGRD", "/BIC/YBWRMSD", "/BIC/YBWRFTAGE"
            ) VALUES ('4.15.1', 'RSA', '20260101', ?, ?, ?, ?)''',
            [
                ("20251231", "101", None, "1"),
                ("20251231", "106", None, "2"),
                ("20251231", "107", None, "3"),
                ("20251231", None, "20260201", "4"),
                ("20251231", "999", "00000000", "10"),
                ("20261231", "101", "20260201", "20"),
                (None, "107", "20260201", "30"),
            ],
        )
        with (
            patch.multiple("pack.hana_import", HANA_HOST="test", HANA_PORT=30015,
                           HANA_SCHEMA="sap", HANA_TABLE="source"),
            patch("hdbcli.dbapi.connect", return_value=connection),
        ):
            frame = _load_hana_frame("test", "test")
        self.assertEqual(set(frame["/BIC/YBWRFTAGE"]), {"10", "20", "30"})
        self.assertEqual(frame.attrs["hana_controls"], (3, 60))

    def test_validation_requires_all_application_columns(self):
        with self.assertRaises(HanaImportError):
            validate_raw_bestand(pd.DataFrame({"/B99/S_BWPKKD": ["1"]}))

    def test_validation_keeps_expected_column_order(self):
        values = {column: ["x"] for column in reversed(RAW_BESTAND_COLUMNS)}
        values["/BIC/YBWRAVON"] = ["2025-01-01"]
        values["/BIC/YBWRELAD"] = ["2025-02-01"]
        values["/BIC/YBWRABIS"] = ["02.04.2025"]
        values["/BIC/YBWRFTAGE"] = ["10"]
        values["/BIC/YBWRMSD"] = ["20250502"]
        values["/BIC/YBWRWVGRD"] = ["001"]
        data = pd.DataFrame(values)
        result = validate_raw_bestand(data)
        self.assertEqual(list(result.columns), list(RAW_BESTAND_COLUMNS))
        self.assertEqual(result["/BIC/YBWRABIS"].iloc[0].isoformat(), "2025-04-02")
        self.assertEqual(result["/BIC/YBWRMSD"].iloc[0].isoformat(), "2025-05-02")
        self.assertEqual(result["/BIC/YBWRWVGRD"].iloc[0], "001")

    def test_validation_rejects_load_date_before_anchor_start(self):
        values = {column: ["x"] for column in RAW_BESTAND_COLUMNS}
        values["/BIC/YBWRAVON"] = ["2025-02-01"]
        values["/BIC/YBWRELAD"] = ["2025-01-01"]
        values["/BIC/YBWRABIS"] = ["20250402"]
        values["/BIC/YBWRFTAGE"] = ["10"]
        values["/BIC/YBWRMSD"] = ["20250502"]
        values["/BIC/YBWRWVGRD"] = ["001"]
        with self.assertRaisesRegex(HanaImportError, "liegt vor Ankerbeginn"):
            validate_raw_bestand(pd.DataFrame(values))

    def test_clear_preserves_empty_raw_table_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.db"
            connection = duckdb.connect(str(database))
            columns_sql = ", ".join(f'"{column}" VARCHAR' for column in RAW_BESTAND_COLUMNS)
            connection.execute(f"CREATE TABLE raw_bestand({columns_sql})")
            connection.execute("INSERT INTO raw_bestand DEFAULT VALUES")
            connection.close()

            with patch("pack.hana_import.DB_PATH", str(database)):
                clear_local_database()

            connection = duckdb.connect(str(database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM raw_bestand").fetchone()[0],
                    0,
                )
                columns = connection.execute("DESCRIBE raw_bestand").fetchdf()["column_name"]
                self.assertEqual(list(columns), list(RAW_BESTAND_COLUMNS))
            finally:
                connection.close()

    def test_schema_upgrade_removes_obsolete_field_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.db"
            columns = (*RAW_BESTAND_COLUMNS[:-2], "/B99/S_BWSTKZ")
            columns_sql = ", ".join(f'"{column}" VARCHAR' for column in columns)
            with duckdb.connect(str(database)) as connection:
                connection.execute(f"CREATE TABLE raw_bestand({columns_sql})")
                connection.execute('INSERT INTO raw_bestand ("/B99/S_BWPKKD") VALUES (?)', ["case-1"])

            with patch("pack.hana_import.DB_PATH", str(database)):
                ensure_local_database()

            with duckdb.connect(str(database), read_only=True) as connection:
                actual_columns = connection.execute("DESCRIBE raw_bestand").fetchdf()["column_name"]
                self.assertEqual(list(actual_columns), list(RAW_BESTAND_COLUMNS))
                self.assertEqual(connection.execute('SELECT "/B99/S_BWPKKD" FROM raw_bestand').fetchall(), [("case-1",)])


if __name__ == "__main__":
    unittest.main()
