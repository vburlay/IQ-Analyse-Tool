from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from pack.data_access import (
    load_anchor_cohort_case_data,
    load_anchor_cohort_weekly_data,
)


def prepare_anchor_forecast_plot_dataset(
    sachverhalt: str | None = "000",
    anchor_year: int | str | None = "all",
) -> pd.DataFrame:
    """Build a cumulative series for unique anchor-overlap cases by Ladedatum."""
    if not sachverhalt:
        return pd.DataFrame()

    df = load_anchor_cohort_weekly_data(str(sachverhalt), anchor_year)
    if df.empty:
        return df

    out = df.copy().sort_values("IPL_dt").reset_index(drop=True)
    out["TAGEN"] = pd.to_numeric(out["TAGEN"], errors="coerce").fillna(0).clip(lower=0)
    out["FAELLE"] = pd.to_numeric(out["FAELLE"], errors="coerce").fillna(0).clip(lower=0)
    out["Kumulierte_Lueckentage"] = out["TAGEN"].cumsum()
    out["Kumulierte_Faelle"] = out["FAELLE"].cumsum()
    return out


def calculate_anchor_weekly_diagnostics(
    sachverhalt: str | None = "000",
    anchor_year: int | str | None = "all",
) -> dict:
    """Diagnose the weekly relationship without modifying business values."""
    data = prepare_anchor_forecast_plot_dataset(sachverhalt, anchor_year)
    if data.empty:
        return {
            "weekly": pd.DataFrame(),
            "weekly_statistics": {},
            "individual_statistics": {},
        }

    weekly = data[
        ["IPL_dt", "FAELLE", "TAGEN", "Kumulierte_Faelle", "Kumulierte_Lueckentage"]
    ].copy()
    weekly = weekly.rename(
        columns={
            "IPL_dt": "erstes_ladedatum",
            "FAELLE": "neue_faelle",
            "TAGEN": "neue_lueckentage",
            "Kumulierte_Faelle": "kumulierte_faelle",
            "Kumulierte_Lueckentage": "kumulierte_lueckentage",
        }
    )
    weekly["durchschnittliche_lueckentage_je_neuem_fall"] = (
        weekly["neue_lueckentage"] / weekly["neue_faelle"].replace(0, np.nan)
    )
    weekly["kumulierte_lueckentage_je_fall"] = (
        weekly["kumulierte_lueckentage"] / weekly["kumulierte_faelle"].replace(0, np.nan)
    )

    average = weekly["durchschnittliche_lueckentage_je_neuem_fall"].dropna()
    cumulative_ratio = weekly["kumulierte_lueckentage_je_fall"].dropna()
    weekly_statistics = {
        "minimum_des_wochenmittels": float(average.min()),
        "median_des_wochenmittels": float(average.median()),
        "mittelwert_der_woechentlichen_lueckentage_je_fall": float(average.mean()),
        "maximum_des_wochenmittels": float(average.max()),
        "standardabweichung_des_wochenmittels": float(average.std()),
        "weekly_correlation": float(
            weekly["neue_faelle"].corr(weekly["neue_lueckentage"])
        ),
        "cumulative_ratio_minimum": float(cumulative_ratio.min()),
        "cumulative_ratio_maximum": float(cumulative_ratio.max()),
        "cumulative_ratio_standard_deviation": float(cumulative_ratio.std()),
    }

    cases = load_anchor_cohort_case_data(sachverhalt, anchor_year)
    individual = cases["lueckentage"].dropna()
    individual_statistics = {
        "anzahl": int(individual.count()),
        "mittelwert": float(individual.mean()),
        "median": float(individual.median()),
        "minimum": int(individual.min()),
        "maximum": int(individual.max()),
        "standardabweichung": float(individual.std()),
    }
    return {
        "weekly": weekly,
        "weekly_statistics": weekly_statistics,
        "individual_statistics": individual_statistics,
    }


def calculate_stichtag_forecast(
    sachverhalt: Optional[str],
    stichtag: str | None,
    intervallniveau: float | int = 80,
    year: int | str | None = "all",
) -> dict:
    """Forecast cumulative cases and Lückentage for the anchor-year cohort."""
    empty = {
        "forecast": None,
        "lower": None,
        "upper": None,
        "calculation_date": None,
        "current_value": None,
        "future": pd.DataFrame(),
        "message": "Bitte vollständige Prognoseparameter eingeben.",
    }
    if not sachverhalt or not stichtag:
        return empty

    df = prepare_anchor_forecast_plot_dataset(sachverhalt, year)
    if df.empty:
        return {**empty, "message": "Für den ausgewählten Sachverhalt sind keine Daten verfügbar."}

    history_columns = ["IPL_dt", "TAGEN", "FAELLE"]
    history = df[history_columns].dropna().sort_values("IPL_dt").copy()
    target_date = pd.to_datetime(stichtag, errors="coerce")
    if pd.isna(target_date):
        return {**empty, "message": "Der Stichtag ist ungültig."}

    last_date = history["IPL_dt"].max()
    if target_date <= last_date:
        return {
            **empty,
            "message": f"Der Stichtag muss nach dem letzten Datenstand ({last_date:%d.%m.%Y}) liegen.",
        }

    level = float(intervallniveau)
    if level not in {80.0, 90.0, 95.0}:
        return {**empty, "message": "Das Intervallniveau muss 80 %, 90 % oder 95 % betragen."}

    increments = pd.to_numeric(history["TAGEN"], errors="coerce").fillna(0).clip(lower=0)
    cumulative = increments.cumsum()
    recent_increments = increments.tail(min(52, len(increments))).to_numpy(dtype=float)
    if recent_increments.size == 0:
        return {**empty, "message": "Es sind keine verwertbaren Wochenwerte verfügbar."}

    future_dates = list(pd.date_range(last_date + pd.Timedelta(days=7), target_date, freq="7D"))
    if not future_dates or future_dates[-1] != target_date:
        future_dates.append(target_date)

    fractions = np.ones(len(future_dates), dtype=float)
    previous_date = last_date if len(future_dates) == 1 else future_dates[-2]
    fractions[-1] = min(1.0, max(0.0, (target_date - previous_date).days / 7.0))

    simulation_count = 5000
    rng = np.random.default_rng(42)
    last_actual = float(cumulative.iloc[-1])

    # Illustrativer Sättigungsverlauf für den Kundenprototyp: Der zuletzt
    # beobachtete Wochenwert nimmt gedämpft ab, bleibt aber nichtnegativ.
    recent_window = recent_increments[-min(12, recent_increments.size):]
    initial_increment = float(np.mean(recent_window))
    horizon = np.arange(len(future_dates), dtype=float)
    damping = 0.94
    residual_level = 0.20
    expected_increments = initial_increment * (
        residual_level + (1.0 - residual_level) * np.power(damping, horizon)
    )
    expected_increments = np.maximum(expected_increments, 0.0) * fractions

    mean_recent = max(float(np.mean(recent_window)), 1.0)
    coefficient_of_variation = float(np.std(recent_window, ddof=0) / mean_recent)
    coefficient_of_variation = float(np.clip(coefficient_of_variation, 0.15, 0.75))
    sigma = np.sqrt(np.log1p(coefficient_of_variation**2))
    multipliers = rng.lognormal(
        mean=-(sigma**2) / 2.0,
        sigma=sigma,
        size=(simulation_count, len(future_dates)),
    )
    sampled = expected_increments * multipliers

    case_increments = pd.to_numeric(history["FAELLE"], errors="coerce").fillna(0).clip(lower=0)
    recent_cases = case_increments.tail(min(12, len(case_increments))).to_numpy(dtype=float)
    initial_cases = float(np.mean(recent_cases))
    expected_case_increments = initial_cases * (
        residual_level + (1.0 - residual_level) * np.power(damping, horizon)
    )
    expected_case_increments = np.maximum(expected_case_increments, 0.0) * fractions
    current_cases = float(case_increments.sum())
    case_forecast = current_cases + np.cumsum(expected_case_increments)
    mean_recent_cases = max(float(np.mean(recent_cases)), 1.0)
    case_cv = float(np.std(recent_cases, ddof=0) / mean_recent_cases)
    case_cv = float(np.clip(case_cv, 0.15, 0.75))
    case_sigma = np.sqrt(np.log1p(case_cv**2))
    case_multipliers = rng.lognormal(
        mean=-(case_sigma**2) / 2.0,
        sigma=case_sigma,
        size=(simulation_count, len(future_dates)),
    )
    simulated_case_paths = current_cases + np.cumsum(
        expected_case_increments * case_multipliers,
        axis=1,
    )
    case_alpha = (100.0 - level) / 2.0
    case_lower = np.percentile(simulated_case_paths, case_alpha, axis=0)
    case_upper = np.percentile(simulated_case_paths, 100.0 - case_alpha, axis=0)
    case_future = pd.concat(
        [
            pd.DataFrame(
                {
                    "date": [last_date],
                    "forecast": [current_cases],
                    "lower": [current_cases],
                    "upper": [current_cases],
                }
            ),
            pd.DataFrame(
                {
                    "date": future_dates,
                    "forecast": case_forecast,
                    "lower": case_lower,
                    "upper": case_upper,
                }
            ),
        ],
        ignore_index=True,
    )

    simulated_paths = last_actual + np.cumsum(sampled, axis=1)
    forecast = last_actual + np.cumsum(expected_increments)
    alpha = (100.0 - level) / 2.0
    lower = np.percentile(simulated_paths, alpha, axis=0)
    upper = np.percentile(simulated_paths, 100.0 - alpha, axis=0)
    future = pd.DataFrame(
        {"date": future_dates, "forecast": forecast, "lower": lower, "upper": upper}
    )
    future = pd.concat(
        [
            pd.DataFrame(
                {"date": [last_date], "forecast": [last_actual], "lower": [last_actual], "upper": [last_actual]}
            ),
            future,
        ],
        ignore_index=True,
    )

    point = float(forecast[-1])
    lower_point = float(lower[-1])
    upper_point = float(upper[-1])
    result = {
        "forecast": point,
        "lower": lower_point,
        "upper": upper_point,
        "calculation_date": last_date,
        "current_value": last_actual,
        "future": future,
        "intervallniveau": int(level),
        "message": (
            "Illustrative Prognose mit gedämpftem Trend auf den lokal gespeicherten Daten; "
            + f"berechnet mit {simulation_count} simulierten Wochenpfaden; "
            f"Prognoseintervall: {int(level)} %."
        ),
        "current_cases": current_cases,
        "forecast_cases": float(case_forecast[-1]),
        "case_future": case_future,
    }
    return result
