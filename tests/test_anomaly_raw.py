import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
from dash import Dash

from pack.config import CASE_KEY_COLUMNS, RAW_BESTAND_COLUMNS
from pack.data_access import (
    load_anchor_cohort_weekly_data, load_anchor_year_diagnostics, load_anomaly_raw_records,
)
from pack.services.quality_service import build_quality_report
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

    def test_each_additional_key_distinguishes_cases_in_the_same_ipl(self):
        rows = [
            self.row,
            {**self.row, "/BIC/YBWRIQBPS": "0000000001"},
            {**self.row, "/BIC/YBWRBPF": "0000000002"},
        ]
        self.write(rows)
        weekly = load_anchor_cohort_weekly_data("01a", 2025)
        self.assertEqual((weekly["FAELLE"].sum(), weekly["TAGEN"].sum()), (3, 300))
        self.assertEqual(len(load_anomaly_raw_records("2025-07-14", 2025, "01a", 3, 300)), 3)
        diagnostics = load_anchor_year_diagnostics(2025)
        self.assertEqual(len(diagnostics[list(CASE_KEY_COLUMNS)].drop_duplicates()), 3)
        report = build_quality_report(pd.DataFrame(rows))
        self.assertEqual(report.records("ipl_conflict")[1], 0)

    def test_missing_optional_keys_are_valid_and_blank_and_null_group_together(self):
        empty_key = {**self.row, **dict.fromkeys(CASE_KEY_COLUMNS[2:])}
        blank_key = {**empty_key, **dict.fromkeys(CASE_KEY_COLUMNS[2:], " ")}
        other = {**empty_key, "/BIC/YBWRBPF": "0000000001"}
        rows = [empty_key, blank_key, other]
        self.write(rows)
        weekly = load_anchor_cohort_weekly_data("01a", 2025)
        self.assertEqual((weekly["FAELLE"].sum(), weekly["TAGEN"].sum()), (2, 200))
        self.assertEqual(len(load_anomaly_raw_records("2025-07-14", 2025, "01a", 2, 200)), 2)
        report = build_quality_report(pd.DataFrame(rows))
        self.assertEqual(report.records("key")[1], 0)
        self.assertEqual(report.records("ipl_conflict")[1], 0)

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
        expected = {
            **self.row,
            "/BIC/YBWRIPL": "21.07.2025",
            "/BIC/YBWRAVON": "01.01.2025",
            "/BIC/YBWRABIS": "31.12.2025",
            "/BIC/YBWRELAD": "15.07.2025",
        }
        self.assertEqual(result[3], [expected])
        self.assertIn("2025-07-14", result[0]["styleConditions"][0]["condition"])
