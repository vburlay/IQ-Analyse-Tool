# pack/ui/refresh.py

import threading
import time

from pack.data_access import get_latest_elad_value
from pack.hana_import import HanaImportError, ensure_local_database, refresh_from_hana


REFRESH_STATE = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "success": None,
    "last_stand": None,
    "message": None,
}

REFRESH_LOCK = threading.Lock()


def ensure_database_exists() -> bool:
    """Create an empty local DuckDB when no local cache exists."""
    return ensure_local_database()


def set_refresh_state(**kwargs):
    with REFRESH_LOCK:
        REFRESH_STATE.update(kwargs)


def get_refresh_state():
    with REFRESH_LOCK:
        return {
            "running": REFRESH_STATE["running"],
            "last_started": REFRESH_STATE["last_started"],
            "last_finished": REFRESH_STATE["last_finished"],
            "success": REFRESH_STATE["success"],
            "last_stand": REFRESH_STATE["last_stand"],
            "message": REFRESH_STATE["message"],
        }


def run_hana_refresh(username: str, password: str):
    set_refresh_state(
        running=True,
        last_started=time.strftime("%Y-%m-%d %H:%M:%S"),
        last_finished=None,
        success=None,
        message=None,
    )

    try:
        row_count = refresh_from_hana(username, password)
        latest_elad = get_latest_elad_value()
        set_refresh_state(
            running=False,
            last_finished=time.strftime("%Y-%m-%d %H:%M:%S"),
            success=True,
            last_stand=latest_elad,
            message=f"{row_count:,} Datensätze wurden aus SAP HANA übernommen.".replace(",", "."),
        )
    except HanaImportError as exc:
        set_refresh_state(
            running=False,
            last_finished=time.strftime("%Y-%m-%d %H:%M:%S"),
            success=False,
            message=str(exc),
        )
    except Exception:
        set_refresh_state(
            running=False,
            last_finished=time.strftime("%Y-%m-%d %H:%M:%S"),
            success=False,
            message="Aktualisierung fehlgeschlagen. Lokale Daten bleiben unverändert.",
        )
