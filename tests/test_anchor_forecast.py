import unittest
from datetime import date

from pack.config import ANALYSIS_START_YEAR
from pack.forecast_runs import ForecastRun
from pack.hana_import import ensure_local_database

import pandas as pd

from pack.data_access import (
    duck_query_df,
    load_anchor_cohort_weekly_data,
    load_anchor_year_diagnostics,
)
from pack.services.forecast_service import (
    calculate_anchor_weekly_diagnostics,
    calculate_stichtag_forecast,
    prepare_anchor_forecast_plot_dataset,
)
from pack.ui.forecast import (
    forecast_detail_grid_data,
    prepare_anchor_forecast_display_data,
)


class AnchorForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_local_database()

    @staticmethod
    def _parse_number(value: str) -> int:
        return int(value.replace(" €", "").replace(".", ""))

    def test_selected_year_uses_anchor_overlap_and_unique_cases(self):
        selected_year = 2025
        actual = load_anchor_cohort_weekly_data("all", selected_year)
        expected = duck_query_df(
            '''
            SELECT COUNT(*) AS cases
            FROM (
                SELECT DISTINCT
                    "/B99/S_BWPKKD",
                    "/BIC/YBWRIQBPS",
                    "/BIC/YBWRBPF",
                    CAST("/BIC/YBWRAVON" AS DATE) AS ankerbeginn
                FROM raw_bestand
                WHERE CAST("/BIC/YBWRAVON" AS DATE) <= DATE '2025-12-31'
                  AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '2025-01-01'
            )
            '''
        ).iloc[0]["cases"]
        self.assertEqual(int(actual["FAELLE"].sum()), int(expected))

    def test_load_date_is_not_before_anchor_start(self):
        diagnostics = load_anchor_year_diagnostics(2025)
        invalid = pd.to_datetime(diagnostics["erstes_ladedatum"]) < pd.to_datetime(
            diagnostics["ankerbeginn"]
        )
        self.assertEqual(int(invalid.sum()), 0)

    def test_anchor_lueckentage_equal_raw_business_metric_for_unique_cases(self):
        selected_year = 2025
        actual = load_anchor_cohort_weekly_data("all", selected_year)
        cases = duck_query_df(
            '''
            SELECT raw_lueckentage
            FROM (
                SELECT
                    TRY_CAST("/BIC/YBWRFTAGE" AS DOUBLE) AS raw_lueckentage,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            "/B99/S_BWPKKD",
                            "/BIC/YBWRIQBPS",
                            "/BIC/YBWRBPF",
                            CAST("/BIC/YBWRAVON" AS DATE)
                        ORDER BY STRPTIME(CAST("/BIC/YBWRIPL" AS VARCHAR), '%Y%m%d') DESC
                    ) AS version_rank
                FROM raw_bestand
                WHERE CAST("/BIC/YBWRAVON" AS DATE) <= DATE '2025-12-31'
                  AND CAST("/BIC/YBWRABIS" AS DATE) >= DATE '2025-01-01'
            )
            WHERE version_rank = 1
            '''
        )
        self.assertEqual(
            int(actual["TAGEN"].sum()),
            int(cases["raw_lueckentage"].sum()),
        )

    def test_ladedatum_axis_may_extend_beyond_anchor_year(self):
        data = prepare_anchor_forecast_plot_dataset("all", 2025)
        years = set(pd.to_datetime(data["IPL"]).dt.year)
        self.assertIn(2025, years)
        self.assertTrue(any(year < 2025 for year in years))
        self.assertTrue(any(year > 2025 for year in years))

    def test_display_starts_at_anchor_year_with_full_prior_baseline(self):
        data = prepare_anchor_forecast_plot_dataset("all", 2025)
        target = "2027-02-28"
        visible, start_date, target_date = prepare_anchor_forecast_display_data(
            data, 2025, target,
        )
        prior = data[data["IPL_dt"] < start_date]

        self.assertEqual(start_date, pd.Timestamp("2025-01-01"))
        self.assertEqual(target_date, pd.Timestamp(target))
        self.assertEqual(visible["IPL_dt"].min(), start_date)
        self.assertGreaterEqual(visible["IPL_dt"].min(), start_date)
        self.assertLessEqual(visible["IPL_dt"].max(), target_date)
        if not (data["IPL_dt"] == start_date).any():
            self.assertEqual(
                visible.iloc[0]["Kumulierte_Faelle"],
                prior.iloc[-1]["Kumulierte_Faelle"],
            )
            self.assertEqual(
                visible.iloc[0]["Kumulierte_Lueckentage"],
                prior.iloc[-1]["Kumulierte_Lueckentage"],
            )

    def test_all_counts_each_case_once(self):
        actual = load_anchor_cohort_weekly_data("all", "all")
        expected = duck_query_df(
            f'''
            SELECT COUNT(*) AS cases
            FROM (
                SELECT DISTINCT
                    "/B99/S_BWPKKD",
                    "/BIC/YBWRIQBPS",
                    "/BIC/YBWRBPF",
                    CAST("/BIC/YBWRAVON" AS DATE) AS ankerbeginn
                FROM raw_bestand
                WHERE CAST("/BIC/YBWRABIS" AS DATE) >= DATE '{ANALYSIS_START_YEAR}-01-01'
            )
            '''
        ).iloc[0]["cases"]
        self.assertEqual(int(actual["FAELLE"].sum()), int(expected))

    def test_weekly_diagnostics_preserve_business_values(self):
        data = prepare_anchor_forecast_plot_dataset("000", 2025)
        diagnostics = calculate_anchor_weekly_diagnostics("000", 2025)
        weekly = diagnostics["weekly"]

        self.assertEqual(int(weekly["neue_faelle"].sum()), int(data["FAELLE"].sum()))
        self.assertEqual(int(weekly["neue_lueckentage"].sum()), int(data["TAGEN"].sum()))
        self.assertIn("weekly_correlation", diagnostics["weekly_statistics"])
        self.assertEqual(
            diagnostics["individual_statistics"]["anzahl"],
            int(data["FAELLE"].sum()),
        )
        self.assertIsInstance(diagnostics["individual_statistics"]["minimum"], int)
        self.assertIsInstance(diagnostics["individual_statistics"]["maximum"], int)

    def test_anchor_forecast_starts_at_last_actual_value(self):
        data = prepare_anchor_forecast_plot_dataset("000", 2025)
        last_date = pd.to_datetime(data["IPL"]).max()
        stichtag = (last_date + pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        result = calculate_stichtag_forecast("000", stichtag, 80, 2025)
        self.assertAlmostEqual(
            result["future"].iloc[0]["forecast"],
            result["current_value"],
        )

    def test_anchor_forecast_uses_a_damped_non_decreasing_curve(self):
        data = prepare_anchor_forecast_plot_dataset("000", 2025)
        last_date = pd.to_datetime(data["IPL"]).max()
        stichtag = (last_date + pd.Timedelta(days=180)).strftime("%Y-%m-%d")
        result = calculate_stichtag_forecast("000", stichtag, 80, 2025)

        forecast = result["future"]["forecast"]
        increments = forecast.diff().dropna()
        self.assertTrue((increments >= 0).all())
        self.assertGreater(increments.iloc[0], increments.iloc[-1])
        self.assertGreater(increments.std(), 0)

    def test_case_forecast_starts_at_current_unique_case_count(self):
        data = prepare_anchor_forecast_plot_dataset("000", 2025)
        last_date = pd.to_datetime(data["IPL"]).max()
        stichtag = (last_date + pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        result = calculate_stichtag_forecast("000", stichtag, 80, 2025)

        case_forecast = result["case_future"]["forecast"]
        self.assertAlmostEqual(case_forecast.iloc[0], result["current_cases"])
        self.assertTrue((case_forecast.diff().dropna() >= 0).all())
        self.assertAlmostEqual(result["current_cases"], data["FAELLE"].sum())

    def test_result_table_expected_values_and_euro_consistency(self):
        result = {
            "data_cutoff": date(2026, 10, 29),
            "intervallniveau": 80,
            "case_future": pd.DataFrame({"lower": [1250.4], "upper": [1350.8]}),
            "current_cases": 1_234.4,
            "forecast_cases": 1_300.6,
            "current_value": 10_000.4,
            "forecast": 10_123.6,
            "lower": 9_900.4,
            "upper": 10_300.6,
        }
        run = ForecastRun.from_result(result, date(2027, 2, 28), 2026, "000")
        rows, columns = forecast_detail_grid_data([run])
        row = rows[0]
        number = self._parse_number

        def euro(value):
            return number(value.replace(",00", ""))

        current_cases = number(row["FaelleAktuell"])
        expected_cases = number(row["FaelleNochErwartet"])
        target_cases = number(row["FaellePrognose"])
        self.assertEqual(current_cases + expected_cases, target_cases)

        current_days = number(row["LueckentageAktuell"])
        expected_days = number(row["LueckentageNochErwartet"])
        target_days = number(row["LueckentagePrognose"])
        self.assertEqual(current_days + expected_days, target_days)

        current_euro = euro(row["ErtragAktuell"])
        expected_euro = euro(row["ErtragNochErwartet"])
        target_euro = euro(row["ErtragPrognose"])
        self.assertEqual(current_euro, current_days * 11)
        self.assertEqual(expected_euro, expected_days * 11)
        self.assertEqual(target_euro, target_days * 11)
        self.assertEqual(current_euro + expected_euro, target_euro)

        lower_days, upper_days = map(
            number, row["LueckentageIntervall"].split(" – ")
        )
        lower_euro, upper_euro = map(
            euro, row["Prognoseintervall"].split(" – ")
        )
        self.assertEqual(lower_euro, lower_days * 11)
        self.assertEqual(upper_euro, upper_days * 11)

        groups = {column["headerName"]: column for column in columns[1:]}
        self.assertEqual(
            [child["headerName"] for child in groups["Fälle"]["children"]],
            ["Aktuell", "Noch erwartet", "Zum Stichtag", "Prognoseintervall"],
        )
        self.assertEqual(
            [child["headerName"] for child in groups["Lückentage"]["children"]],
            ["Aktuell", "Noch erwartet", "Zum Stichtag", "Prognoseintervall"],
        )
        self.assertEqual(
            [child["headerName"] for child in groups["Ertragswert"]["children"]],
            [
                "Aktuell (€)",
                "Noch erwartet (€)",
                "Zum Stichtag (€)",
                "Prognoseintervall (€)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
