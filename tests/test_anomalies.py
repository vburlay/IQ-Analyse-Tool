import unittest

import pandas as pd

from pack.ui.anomalies import weekly_case_severity


class SeverityTests(unittest.TestCase):
    def test_display_range_excludes_old_values_from_reference(self):
        dates = pd.date_range("2024-12-02", periods=12, freq="W-MON")
        cases = pd.DataFrame({
            "erstes_ladedatum": dates.repeat(10),
            "lueckentage": [900] * 50 + [10] * 50 + [100] * 10 + [10] * 10,
        })
        result = weekly_case_severity(cases, start=dates[5], end=dates[-1])
        self.assertEqual(result.index.min(), dates[5])
        self.assertEqual(result.loc[dates[10], "upper"], 10)
        self.assertTrue(result.loc[dates[10], "flagged"])

    def test_jump_uses_whole_period_and_excludes_last_week(self):
        dates = pd.date_range("2025-01-06", periods=12, freq="W-MON")
        cases = pd.DataFrame({
            "erstes_ladedatum": dates.repeat(10),
            "lueckentage": [10] * 10 + [100] * 10 + [10] * 90 + [100] * 10,
        })
        result = weekly_case_severity(cases)
        self.assertEqual(result.loc[dates[1], "mean"], 100)
        self.assertEqual(result.loc[dates[1], "upper"], 10)
        self.assertTrue(result.loc[dates[1], "flagged"])
        self.assertFalse(result.loc[dates[11], "flagged"])
        self.assertEqual(int(result["flagged"].sum()), 1)

    def test_missing_week_and_empty_data(self):
        self.assertTrue(weekly_case_severity(pd.DataFrame()).empty)
        cases = pd.DataFrame({
            "erstes_ladedatum": pd.to_datetime(["2025-01-06", "2025-01-20"]),
            "lueckentage": [10, 20],
        })
        result = weekly_case_severity(cases)
        self.assertTrue(pd.isna(result.loc["2025-01-13", "mean"]))
