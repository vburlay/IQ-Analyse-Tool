import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from pack.services.monitoring_service import (
    get_monitoring_chart_data,
    get_monitoring_dso_page,
    get_monitoring_grid_data,
)
from pack.config import RAW_BESTAND_COLUMNS
from pack.data_access import database_connection, load_anchor_monitoring_cases
from pack.ui.monitoring import build_monitoring_main_fig


class MonitoringEladTests(unittest.TestCase):
    def test_anchor_chart_and_grid_use_same_latest_cases_across_calendar_years(self):
        case = {
            **dict.fromkeys(RAW_BESTAND_COLUMNS),
            "/B99/S_BWPKKD": "case", "/BIC/YBWRIQBPS": "01", "/BIC/YBWRBPF": None,
            "/BIC/YBWRAVON": "2024-12-01", "/BIC/YBWRABIS": "2025-04-30",
            "/BIC/YBWRELAD": "2024-12-16", "/BIC/YBWRIPL": "20250104",
            "/BIC/YBWRSV": "01a", "/BIC/YBWRERGBN": "001", "/BIC/YBWRFTAGE": "10",
        }
        data = pd.DataFrame([
            case,
            {**case, "/BIC/YBWRIPL": "20250111", "/BIC/YBWRERGBN": "002", "/BIC/YBWRFTAGE": "15"},
            {**case, "/BIC/YBWRIQBPS": "02", "/BIC/YBWRERGBN": "999", "/BIC/YBWRFTAGE": "5"},
            {**case, "/B99/S_BWPKKD": "outside", "/BIC/YBWRABIS": "2024-12-31"},
        ])
        with TemporaryDirectory() as directory, patch(
            "pack.data_access.DB_PATH", str(Path(directory) / "test.duckdb")
        ):
            with database_connection() as connection:
                connection.register("test_data", data)
                connection.execute("CREATE TABLE raw_bestand AS SELECT * FROM test_data")
            cases = load_anchor_monitoring_cases(2025)
            chart = get_monitoring_chart_data(2025, "anchor", anchor_cases=cases)
            rows, _ = get_monitoring_grid_data(2025, "anchor", anchor_cases=cases)
            graph = build_monitoring_main_fig(2025, "anchor", anchor_cases=cases)
            for mode, year, expected_days, expected_count in (
                ("anchor", 2025, 30, 3), ("elad", 2024, 40, 4), ("ipl", 2025, 40, 4),
            ):
                with self.subTest(mode=mode):
                    records, days, _, _, _, count = get_monitoring_dso_page(year, mode, "01a", 0)
                    self.assertEqual((days, count, len(records)), (expected_days, expected_count, expected_count))
                    self.assertIn("999", {row["/BIC/YBWRERGBN"] for row in records})
            for sv, field, expected in (
                ("01a", "Fami erfasst", 15),
                ("Gesamtergebnis", "offen", 0),
                ("01a", "Sonstige Ergebnisschlüssel", 5),
                ("01a", "Ertrag", 15),
                ("01a", "storno", 0),
            ):
                with self.subTest(sv=sv, field=field):
                    _, days, _, _, _, count = get_monitoring_dso_page(2025, "anchor", sv, 0, field=field)
                    self.assertEqual((days, count), (expected, int(expected != 0)))
            for mode, year in (("elad", 2024), ("ipl", 2025)):
                _, days, _, _, _, count = get_monitoring_dso_page(year, mode, "01a", 0, field="offen")
                self.assertEqual((days, count), (20, 2))

        self.assertEqual(len(cases), 2)
        self.assertEqual(chart["x"].tolist(), [pd.Timestamp("2024-12-16")])
        self.assertEqual(chart["TAGEN"].sum(), 20)
        self.assertEqual(rows[-1]["Potential"], 20)
        self.assertEqual(rows[-1]["Fami erfasst"], 15)
        self.assertEqual(graph.data[0].customdata[0][0], "16.12.2024")

    def test_chart_uses_only_selected_elad_year(self):
        for year in (2024, 2025, 2026):
            chart = get_monitoring_chart_data(year)
            self.assertFalse(chart.empty)
            self.assertEqual(set(pd.to_datetime(chart["x"]).dt.year), {year})

    def test_chart_and_grid_cover_same_elad_year_total(self):
        for year in (2024, 2025, 2026):
            chart = get_monitoring_chart_data(year)
            rows, _ = get_monitoring_grid_data(year)
            self.assertTrue(rows)
            self.assertAlmostEqual(float(chart["TAGEN"].sum()), rows[-1]["Potential"])

    def test_boundary_weeks_are_marked_as_incomplete(self):
        chart = get_monitoring_chart_data(2024)
        last_week = chart.iloc[-1]
        self.assertEqual(last_week["Kalendertage"], 2)
        self.assertFalse(bool(last_week["Vollstaendige_Woche"]))

    def test_ipl_chart_and_grid_use_the_same_year_total(self):
        for year in (2024, 2025, 2026):
            chart = get_monitoring_chart_data(year, "ipl")
            rows, _ = get_monitoring_grid_data(year, "ipl")
            self.assertTrue(rows)
            self.assertEqual(set(pd.to_datetime(chart["x"]).dt.year), {year})
            self.assertAlmostEqual(float(chart["TAGEN"].sum()), rows[-1]["Potential"])


if __name__ == "__main__":
    unittest.main()
