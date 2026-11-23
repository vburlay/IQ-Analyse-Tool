import unittest

import pandas as pd

from pack.config import RAW_BESTAND_COLUMNS
from pack.services.quality_service import build_quality_report, parse_source_date
from pack.ui.quality import overview_content


class QualityTests(unittest.TestCase):
    def test_history_gaps_and_group_freshness(self):
        rows = [
            self.row(**{"/BIC/YBWRELAD": "2025-01-06", "/BIC/YBWRIPL": "20250106"}),
            self.row(**{"/BIC/YBWRELAD": "2025-01-27", "/BIC/YBWRIPL": "20250127"}),
            self.row(**{"/BIC/YBWRSV": "004", "/BIC/YBWRIPL": "20250303"}),
        ]
        report = build_quality_report(pd.DataFrame(rows))
        group = next(item for item in report.coverage if item["Sachverhalt"] == "01a")
        self.assertEqual(group["Wochen ohne Einträge"], 2)
        self.assertEqual(group["Längste Pause (Wochen)"], 2)
        self.assertEqual(group["IPL-Rückstand (Tage)"], 35)
        self.assertIn("13.01.2025–26.01.2025", group["Zeiträume ohne Einträge"])
        self.assertEqual(report.records("history_gaps")[1], 2)
        self.assertEqual(report.records("stale_groups")[1], 2)

    def test_reporting_scope_keeps_required_history_and_malformed_dates(self):
        old = self.row(**{
            "/B99/S_BWPKKD": "old-only", "/BIC/YBWRAVON": "2020-01-01",
            "/BIC/YBWRABIS": "2021-12-31", "/BIC/YBWRELAD": "2020-06-01",
            "/BIC/YBWRIPL": "20210601",
        })
        history = {**old, "/B99/S_BWPKKD": "active"}
        active = {**history, "/BIC/YBWRABIS": "9999-12-31",
                  "/BIC/YBWRELAD": "2025-06-01", "/BIC/YBWRIPL": "20250602"}
        recent_ipl = {**old, "/B99/S_BWPKKD": "recent-ipl", "/BIC/YBWRIPL": "20240101"}
        recent_load = {**old, "/B99/S_BWPKKD": "recent-load", "/BIC/YBWRELAD": "2024-01-01"}
        malformed = {**old, "/B99/S_BWPKKD": "invalid", "/BIC/YBWRABIS": "wrong"}
        unreadable_load = {**malformed, "/BIC/YBWRELAD": "wrong"}
        report = build_quality_report(pd.DataFrame([
            old, history, active, recent_ipl, recent_load, malformed, unreadable_load,
        ]))
        self.assertEqual(report.source["/B99/S_BWPKKD"].tolist(), ["active", "recent-load", "invalid"])
        records, count, _ = report.records("load_conflict")
        self.assertEqual(count, 1)
        self.assertEqual(records[0]["/BIC/YBWRELAD"], "01.06.2025")
        self.assertEqual(report.records("date_/BIC/YBWRABIS")[1], 1)
        self.assertEqual(report.records("date_/BIC/YBWRELAD")[1], 1)

    def test_regular_pauses_and_old_ipl_are_information_only(self):
        rows = [
            self.row(**{"/B99/S_BWPKKD": "a", "/BIC/YBWRELAD": "2025-01-06", "/BIC/YBWRIPL": "20250106"}),
            self.row(**{"/B99/S_BWPKKD": "b", "/BIC/YBWRELAD": "2025-01-27", "/BIC/YBWRIPL": "20250127"}),
            self.row(**{"/B99/S_BWPKKD": "c", "/BIC/YBWRSV": "004", "/BIC/YBWRELAD": "2025-03-03", "/BIC/YBWRIPL": "20250303"}),
        ]
        report = build_quality_report(pd.DataFrame(rows))
        statuses = {c["id"]: c["Status"] for c in report.checks}
        self.assertEqual(statuses["history_gaps"], "Information")
        self.assertEqual(statuses["stale_groups"], "Information")
        self.assertFalse(report.affected.any())

    def test_hana_completeness_never_claims_success_without_controls(self):
        report = build_quality_report(pd.DataFrame([self.row()]))
        check = next(c for c in report.checks if c["id"] == "hana_reconciliation")
        self.assertEqual(check["Status"], "Nicht geprüft")
        self.assertEqual(report.records("hana_reconciliation")[1], 0)

    def test_evidence_filters_whole_selection_and_display_dates(self):
        rows = [self.row(**{"/B99/S_BWPKKD": f"case-{i}"}) for i in range(137)]
        report = build_quality_report(pd.DataFrame(rows))
        records, count, _ = report.records("open_end", filter_model={
            "/B99/S_BWPKKD": {"type": "equals", "filter": "case-136"},
            "/BIC/YBWRABIS": {"type": "contains", "filter": "31.12.9999"},
        })
        self.assertEqual(count, 1)
        self.assertEqual(records[0]["/B99/S_BWPKKD"], "case-136")

    def test_evidence_sorting_precedes_pagination(self):
        rows = [self.row(**{"/BIC/YBWRFTAGE": str(i)}) for i in range(137)]
        report = build_quality_report(pd.DataFrame(rows))
        first, count, _ = report.records("open_end", sort_model=[
            {"colId": "/BIC/YBWRFTAGE", "sort": "desc"},
        ])
        self.assertEqual(count, 137)
        self.assertEqual(first[0]["/BIC/YBWRFTAGE"], "136")
        self.assertEqual(first[-1]["/BIC/YBWRFTAGE"], "37")

    def row(self, **changes):
        row = {column: "x" for column in RAW_BESTAND_COLUMNS}
        row.update({
            "/B99/S_BWPKKD": "one", "/BIC/YBWRSV": "01a",
            "/BIC/YBWRERGBN": "002", "/BIC/YBWRAVON": "2025-01-01",
            "/BIC/YBWRABIS": "9999-12-31", "/BIC/YBWRELAD": "2025-07-15",
            "/BIC/YBWRIPL": "20250721", "/BIC/YBWRFTAGE": "10",
        })
        row.update(changes)
        return row

    def test_open_end_is_valid_and_dates_are_formatted(self):
        report = build_quality_report(pd.DataFrame([self.row()]))
        counts = {check["id"]: check["Zeilen"] for check in report.checks}
        self.assertEqual(counts["open_end"], 1)
        self.assertEqual(counts["date_/BIC/YBWRABIS"], 0)
        self.assertEqual(int(report.affected.sum()), 0)
        records, count, page = report.records("open_end")
        self.assertEqual(records[0]["/BIC/YBWRABIS"], "31.12.9999")
        self.assertEqual((count, page), (1, 0))
        self.assertIsNone(parse_source_date("nonsense"))

    def test_invalid_dates_reversed_periods_and_key_conflicts(self):
        rows = [
            self.row(),
            self.row(**{"/BIC/YBWRELAD": "2025-07-22", "/BIC/YBWRIPL": "20250728"}),
            self.row(**{"/B99/S_BWPKKD": "two", "/BIC/YBWRABIS": "wrong"}),
            self.row(**{"/B99/S_BWPKKD": "three", "/BIC/YBWRABIS": "2024-12-31"}),
        ]
        report = build_quality_report(pd.DataFrame(rows))
        counts = {check["id"]: check["Zeilen"] for check in report.checks}
        self.assertEqual(counts["load_conflict"], 2)
        self.assertEqual(counts["date_/BIC/YBWRABIS"], 1)
        self.assertEqual(counts["reverse"], 1)
        records, count, _ = report.records("load_conflict")
        self.assertEqual((len(records), count), (2, 2))
        self.assertEqual({r["/BIC/YBWRELAD"] for r in records}, {"15.07.2025", "22.07.2025"})

    def test_filters_keep_all_matching_evidence_and_duplicate_classification(self):
        rows = [
            self.row(), self.row(),
            self.row(**{"/BIC/YBWRVA": "other"}),
            self.row(**{"/B99/S_BWPKKD": "two", "/BIC/YBWRSV": "004"}),
        ]
        report = build_quality_report(pd.DataFrame(rows), 2026, "01a")
        counts = {check["id"]: check["Zeilen"] for check in report.checks}
        self.assertEqual(len(report.source), 3)
        self.assertEqual(counts["identical"], 2)
        self.assertEqual(counts["ipl_conflict"], 3)
        self.assertEqual(report.records("ipl_conflict")[1], 3)

    def test_empty_report_and_pagination(self):
        report = build_quality_report(pd.DataFrame(columns=RAW_BESTAND_COLUMNS))
        self.assertEqual(report.records("load_conflict"), ([], 0, 0))
        self.assertIsNotNone(overview_content(report))
        report = build_quality_report(pd.DataFrame([self.row()] * 137))
        self.assertEqual(len(report.records("identical")[0]), 100)
        self.assertEqual(len(report.records("identical", 1)[0]), 37)
