"""Read-only data-quality checks with source-row evidence."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

from pack.config import ANALYSIS_START_YEAR, CASE_KEY_COLUMNS, DB_PATH, MSD_COLUMN, RAW_BESTAND_COLUMNS
from pack.data_access import database_connection

KEY = list(CASE_KEY_COLUMNS)
DATES = ["/BIC/YBWRAVON", "/BIC/YBWRABIS", "/BIC/YBWRELAD", "/BIC/YBWRIPL"]
DISPLAY_DATES = (*DATES, MSD_COLUMN)
HANA_CONTROL_TABLE = "hana_import_controls"


def parse_source_date(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    try:
        if len(text) == 8 and text.isdigit():
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        if len(text) >= 10 and text[4] == "-":
            return date.fromisoformat(text[:10])
        if len(text) == 10 and text[2] == ".":
            return date(int(text[6:]), int(text[3:5]), int(text[:2]))
    except ValueError:
        pass
    return None


def text_filter_mask(values, model):
    conditions = model.get("conditions")
    if conditions is None and "condition1" in model:
        conditions = [model["condition1"], model["condition2"]]
    if conditions:
        masks = [text_filter_mask(values, condition) for condition in conditions]
        result = masks[0]
        for mask in masks[1:]:
            result = result | mask if model.get("operator") == "OR" else result & mask
        return result
    text = values.astype("string").fillna("").str.casefold()
    query = str(model.get("filter", "")).casefold()
    operation = model.get("type", "contains")
    if operation == "blank":
        return text.eq("")
    if operation == "notBlank":
        return text.ne("")
    operations = {
        "contains": lambda: text.str.contains(query, regex=False),
        "notContains": lambda: ~text.str.contains(query, regex=False),
        "equals": lambda: text.eq(query),
        "notEqual": lambda: text.ne(query),
        "startsWith": lambda: text.str.startswith(query),
        "endsWith": lambda: text.str.endswith(query),
    }
    if operation not in operations:
        raise ValueError("Unbekannte Filterbedingung")
    return operations[operation]()


@dataclass
class QualityReport:
    source: pd.DataFrame
    dates: dict
    checks: list
    masks: dict
    affected: pd.Series
    coverage: list = field(default_factory=list)

    def forecast_readiness(self, backtest_results=None):
        """Score a screening checklist for a 13-week cumulative forecast."""
        common_checks = ["key", "reverse", "early_load", "early_ipl", "load_conflict",
                         "ipl_conflict", "sv_missing", *("date_" + column for column in DATES)]
        common = self.source.index.isin([])
        for check in common_checks:
            common = common | self.masks[check].loc[self.source.index].to_numpy()

        day_issues = common.copy()
        for check in ("days_invalid", "days_negative", "days_fractional"):
            day_issues |= self.masks[check].loc[self.source.index].to_numpy()
        day_values = pd.to_numeric(self.source["/BIC/YBWRFTAGE"], errors="coerce")
        day_weights = day_values.abs().replace([float("inf"), float("-inf")], 0).fillna(0)
        total_weight = day_weights.sum()
        day_share = day_weights.loc[day_issues].sum() / total_weight if total_weight else 0

        ipls = self.dates[DATES[3]].dropna()
        weeks = {value - timedelta(days=value.weekday()) for value in ipls
                 if value >= date(ANALYSIS_START_YEAR, 1, 1)}
        # One year for training and three non-overlapping 13-week test windows.
        history_weeks = 52 + 3 * 13
        hana_status = next((check["Status"] for check in self.checks
                            if check["id"] == "hana_reconciliation"), None)
        hana = {"Bestätigt": "passed", "Nicht bestanden": "failed"}.get(hana_status, "pending")
        results = []
        for metric, issue_rows, amount_share in (
            ("Fälle", common, 0), ("Lückentage", day_issues, day_share)
        ):
            row_share = issue_rows.mean() if len(issue_rows) else 1
            impact = max(row_share, amount_share)
            quality = "passed" if impact <= 0.05 else "failed" if impact >= 0.20 else "pending"
            backtest = {True: "passed", False: "failed"}.get((backtest_results or {}).get(metric), "pending")
            quality_detail = f"{row_share:.1%} betroffene Zeilen"
            if metric == "Lückentage":
                quality_detail += f", {amount_share:.1%} des absoluten Tagesvolumens dieser Rohdaten"
            criteria = [
                ("Datenqualität", quality, quality_detail.replace(".", ",") + " · bis 5 % erfüllt, ab 20 % kritisch"),
                ("IPL-Historie", "passed" if len(weeks) >= history_weeks else "failed",
                 f"{len(weeks)} von mindestens {history_weeks} beobachteten IPL-Wochen seit {ANALYSIS_START_YEAR}"),
                ("SAP-HANA-Abgleich", hana, "Kontrollanzahl und Kontrollsumme für denselben Datenstand"),
                ("Historische Prognoseprüfung", backtest, "Fehler des kumulierten Zuwachses am Stichtag"),
            ]
            passed = sum(status == "passed" for _, status, _ in criteria)
            status = "failed" if any(state == "failed" for _, state, _ in criteria) else (
                "passed" if passed == len(criteria) else "pending"
            )
            results.append((metric, 100 * passed // len(criteria), status, criteria))
        return results

    def records(self, check_id, page=0, size=100, sort_model=None, filter_model=None):
        mask = self.masks.get(check_id, self.affected)
        selected = self.source.loc[mask]
        for column, model in (filter_model or {}).items():
            if column not in RAW_BESTAND_COLUMNS:
                raise ValueError("Ungültige Filterspalte")
            values = selected[column]
            if column in DISPLAY_DATES:
                values = self.dates[column].loc[selected.index].map(
                    lambda value: f"{value.day:02d}.{value.month:02d}.{value.year:04d}"
                    if value is not None and pd.notna(value) else ""
                )
            selected = selected.loc[text_filter_mask(values, model)]
        if sort_model:
            ordering = pd.DataFrame(index=selected.index)
            ascending = []
            for item in sort_model:
                column = item["colId"]
                if column not in RAW_BESTAND_COLUMNS or item["sort"] not in ("asc", "desc"):
                    raise ValueError("Ungültige Sortierung")
                if column in DISPLAY_DATES:
                    ordering[column] = self.dates[column].loc[selected.index]
                elif column == "/BIC/YBWRFTAGE":
                    ordering[column] = pd.to_numeric(selected[column], errors="coerce")
                else:
                    ordering[column] = selected[column].astype("string")
                ascending.append(item["sort"] == "asc")
            selected = selected.loc[ordering.sort_values(
                list(ordering.columns), ascending=ascending, na_position="last", kind="stable"
            ).index]
        page = max(0, min(page, max(0, (len(selected) - 1) // size)))
        records = selected.iloc[page * size:(page + 1) * size].astype(object).where(
            selected.iloc[page * size:(page + 1) * size].notna(), None
        ).to_dict("records")
        for record in records:
            for column in DISPLAY_DATES:
                parsed = parse_source_date(record.get(column))
                if parsed is not None:
                    record[column] = f"{parsed.day:02d}.{parsed.month:02d}.{parsed.year:04d}"
        return records, len(selected), page


def case_key_frame(source, anchor_dates):
    frame = source[KEY].astype("string")
    frame[KEY[1]] = anchor_dates
    return frame


def complete_case_keys(frame):
    return frame[KEY[1]].notna() & frame[KEY[0]].fillna("").str.strip().ne("")


def build_quality_report(source, year="all", sachverhalt="all", hana_controls=None):
    source = source.copy().reset_index(drop=True)
    for column in KEY[2:]:
        values = source[column].astype("string")
        source[column] = values.mask(values.str.strip().eq("").fillna(False))
    dates = {}
    for column in DISPLAY_DATES:
        values = source[column].astype("string")
        lookup = {value: parse_source_date(value) for value in values.dropna().unique()}
        dates[column] = values.map(lookup).astype(object).where(values.notna(), None)
    selected = pd.Series(True, index=source.index)
    if sachverhalt != "all":
        selected &= source["/BIC/YBWRSV"].astype("string").eq(str(sachverhalt)).fillna(False)
    if year != "all":
        start, end = date(int(year), 1, 1), date(int(year), 12, 31)
        selected &= pd.Series([
            a is not None and b is not None and a <= end and b >= start
            for a, b in zip(dates[DATES[0]], dates[DATES[1]])
        ], index=source.index)
    # Evaluate key conflicts before filtering so other snapshots remain detectable.
    key_frame = case_key_frame(source, dates[DATES[0]])
    valid_key = complete_case_keys(key_frame)
    groups = [key_frame[column] for column in KEY]
    load_dates = dates[DATES[2]]
    selected &= load_dates.isna() | load_dates.between(
        date(ANALYSIS_START_YEAR, 1, 1), date(date.today().year, 12, 31)
    )
    checks, masks = [], {}
    affected = pd.Series(False, index=source.index)

    def add(identifier, label, severity, mask, impact, columns):
        nonlocal affected
        mask = mask.fillna(False) & selected
        masks[identifier] = mask
        count = int(mask.sum())
        case_count = len(key_frame.loc[mask & valid_key].drop_duplicates())
        def short_values(values):
            unique = sorted(set(str(value) for value in values if pd.notna(value)))
            return ", ".join(unique[:4]) + (f" (+{len(unique) - 4})" if len(unique) > 4 else "") or "—"

        checks.append({
            "Sachverhalte": short_values(source.loc[mask, "/BIC/YBWRSV"]),
            "Ankerbeginn-Jahre": short_values(
                value.year for value in dates[DATES[0]].loc[mask].dropna()
            ),
            "id": identifier, "Prüfung": label,
            "Status": severity if count else "OK", "Zeilen": count,
            "Fälle": case_count, "Auswirkung": impact,
            "columns": columns,
        })
        if severity != "Information":
            affected |= mask

    add("key", "Fehlender Fallschlüssel", "Fehler",
        ~valid_key,
        "BWPKKD und Ankerbeginn sind erforderlich. YBWRIQBPS und YBWRBPF dürfen leer sein.", KEY[:2])
    for column in DATES:
        add("date_" + column, "Nicht lesbares Datum: " + column, "Fehler",
            dates[column].isna(),
            "Datum kann keinem Zeitraum zugeordnet werden. Quellwert prüfen.", [column])
    add("open_end", "Offenes Ankerende (31.12.9999)", "Information",
        dates[DATES[1]].eq(date(9999, 12, 31)),
        "Gültiger offener Zeitraum; darf beim Datumsimport nicht als Fehler entfallen.", [DATES[1]])
    for identifier, label, left, right, impact in [
        ("reverse", "Ankerende vor Ankerbeginn", DATES[1], DATES[0], "Die Zuordnung zum Ankerjahr ist widersprüchlich."),
        ("early_load", "Erstes Ladedatum vor Ankerbeginn", DATES[2], DATES[0], "Zeitliche Zuordnung des Falls prüfen."),
        ("early_ipl", "IPL vor erstem Ladedatum", DATES[3], DATES[2], "Reihenfolge der Quelldaten ist widersprüchlich."),
    ]:
        mask = pd.Series([a is not None and b is not None and a < b
                          for a, b in zip(dates[left], dates[right])], index=source.index)
        add(identifier, label, "Fehler", mask, impact, [left, right])
    numeric = pd.to_numeric(source["/BIC/YBWRFTAGE"], errors="coerce")
    import numpy as np
    add("days_invalid", "Fehlende / nicht endliche Lückentage", "Fehler",
        numeric.isna() | ~np.isfinite(numeric), "Die Summe der Lückentage ist nicht verlässlich.", ["/BIC/YBWRFTAGE"])
    add("days_negative", "Negative Lückentage", "Prüfen", numeric.lt(0),
        "Korrektur oder fehlerhafter Wert? Fachliche Bedeutung prüfen.", ["/BIC/YBWRFTAGE"])
    add("days_fractional", "Nicht ganzzahlige Lückentage", "Prüfen", numeric.mod(1).ne(0) & numeric.notna(),
        "Tagesdefinition und Rundung prüfen.", ["/BIC/YBWRFTAGE"])
    for identifier, column, label, severity, impact in [
        ("load_conflict", DATES[2], "Mehrere erste Ladedaten je Fall", "Prüfen",
         "Die Zuordnung zu einer Woche ist mehrdeutig. Frühere Versionen werden zum Vergleich herangezogen."),
        ("end_change", DATES[1], "Geändertes Ankerende je Fall", "Information", "Kann die Kohortenzuordnung verändern; Änderung kann fachlich korrekt sein."),
        ("sv_change", "/BIC/YBWRSV", "Geänderter Sachverhalt je Fall", "Information", "Ein Fall ist in verschiedenen Datenständen unterschiedlichen Sachverhalten zugeordnet."),
        ("days_change", "/BIC/YBWRFTAGE", "Geänderte Lückentage je Fall", "Information", "Historische Rückprüfung benötigt den damals bekannten Datenstand."),
    ]:
        values = dates[column] if column in dates else source[column]
        counts = values.groupby(groups, dropna=False).transform("nunique")
        mask = counts.gt(1) & valid_key
        if identifier == "load_conflict":
            mask &= values.notna()
        add(identifier, label, severity, mask, impact, [*KEY, column, DATES[3]])
    add("identical", "Vollständig identische Datensätze", "Information",
        source.duplicated(keep=False), "Wiederholungen beeinflussen Summen aus Rohzeilen; Fallprognosen zählen eindeutige Schlüssel.", list(RAW_BESTAND_COLUMNS))
    distinct = source.drop_duplicates()
    snapshot_keys = key_frame.assign(ipl=dates[DATES[3]])
    distinct_keys = snapshot_keys.loc[distinct.index]
    conflict_keys = pd.MultiIndex.from_frame(distinct_keys.loc[distinct_keys.duplicated(keep=False)])
    mask = pd.Series(pd.MultiIndex.from_frame(snapshot_keys).isin(conflict_keys), index=source.index)
    add("ipl_conflict", "Unterschiedliche Datensätze je Fall und IPL", "Prüfen", mask & valid_key,
        "Mehrere Datensätze beschreiben denselben Fall im selben Datenstand unterschiedlich. Die gültige Version muss geklärt werden.", list(RAW_BESTAND_COLUMNS))
    add("result_unknown", "Unbekannter Ergebnisschlüssel", "Prüfen",
        ~source["/BIC/YBWRERGBN"].astype("string").isin([f"{n:03d}" for n in range(1, 9)]),
        "Diese Codes werden in der Kreuztabelle nicht berücksichtigt.", ["/BIC/YBWRERGBN"])
    add("sv_ui", "Sachverhalt außerhalb der Prognoseauswahl", "Information",
        ~source["/BIC/YBWRSV"].astype("string").isin(["000", "004", "005", "01a", "01b", "01c", "04a", "04b", "04c"]),
        "Über 'all' enthalten, aber im aktuellen Prognosefilter nicht einzeln auswählbar.", ["/BIC/YBWRSV"])
    add("sv_missing", "Fehlender Sachverhalt", "Fehler",
        source["/BIC/YBWRSV"].astype("string").fillna("").str.strip().eq(""),
        "Datensätze können keiner Sachverhaltsgruppe zugeordnet werden.", ["/BIC/YBWRSV"])

    gap_mask = pd.Series(False, index=source.index)
    stale_mask = pd.Series(False, index=source.index)
    coverage = []
    usable = source.loc[selected].copy()
    latest_ipl = max(dates[DATES[3]].loc[selected].dropna(), default=None)
    for sv, group in usable.groupby("/BIC/YBWRSV", dropna=False):
        load_dates = dates[DATES[2]].loc[group.index].dropna()
        ipls = dates[DATES[3]].loc[group.index].dropna()
        last_ipl = max(ipls, default=None)
        lag = (latest_ipl - last_ipl).days if latest_ipl and last_ipl else None
        # Coverage describes first-load observations; it does not certify extraction completeness.
        weeks = sorted({value - timedelta(days=value.weekday()) for value in load_dates})
        missing, max_gap, intervals = 0, 0, []
        boundaries = set()
        for before, after in zip(weeks, weeks[1:]):
            gap = (after - before).days // 7 - 1
            if gap > 0:
                missing += gap
                max_gap = max(max_gap, gap)
                boundaries.update((before, after))
                intervals.append(
                    f"{(before + timedelta(days=7)):%d.%m.%Y}–{(after - timedelta(days=1)):%d.%m.%Y}"
                )
        if boundaries:
            gap_mask.loc[group.index] = dates[DATES[2]].loc[group.index].map(
                lambda value: value is not None and pd.notna(value)
                and value - timedelta(days=value.weekday()) in boundaries
            )
        if lag is not None and lag >= 28:
            stale_mask.loc[group.index] = True
        coverage.append({
            "Sachverhalt": str(sv) if pd.notna(sv) else "(leer)",
            "Erste Ladung": min(load_dates).strftime("%d.%m.%Y") if len(load_dates) else "—",
            "Letzte erste Ladung": max(load_dates).strftime("%d.%m.%Y") if len(load_dates) else "—",
            "Letzter IPL": last_ipl.strftime("%d.%m.%Y") if last_ipl else "—",
            "IPL-Rückstand (Tage)": lag,
            "Wochen ohne Einträge": missing,
            "Längste Pause (Wochen)": max_gap,
            "Zeiträume ohne Einträge": "; ".join(intervals) or "—",
        })
    add("history_gaps", "Wochen ohne neue Fälle", "Information", gap_mask,
        "Wochen ohne neue Fälle sind normale Betriebspausen und kein Datenfehler. "
        "Rohdaten zeigen die Randwochen vor und nach den Pausen. "
        "Anzahl und Zeiträume stehen im Überblick; fehlende Einträge beweisen keine Datenlücke.",
        ["/BIC/YBWRSV", DATES[2]])
    add("stale_groups", "Sachverhalte mit älterem IPL-Datenstand", "Information", stale_mask,
        "Letzter IPL liegt mindestens 28 Tage hinter dem neuesten IPL der Auswahl. "
        "Ein älterer IPL allein ist kein Datenfehler; Sachverhalte können regulär beendet sein.",
        ["/BIC/YBWRSV", DATES[3]])
    add("hana_reconciliation", "Vollständigkeitsabgleich mit SAP HANA", "Information",
        pd.Series(False, index=source.index),
        "Nicht geprüft: Kontrollanzahl und Kontrollsumme aus SAP HANA für denselben "
        "Abfragezeitpunkt und dieselben Filter liegen nicht vor. "
        "Die Anzahl vorhandener Datensätze allein bestätigt keine vollständige Auslieferung.", [])
    checks[-1]["Status"] = "Nicht geprüft"
    if hana_controls is not None:
        row_count, day_sum = hana_controls
        checks[-1]["Status"] = "Bestätigt"
        checks[-1]["Auswirkung"] = (
            "Beim Import mit SAP HANA abgeglichen. "
            f"Gesamte Lieferung RSA / 4.15.1 mit {row_count} Zeilen und {day_sum:f} Lückentagen."
        )
    return QualityReport(source.loc[selected], {k: v.loc[selected] for k, v in dates.items()},
                         checks, {k: v.loc[selected] for k, v in masks.items()}, affected.loc[selected], coverage)


@lru_cache(maxsize=2)
def _source_snapshot(path, modified, size):
    columns = ", ".join(f'"{c}"' for c in RAW_BESTAND_COLUMNS)
    with database_connection(read_only=True, path=path) as connection:
        source = connection.execute(f"SELECT {columns} FROM raw_bestand").df()
        has_controls = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
            [HANA_CONTROL_TABLE],
        ).fetchone()[0]
        controls = connection.execute(
            f"SELECT row_count, day_sum FROM {HANA_CONTROL_TABLE}"
        ).fetchone() if has_controls else None
        return source, controls


@lru_cache(maxsize=4)
def _report_snapshot(path, modified, size, year, sachverhalt):
    source, controls = _source_snapshot(path, modified, size)
    return build_quality_report(source, year, sachverhalt, hana_controls=controls)


def load_quality_report(year="all", sachverhalt="all"):
    path = Path(DB_PATH)
    stat = path.stat()
    return _report_snapshot(str(path), stat.st_mtime_ns, stat.st_size, year, sachverhalt)
