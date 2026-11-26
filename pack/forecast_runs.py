from dataclasses import dataclass, asdict
from datetime import date, datetime
from decimal import Decimal
import json
from math import ceil, floor
from uuid import uuid4

from pack.config import EURO_PER_LUECKENTAG
from pack.data_access import database_connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_runs (
    run_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    data_cutoff DATE NOT NULL,
    stichtag DATE NOT NULL,
    ankerjahr INTEGER NOT NULL,
    sachverhalt VARCHAR NOT NULL,
    interval_level DOUBLE NOT NULL,
    cases_model_name VARCHAR NOT NULL,
    cases_actual BIGINT NOT NULL,
    cases_expected BIGINT NOT NULL,
    cases_forecast BIGINT NOT NULL,
    cases_lower BIGINT,
    cases_upper BIGINT,
    days_model_name VARCHAR NOT NULL,
    days_actual BIGINT NOT NULL,
    days_expected BIGINT NOT NULL,
    days_forecast BIGINT NOT NULL,
    days_lower BIGINT,
    days_upper BIGINT,
    euro_per_day DECIMAL(10,2) NOT NULL,
    revenue_actual DECIMAL(18,2) NOT NULL,
    revenue_expected DECIMAL(18,2) NOT NULL,
    revenue_forecast DECIMAL(18,2) NOT NULL,
    revenue_lower DECIMAL(18,2),
    revenue_upper DECIMAL(18,2),
    is_experimental BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_run_values (
    run_id VARCHAR PRIMARY KEY,
    values_snapshot JSON NOT NULL
);
"""

# The existing all-years option has no calendar year.
ALL_ANCHOR_YEARS = 0


@dataclass(frozen=True)
class ForecastRun:
    run_id: str
    created_at: datetime
    data_cutoff: date
    stichtag: date
    ankerjahr: int
    sachverhalt: str
    interval_level: float
    cases_model_name: str
    cases_actual: int
    cases_expected: int
    cases_forecast: int
    cases_lower: int | None
    cases_upper: int | None
    days_model_name: str
    days_actual: int
    days_expected: int
    days_forecast: int
    days_lower: int | None
    days_upper: int | None
    euro_per_day: Decimal
    revenue_actual: Decimal
    revenue_expected: Decimal
    revenue_forecast: Decimal
    revenue_lower: Decimal | None
    revenue_upper: Decimal | None
    is_experimental: bool

    @classmethod
    def from_result(cls, result, stichtag: date, year, sachverhalt: str):
        cases_actual = round(result["current_cases"])
        cases_forecast = round(result["forecast_cases"])
        case_interval = result["case_future"].iloc[-1]
        days_actual = round(result["current_value"])
        days_forecast = round(result["forecast"])
        days_expected = days_forecast - days_actual
        days_lower = floor(result["lower"])
        days_upper = ceil(result["upper"])
        rate = Decimal(str(EURO_PER_LUECKENTAG)).quantize(Decimal("0.01"))
        return cls(
            run_id=str(uuid4()),
            created_at=datetime.now(),
            data_cutoff=result["data_cutoff"],
            stichtag=stichtag,
            ankerjahr=ALL_ANCHOR_YEARS if year == "all" else int(year),
            sachverhalt=sachverhalt,
            interval_level=result["intervallniveau"] / 100,
            cases_model_name="illustrative",
            cases_actual=cases_actual,
            cases_expected=cases_forecast - cases_actual,
            cases_forecast=cases_forecast,
            cases_lower=floor(case_interval["lower"]),
            cases_upper=ceil(case_interval["upper"]),
            days_model_name="illustrative",
            days_actual=days_actual,
            days_expected=days_expected,
            days_forecast=days_forecast,
            days_lower=days_lower,
            days_upper=days_upper,
            euro_per_day=rate,
            revenue_actual=days_actual * rate,
            revenue_expected=days_expected * rate,
            revenue_forecast=days_forecast * rate,
            revenue_lower=days_lower * rate,
            revenue_upper=days_upper * rate,
            is_experimental=True,
        )

    def validate(self):
        if (
            self.cases_actual + self.cases_expected != self.cases_forecast
            or self.days_actual + self.days_expected != self.days_forecast
            or self.revenue_actual + self.revenue_expected != self.revenue_forecast
        ):
            raise ValueError("Inconsistent forecast totals")
        for field in ("actual", "expected", "forecast", "lower", "upper"):
            days = getattr(self, f"days_{field}")
            revenue = getattr(self, f"revenue_{field}")
            if revenue != (None if days is None else days * self.euro_per_day):
                raise ValueError("Inconsistent forecast revenue")


def ensure_forecast_runs_table():
    with database_connection() as connection:
        connection.execute(SCHEMA)


def save_forecast_run(result: ForecastRun, values_snapshot=None):
    result.validate()
    values = asdict(result)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with database_connection() as connection:
        connection.begin()
        try:
            connection.execute(
                f"INSERT INTO forecast_runs ({columns}) VALUES ({placeholders})",
                list(values.values()),
            )
            if values_snapshot is not None:
                connection.execute(
                    "INSERT INTO forecast_run_values (run_id, values_snapshot) VALUES (?, ?)",
                    [result.run_id, json.dumps(values_snapshot, allow_nan=False)],
                )
            history = _read_runs(connection)
            connection.commit()
            return history
        except Exception:
            connection.rollback()
            raise


def _read_runs(connection, limit=""):
    rows = connection.execute(
        "SELECT * FROM forecast_runs WHERE is_experimental "
        "ORDER BY created_at DESC, run_id DESC " + limit
    ).fetchall()
    return [ForecastRun(*row) for row in rows]


def load_forecast_runs():
    with database_connection(read_only=True) as connection:
        return _read_runs(connection)


def load_latest_forecast_run():
    with database_connection(read_only=True) as connection:
        return next(iter(_read_runs(connection, "LIMIT 1")), None)


def load_forecast_value_history():
    with database_connection(read_only=True) as connection:
        rows = connection.execute("""
            SELECT run_id, created_at, values_snapshot FROM forecast_runs
            JOIN forecast_run_values USING (run_id)
            WHERE is_experimental
            ORDER BY created_at DESC, run_id DESC
        """).fetchall()
    return [(run_id, created_at, json.loads(snapshot)) for run_id, created_at, snapshot in rows]


def load_forecast_values(run_id):
    with database_connection(read_only=True) as connection:
        snapshot = connection.execute(
            "SELECT values_snapshot FROM forecast_run_values WHERE run_id = ?", [run_id]
        ).fetchone()
    return json.loads(snapshot[0]) if snapshot else None


def clear_forecast_runs(connection=None):
    if connection is None:
        with database_connection() as connection:
            connection.begin()
            try:
                clear_forecast_runs(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return
    connection.execute(SCHEMA)
    connection.execute("DELETE FROM forecast_run_values")
    connection.execute("DELETE FROM forecast_runs")
