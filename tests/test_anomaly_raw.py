import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
from dash import Dash

from pack.config import RAW_BESTAND_COLUMNS
from pack.data_access import load_anomaly_raw_records
from pack.ui.callbacks import register_callbacks


class AnomalyRawTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "test.db")
        self.patch = patch("pack.data_access.DB_PATH", self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        row = {column: "x" for column in RAW_BESTAND_COLUMNS}
        row.update({
            "/B99/S_BWPKKD": "one", "/BIC/YBWRSV": "01a",
            "/BIC/YBWRAVON": "2025-01-01", "/BIC/YBWRABIS": "2025-12-31",
            "/BIC/YBWRELAD": "2025-07-15", "/BIC/YBWRIPL": "20250721",
            "/BIC/YBWRFTAGE": "100",
        })
        self.row = row

    def write(self, rows):
        frame = pd.DataFrame(rows)
        with duckdb.connect(self.path) as connection:
            connection.register("source", frame)
            connection.execute("CREATE OR REPLACE TABLE raw_bestand AS SELECT * FROM source")

    def test_exact_latest_source_row_and_cohort_filters(self):
        older = {**self.row, "/BIC/YBWRIPL": "20250714", "/BIC/YBWRFTAGE": "10"}
        other = {**self.row, "/B99/S_BWPKKD": "two", "/BIC/YBWRSV": "004"}
        self.write([older, self.row, self.row, other])
        result = load_anomaly_raw_records("2025-07-14", 2025, "01a", 1, 100)
        self.assertEqual(result.to_dict("records"), [self.row])
        self.assertEqual(list(result.columns), list(RAW_BESTAND_COLUMNS))
        with self.assertRaisesRegex(ValueError, "stimmen nicht"):
            load_anomaly_raw_records("2025-07-14", 2025, "01a", 37, 100)

    def test_ambiguous_latest_rows_are_rejected(self):
        self.write([self.row, {**self.row, "/BIC/YBWRVA": "different"}])
        with self.assertRaisesRegex(ValueError, "Quellschlüssel"):
            load_anomaly_raw_records("2025-07-14", 2025, "01a", 1, 100)

    def test_changed_load_date_is_rejected_in_raw_details(self):
        older = {
            **self.row, "/BIC/YBWRIPL": "20250714",
            "/BIC/YBWRELAD": "2025-07-01", "/BIC/YBWRFTAGE": "10",
        }
        self.write([older, self.row])
        with self.assertRaisesRegex(ValueError, "Erstes Ladedatum ist je Fallschlüssel nicht eindeutig"):
            load_anomaly_raw_records("2025-07-14", 2025, "01a", 1, 100)

    def test_click_opens_table_and_preserves_highlight(self):
        self.write([self.row])
        app = Dash(__name__, suppress_callback_exceptions=True)
        register_callbacks(app)
        entry = next(entry for entry in app.callback_map.values()
                     if entry["callback"].__wrapped__.__name__ == "highlight_severity_week")
        callback = entry["callback"].__wrapped__
        result = callback(
            {"points": [{"curveNumber": 1, "x": "2025-07-14"}]},
            [{"week_id": "2025-07-14", "week": "14.07.2025", "cases": 1, "days": 100}],
            {"year": 2025, "sachverhalt": "01a"},
        )
        self.assertTrue(result[2])
        self.assertEqual(result[3], [self.row])
        self.assertIn("2025-07-14", result[0]["styleConditions"][0]["condition"])
