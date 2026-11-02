"""UrbanPulse - ingestion live TomTom Flow -> data/bronze/traffic/<YYYY-MM>/<tanggal>.jsonl

Jalankan manual : python src/ingestion/ingestion_traffic.py
Dari Airflow    : run_traffic_ingestion(scheduled_hour=...)
Exit code CLI   : 0 = selesai, 1 = gagal fatal (config/API key/lock).
"""
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.common import (ConfigError, LockBusy, get_logger, http_get, load_locations,
                              run_source, setup_logging, source_lock, WIB)

TOMTOM_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

def half_hour_slot():
    now = datetime.now(WIB)
    return now.replace(minute=0 if now.minute < 30 else 30).strftime("%Y-%m-%dT%H:%M")

def require_api_key():
    if not os.getenv("TOMTOM_API_KEY"):
        raise ConfigError("TOMTOM_API_KEY belum di-set (cek file .env)")


def fetch_traffic(lat, lon, logger=None):
    safe = {"point": f"{lat},{lon}"}
    return http_get(TOMTOM_URL, {**safe, "key": os.getenv("TOMTOM_API_KEY")}, safe, logger)


def validate_traffic(payload):
    if not isinstance(payload, dict):
        return False, "Payload TomTom bukan JSON object"
    seg = payload.get("flowSegmentData")
    if not isinstance(seg, dict):
        return False, "Missing flowSegmentData in TomTom response"
    missing = [k for k in ("currentSpeed", "freeFlowSpeed") if k not in seg]
    if missing:
        return False, f"Missing flowSegmentData fields: {missing}"
    return True, None


def run_traffic_ingestion(scheduled_hour=None):
    """Satu kali tarik traffic. Raise ConfigError/LockBusy jika gagal fatal."""
    setup_logging()
    require_api_key()
    locs = load_locations()
    with source_lock("traffic"):
        return run_source(tag="TRAFFIC", source="tomtom", kind="traffic",
                          fetch=fetch_traffic, validator=validate_traffic,
                          locs=locs, scheduled_hour=scheduled_hour)


def main():
    try:
        run_traffic_ingestion()
    except (ConfigError, LockBusy) as e:
        get_logger("TRAFFIC").error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
