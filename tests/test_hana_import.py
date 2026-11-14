import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

from pack.config import RAW_BESTAND_COLUMNS
from pack.hana_import import HanaImportError, clear_local_database, validate_raw_bestand


class HanaImportTests(unittest.TestCase):
    def test_validation_requires_all_application_columns(self):
        with self.assertRaises(HanaImportError):
            validate_raw_bestand(pd.DataFrame({"/B99/S_BWPKKD": ["1"]}))

    def test_validation_keeps_expected_column_order(self):
        values = {column: ["x"] for column in reversed(RAW_BESTAND_COLUMNS)}
        values["/BIC/YBWRAVON"] = ["2025-01-01"]
        values["/BIC/YBWRELAD"] = ["2025-02-01"]
        values["/BIC/YBWRFTAGE"] = ["10"]
        data = pd.DataFrame(values)
        result = validate_raw_bestand(data)
        self.assertEqual(list(result.columns), list(RAW_BESTAND_COLUMNS))

    def test_validation_rejects_load_date_before_anchor_start(self):
        values = {column: ["x"] for column in RAW_BESTAND_COLUMNS}
        values["/BIC/YBWRAVON"] = ["2025-02-01"]
        values["/BIC/YBWRELAD"] = ["2025-01-01"]
        values["/BIC/YBWRFTAGE"] = ["10"]
        with self.assertRaises(HanaImportError):
            validate_raw_bestand(pd.DataFrame(values))

    def test_clear_replaces_database_with_empty_raw_table(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.db"
            connection = duckdb.connect(str(database))
            connection.execute("CREATE TABLE raw_bestand(value INTEGER)")
            connection.execute("INSERT INTO raw_bestand VALUES (1)")
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


if __name__ == "__main__":
    unittest.main()
