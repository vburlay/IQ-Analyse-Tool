import os
from configparser import ConfigParser
from pathlib import Path

# ============================================================
# Paths
# ============================================================


BASE_DIR = Path(__file__).resolve().parent.parent


DB_PATH = str(BASE_DIR / "data" / "iq_analyse.db")


# ============================================================
# Database tables
# ============================================================

TABLE_RAW = "raw_bestand"

# Earliest reporting year; source history remains available for cohort calculations.
ANALYSIS_START_YEAR = 2024

# SAP HANA connection settings. Credentials are entered in the application
# and are never stored in this file.
hana_config = ConfigParser(interpolation=None)
hana_config.read(BASE_DIR / "hana.ini", encoding="utf-8")

HANA_HOST = os.getenv("HANA_HOST", hana_config.get("hana", "host", fallback=""))
HANA_PORT = int(os.getenv("HANA_PORT", hana_config.get("hana", "port", fallback="")) or 0)
HANA_SCHEMA = os.getenv("HANA_SCHEMA", hana_config.get("hana", "schema", fallback=""))
HANA_TABLE = os.getenv("HANA_TABLE", hana_config.get("hana", "table", fallback=""))

CASE_EXTRA_KEY_COLUMNS = ("/BIC/YBWRIQBPS", "/BIC/YBWRBPF")
CASE_REQUIRED_KEY_COLUMNS = ("/B99/S_BWPKKD", "/BIC/YBWRAVON")
CASE_KEY_COLUMNS = (*CASE_REQUIRED_KEY_COLUMNS, *CASE_EXTRA_KEY_COLUMNS)
CASE_EXTRA_KEY_LENGTH = 10
WVGRD_COLUMN = "/BIC/YBWRWVGRD"
WVGRD_LENGTH = 3
MSD_COLUMN = "/BIC/YBWRMSD"

RAW_BESTAND_COLUMNS = (
    "/BIC/YBWRIQIQ",
    "/BIC/YBWRSEL",
    "/BIC/YBWRSV",
    "/B99/S_BWPKKD",
    "/BIC/YBWRIPL",
    "/BIC/YBWRFTAGE",
    "/BIC/YBWRAVON",
    "/BIC/YBWRELAD",
    "/BIC/YBWRABIS",
    "/BIC/YBWRVA",
    "/BIC/YBWRERGBN",
    *CASE_EXTRA_KEY_COLUMNS,
    WVGRD_COLUMN,
    MSD_COLUMN,
)

# Keep the complete case key together in the raw-data grids.
RAW_BESTAND_DISPLAY_COLUMNS = (
    *RAW_BESTAND_COLUMNS[:3],
    *CASE_KEY_COLUMNS,
    *(column for column in RAW_BESTAND_COLUMNS[3:] if column not in CASE_KEY_COLUMNS),
)


# ============================================================
# General settings
# ============================================================

# retry настройки для DB
DB_RETRIES = 12
DB_RETRY_DELAY = 0.25


# ============================================================
# App settings
# ============================================================

APP_TITLE = "IQ-Analyse-Tool (Block 1)"

DEFAULT_REFRESH_INTERVAL_MS = 700

# Monetary value assigned to one closed Lückentag.
EURO_PER_LUECKENTAG = 11
