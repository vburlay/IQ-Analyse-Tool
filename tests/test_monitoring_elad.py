import unittest

import pandas as pd

from pack.services.monitoring_service import (
    get_monitoring_chart_data,
    get_monitoring_grid_data,
)


class MonitoringEladTests(unittest.TestCase):
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
