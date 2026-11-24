from dataclasses import fields, replace
from datetime import date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from dash import Dash, no_update
import pandas as pd

from pack.config import RAW_BESTAND_COLUMNS
from pack.data_access import database_connection
from pack.forecast_runs import (
    ForecastRun,
    clear_forecast_runs,
    ensure_forecast_runs_table,
    load_forecast_runs,
    load_latest_forecast_run,
    save_forecast_run,
)
from pack.hana_import import HanaImportError, clear_local_database, ensure_local_database, refresh_from_hana
from pack.services.forecast_service import calculate_stichtag_forecast
from pack.services.quality_service import load_quality_report
from pack.ui.callbacks import register_callbacks
from pack.ui.forecast import CURRENT_RUN_MESSAGE, forecast_detail_grid_data, render_forecast_tab


class ForecastRunsTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        database = str(Path(directory) / "test.db")
        self.enterContext(patch("pack.data_access.DB_PATH", database))
        self.enterContext(patch("pack.hana_import.DB_PATH", database))
        self.enterContext(patch("pack.services.quality_service.DB_PATH", database))
        ensure_local_database()
        self.calculation = {
            "current_cases": 10.0,
            "forecast_cases": 14.6,
            "case_future": pd.DataFrame({"lower": [12.7], "upper": [16.1]}),
            "current_value": 100.0,
            "forecast": 132.8,
            "lower": 121.6,
            "upper": 142.1,
            "data_cutoff": date(2025, 10, 3),
            "intervallniveau": 90,
        }
        self.target = date(2025, 11, 1)

    def make_run(self):
        return ForecastRun.from_result(self.calculation, self.target, 2025, "000")

    def test_schema_and_empty_history(self):
        ensure_forecast_runs_table()
        with database_connection(read_only=True) as connection:
            columns = connection.execute("PRAGMA table_info('forecast_runs')").fetchall()
        self.assertEqual([column[1] for column in columns], [field.name for field in fields(ForecastRun)])
        types = {column[1]: column[2] for column in columns}
        self.assertEqual(types["revenue_forecast"], "DECIMAL(18,2)")
        self.assertEqual(types["euro_per_day"], "DECIMAL(10,2)")
        self.assertEqual(types["interval_level"], "DOUBLE")
        self.assertTrue(columns[0][5])
        self.assertEqual(load_forecast_runs(), [])
        self.assertIsNone(load_latest_forecast_run())
        rows, columns = forecast_detail_grid_data([])
        self.assertEqual(rows, [])
        self.assertEqual(columns[0]["headerName"], "Berechnet am")
        self.assertIsNotNone(render_forecast_tab())

    def test_rounding_and_monetary_invariants(self):
        run = self.make_run()
        save_forecast_run(run)
        run = load_latest_forecast_run()
        self.assertEqual(UUID(run.run_id).version, 4)
        self.assertEqual(run.data_cutoff, date(2025, 10, 3))
        self.assertEqual(run.interval_level, 0.90)
        self.assertEqual((run.cases_actual, run.cases_expected, run.cases_forecast), (10, 5, 15))
        self.assertEqual((run.days_actual, run.days_expected, run.days_forecast), (100, 33, 133))
        self.assertEqual((run.cases_lower, run.cases_upper), (12, 17))
        self.assertEqual((run.days_lower, run.days_upper), (121, 143))
        self.assertEqual(run.euro_per_day, Decimal("11.00"))
        for suffix in ("actual", "expected", "forecast", "lower", "upper"):
            self.assertEqual(getattr(run, f"revenue_{suffix}"), getattr(run, f"days_{suffix}") * 11)
        self.assertEqual(run.revenue_actual + run.revenue_expected, run.revenue_forecast)
        self.assertEqual((run.cases_model_name, run.days_model_name), ("illustrative", "illustrative"))
        self.assertTrue(run.is_experimental)

    def test_history_order_current_style_and_reload(self):
        older = replace(self.make_run(), created_at=datetime(2025, 10, 1))
        newer = replace(self.make_run(), created_at=datetime(2025, 10, 2))
        save_forecast_run(newer)
        save_forecast_run(older)
        runs = load_forecast_runs()
        self.assertEqual(runs, [newer, older])
        rows, columns = forecast_detail_grid_data(runs, newer.run_id)
        self.assertEqual([(r["run_id"], r["is_current"]) for r in rows],
                         [(newer.run_id, True), (older.run_id, False)])
        self.assertNotIn("run_id", [column.get("field") for column in columns])
        self.assertFalse(any(row["is_current"] for row in forecast_detail_grid_data(runs)[0]))

    def test_persistence_failure_rolls_back_insert(self):
        run = self.make_run()
        with patch("pack.forecast_runs._read_runs", side_effect=RuntimeError("read failed")):
            with self.assertRaisesRegex(RuntimeError, "read failed"):
                save_forecast_run(run)
        self.assertEqual(load_forecast_runs(), [])

    def test_refresh_preserves_history_and_clear_preserves_schema(self):
        run = self.make_run()
        save_forecast_run(run)
        frame = pd.DataFrame({column: ["x"] for column in RAW_BESTAND_COLUMNS})
        frame["/BIC/YBWRAVON"] = "20250101"
        frame["/BIC/YBWRABIS"] = "20251231"
        frame["/BIC/YBWRELAD"] = "20251003"
        frame["/BIC/YBWRFTAGE"] = "10"
        frame["/BIC/YBWRIPL"] = "20251006"
        frame["/BIC/YBWRSV"] = "000"
        frame["/BIC/YBWRMSD"] = "00000000"
        frame["/BIC/YBWRWVGRD"] = "001"
        frame["/BIC/YBWRIQBPS"] = ""
        frame["/BIC/YBWRBPF"] = None
        frame.attrs["hana_controls"] = (1, Decimal("10"))
        with patch("pack.hana_import._load_hana_frame", return_value=frame):
            self.assertEqual(refresh_from_hana("test", "test"), 1)
        mismatch = frame.copy()
        mismatch.attrs["hana_controls"] = (1, Decimal("11"))
        with patch("pack.hana_import._load_hana_frame", return_value=mismatch):
            with self.assertRaisesRegex(HanaImportError, "SAP-HANA-Abgleich fehlgeschlagen"):
                refresh_from_hana("test", "test")
        report = load_quality_report()
        hana_check = next(check for check in report.checks if check["id"] == "hana_reconciliation")
        self.assertEqual(hana_check["Status"], "Bestätigt")
        self.assertEqual(load_forecast_runs(), [run])
        forecast = calculate_stichtag_forecast("000", "2025-11-01", 90, 2025)
        self.assertEqual(forecast["data_cutoff"], date(2025, 10, 3))
        self.assertEqual(forecast["calculation_date"].date(), date(2025, 9, 29))
        clear_local_database()
        report = load_quality_report()
        hana_check = next(check for check in report.checks if check["id"] == "hana_reconciliation")
        self.assertEqual(hana_check["Status"], "Nicht geprüft")
        self.assertEqual(load_forecast_runs(), [])
        with database_connection(read_only=True) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM raw_bestand").fetchone()[0], 0)
        save_forecast_run(run)
        clear_forecast_runs()
        self.assertEqual(load_forecast_runs(), [])

    def test_click_callback_saves_once_per_click(self):
        app = Dash(__name__)
        register_callbacks(app)
        callback = next(entry for entry in app.callback_map.values()
                        if any(item["id"] == "forecast-calculate-btn" for item in entry["inputs"]))
        self.assertEqual(callback["inputs"], [{"id": "forecast-calculate-btn", "property": "n_clicks"}])
        calculate = callback["callback"].__wrapped__
        args = ("form", "tab-anchor-forecast", "000", 2025, self.target.isoformat(), 90)

        with (
            patch("pack.ui.callbacks.prepare_anchor_forecast_plot_dataset"),
            patch("pack.ui.callbacks.calculate_stichtag_forecast", return_value=self.calculation),
            patch("pack.ui.callbacks.anchor_graph_components", return_value=["two graphs"]),
        ):
            calculate(0, *args)
            self.assertEqual(load_forecast_runs(), [])
            first = calculate(1, *args)
            calculate(1, *args)
            self.assertEqual(len(load_forecast_runs()), 1)
            second = calculate(2, *args)
            self.assertEqual(len(load_forecast_runs()), 2)
            self.assertNotEqual(first[3], second[3])
            self.assertEqual(second[2][0]["run_id"], second[3])
            self.assertTrue(second[2][0]["is_current"])
            self.assertEqual(second[4], CURRENT_RUN_MESSAGE)
            with patch("pack.ui.callbacks.save_forecast_run", side_effect=RuntimeError("write failed")):
                with self.assertLogs(level="ERROR"):
                    failed = calculate(3, *args)
            self.assertEqual(failed[:5], (no_update,) * 5)
            self.assertIn("gespeichert", failed[5])
            self.assertEqual(len(load_forecast_runs()), 2)
