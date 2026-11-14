import os
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

# SAP HANA connection settings. Credentials are entered in the application
# and are never stored in this file.
HANA_HOST = os.getenv("HANA_HOST", "")
HANA_PORT = int(os.getenv("HANA_PORT", "0") or 0)
HANA_SCHEMA = os.getenv("HANA_SCHEMA", "")
HANA_TABLE = os.getenv("HANA_TABLE", "")

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
    "/B99/S_BWSTKZ",
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
