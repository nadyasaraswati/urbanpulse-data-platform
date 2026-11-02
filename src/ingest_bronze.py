import os, json, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

import logging, time

load_dotenv()
KEY = os.getenv("TOMTOM_API_KEY")

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
BRONZE = DATA_DIR / "bronze"
LOCATIONS = BASE / "config" / "locations.csv"

LOG_DIR = DATA_DIR / "logs"
log = logging.getLogger("ingest")

WIB = timezone(timedelta(hours=7))
TOMTOM_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
METEO_URL = "https://api.open-meteo.com/v1/forecast"
METEO_CURRENT = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,cloud_cover,weather_code"

# ---------- Logging ----------
def setup_logging():
  LOG_DIR.mkdir(exist_ok=True)

  # Dapatkan tanggal hari ini (format: 2026-10-31)
  today_str = datetime.now().strftime("%Y-%m-%d")
  log_file = LOG_DIR / f"ingest_{today_str}.log"

  fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
  log.setLevel(logging.INFO)

  # Bersihkan handler lama jika ada (terutama berguna di Jupyter Notebook)
  if log.handlers:
    log.handlers.clear()

  # Buat handler untuk Console (StreamHandler) dan File (FileHandler dengan append otomatis)
  handlers = [logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")]

  for h in handlers:
    h.setFormatter(fmt)
    log.addHandler(h)

# ---------- Config ----------
def load_locations(path=LOCATIONS):
    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    df = df.rename(columns={"ID": "id", "Titik Jalan": "name", "Latitude": "lat",
                            "Longitude": "lon", "Area": "area"})
    for c in ["lat", "lon"]:
        df[c] = df[c].astype(str).str.replace(",", ".").astype(float)
    return df[["id", "name", "lat", "lon", "area"]]


# ---------- Fetch (mengembalikan status, payload mentah, params tanpa key) ----------
RETRY_STATUS = {429, 500, 502, 503, 504}


def _get(url, params, safe_params, retries=3, backoff=2):
    last = (None, {"_error": "no attempt"}, safe_params)
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=20)
            try:
                payload = r.json()
            except ValueError:
                payload = {"_non_json_body": r.text[:2000]}
            last = (r.status_code, payload, safe_params)
            if r.status_code not in RETRY_STATUS:
                return last
        except requests.RequestException as e:
            last = (None, {"_error": str(e)}, safe_params)
        if attempt < retries:
            log.warning(f"retry {attempt}/{retries - 1} | status={last[0]} | {safe_params}")
            time.sleep(backoff * attempt)
    return last


def fetch_weather(lat, lon):
    params = {"latitude": lat, "longitude": lon, "current": METEO_CURRENT, "timezone": "Asia/Jakarta"}
    return _get(METEO_URL, params, params)


def fetch_traffic(lat, lon):
    safe = {"point": f"{lat},{lon}"}
    return _get(TOMTOM_URL, {**safe, "key": KEY}, safe)

# ---------- Record ----------
def hour_bucket(fetched_at):
    """20.51 WIB -> '2026-10-31T20:00'"""
    return fetched_at.astimezone(WIB).strftime("%Y-%m-%dT%H:00")

def extract_observation_time(source, payload):
    # Open-Meteo punya waktu observasi di payload; TomTom flow tidak menyediakan
    if source == "open-meteo":
        return (payload.get("current") or {}).get("time")
    return None

def build_record(source, p, fetched_at, run_id, status, payload, params, mode="live"):
    return {
        "ingestion_id": str(uuid.uuid4()),
        "run_id": run_id,
        "source": source,
        "ingestion_mode": mode,
        "fetched_at": fetched_at.isoformat(),
        "scheduled_hour": hour_bucket(fetched_at),
        "observation_time": extract_observation_time(source, payload),
        "location_id": str(p["id"]),
        "location_name": p["name"],
        "area": p["area"],
        "latitude": p["lat"],
        "longitude": p["lon"],
        "http_status": status,
        "request_params": params,
        "raw_payload": payload,
    }

# ---------- Write (append-only JSONL, 1 file per hari WIB) ----------
def bronze_path(kind, fetched_at):
    day = fetched_at.astimezone(WIB).strftime("%Y-%m-%d")
    return BRONZE / kind / f"{day}.jsonl"


def append_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------- Orchestrasi satu kali tarik ----------
def run_once(locs):
    fetched_at = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    weather, traffic = [], []
    total = len(locs)
    t0 = time.time()
    log.info(f"START run {run_id[:8]} | {total} titik | jam {hour_bucket(fetched_at)}")

    for i, (_, p) in enumerate(locs.iterrows(), 1):
        t1 = time.time()
        s_w, pl, pr = fetch_weather(p["lat"], p["lon"])
        weather.append(build_record("open-meteo", p, fetched_at, run_id, s_w, pl, pr))
        s_t, pl, pr = fetch_traffic(p["lat"], p["lon"])
        traffic.append(build_record("tomtom", p, fetched_at, run_id, s_t, pl, pr))

        level = logging.INFO if (s_w == 200 and s_t == 200) else logging.WARNING
        log.log(level, f"[{i}/{total}] {p['name']} | weather={s_w} traffic={s_t} | {time.time()-t1:.1f}s")

    wp, tp = bronze_path("weather", fetched_at), bronze_path("traffic", fetched_at)
    append_jsonl(wp, weather)
    append_jsonl(tp, traffic)
    log.info(f"WRITE {wp.relative_to(BASE)} (+{len(weather)}), {tp.relative_to(BASE)} (+{len(traffic)})")

    ok_w = sum(r["http_status"] == 200 for r in weather)
    ok_t = sum(r["http_status"] == 200 for r in traffic)
    log.info(f"DONE weather {ok_w}/{total} ok | traffic {ok_t}/{total} ok | {time.time()-t0:.1f}s")
    return weather, traffic

if __name__ == "__main__":
    setup_logging()
    run_once(load_locations())