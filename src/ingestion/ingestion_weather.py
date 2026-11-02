"""UrbanPulse - ingestion live Open-Meteo -> data/bronze/weather/<YYYY-MM>/<tanggal>.jsonl

Jalankan manual : python src/ingestion/ingestion_weather.py
Dari Airflow    : run_weather_ingestion(scheduled_hour=...)
Exit code CLI   : 0 = selesai, 1 = gagal fatal (config/lock).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.common import (ConfigError, LockBusy, get_logger, http_get, load_locations,
                              run_source, setup_logging, source_lock)

METEO_URL = "https://api.open-meteo.com/v1/forecast"
METEO_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation",
              "wind_speed_10m", "cloud_cover", "weather_code"]
METEO_CURRENT = ",".join(METEO_VARS)


def fetch_weather(lat, lon, logger=None):
    params = {"latitude": lat, "longitude": lon, "current": METEO_CURRENT, "timezone": "Asia/Jakarta"}
    return http_get(METEO_URL, params, params, logger)


def validate_weather_current(payload):
    if not isinstance(payload, dict):
        return False, "Payload Open-Meteo bukan JSON object"
    cur = payload.get("current")
    if not isinstance(cur, dict) or not cur.get("time"):
        return False, "Missing current.time in Open-Meteo response"
    missing = [k for k in METEO_VARS if k not in cur]
    if missing:
        return False, f"Missing current fields: {missing}"
    return True, None


def validate_weather_hourly(payload):
    if not isinstance(payload, dict):
        return False, "Payload Open-Meteo bukan JSON object"
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list) or not hourly["time"]:
        return False, "Missing hourly.time in Open-Meteo response"
    n = len(hourly["time"])
    for k in METEO_VARS:
        if not isinstance(hourly.get(k), list) or len(hourly[k]) != n:
            return False, f"Missing or misaligned hourly.{k}"
    return True, None


def run_weather_ingestion(scheduled_hour=None):
    """Satu kali tarik weather. Raise ConfigError/LockBusy jika gagal fatal."""
    setup_logging()
    locs = load_locations()
    with source_lock("weather"):
        return run_source(tag="WEATHER", source="open-meteo", kind="weather",
                          fetch=fetch_weather, validator=validate_weather_current,
                          locs=locs, scheduled_hour=scheduled_hour)


def main():
    try:
        run_weather_ingestion()
    except (ConfigError, LockBusy) as e:
        get_logger("WEATHER").error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
