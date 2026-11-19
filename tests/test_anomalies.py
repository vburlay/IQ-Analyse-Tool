import unittest

import pandas as pd
from unittest.mock import patch

from pack.ui.anomalies import build_severity_section, weekly_case_severity


class SeverityTests(unittest.TestCase):
    def test_total_jump_with_unchanged_mean_and_no_future_axis(self):
        dates = pd.date_range("2025-01-06", periods=12, freq="W-MON")
        cases = pd.DataFrame({
            "erstes_ladedatum": [date for i, date in enumerate(dates)
                                for _ in range(100 if i == 6 else 10)],
        })
        cases["lueckentage"] = 10
        result = weekly_case_severity(cases)
        self.assertTrue(result.loc[dates[6], "days_flagged"])
        self.assertFalse(result.loc[dates[6], "mean_flagged"])
        with patch("pack.ui.anomalies.load_anchor_cohort_case_data", return_value=cases):
            section = build_severity_section("000", 2025, start="2025-01-01", end="2027-01-01")
        graph = next(child for child in section.children if hasattr(child, "figure"))
        self.assertEqual(pd.Timestamp(graph.figure.layout.xaxis.range[1]), dates[-1])

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
        self.assertFalse(result.loc[dates[11], "mean_flagged"])
        self.assertTrue(result.loc[dates[11], "case_flagged"])
        self.assertEqual(int(result["flagged"].sum()), 1)

    def test_large_individual_case_is_visible_when_weekly_mean_is_ordinary(self):
        dates = pd.date_range("2025-01-06", periods=12, freq="W-MON")
        cases = pd.DataFrame({
            "erstes_ladedatum": dates.repeat(100),
            "lueckentage": [10] * 1200,
        })
        cases.loc[600, "lueckentage"] = 1000
        result = weekly_case_severity(cases)
        self.assertTrue(result.loc[dates[6], "volume_flagged"])
        self.assertEqual(result.loc[dates[6], "large_cases"], 1)
        self.assertEqual(result.loc[dates[6], "largest"], 1000)

    def test_missing_week_and_empty_data(self):
        self.assertTrue(weekly_case_severity(pd.DataFrame()).empty)
        cases = pd.DataFrame({
            "erstes_ladedatum": pd.to_datetime(["2025-01-06", "2025-01-20"]),
            "lueckentage": [10, 20],
        })
        result = weekly_case_severity(cases)
        self.assertTrue(pd.isna(result.loc["2025-01-13", "mean"]))

    def test_large_case_does_not_mark_ordinary_weekly_total(self):
        dates = pd.date_range("2025-01-06", periods=12, freq="W-MON")
        rows = []
        for index, week in enumerate(dates):
            days = [100] + [0] * 9 if index == 6 else [10] * 10
            rows.extend({"erstes_ladedatum": week, "lueckentage": value} for value in days)
        result = weekly_case_severity(pd.DataFrame(rows))
        self.assertTrue(result.loc[dates[6], "case_flagged"])
        self.assertFalse(result["flagged"].any())
