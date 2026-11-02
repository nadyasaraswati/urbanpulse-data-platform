"""UrbanPulse - Silver -> Gold (manual).

  python src/gold/gold_build.py                 # hitung ulang SEMUA data
  python src/gold/gold_build.py --days 2        # hari ini + kemarin (WIB), seperti jadwal Airflow
  python src/gold/gold_build.py --days 2 --dry-run   # jalankan semua langkah lalu rollback

Idempotent: INSERT ... ON CONFLICT DO UPDATE, hanya mengubah baris yang nilainya berbeda.
Koneksi dari .env: POSTGRES_HOST (default localhost), POSTGRES_PORT (default 5432),
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD.
"""
import os
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parents[2]   # src/gold/gold_build.py -> root project
load_dotenv(BASE / ".env")

LOCATIONS = BASE / "config" / "locations.csv"
LOG_DIR = BASE / "data" / "logs"
WIB = timezone(timedelta(hours=7))
TZ = "Asia/Jakarta"

log = logging.getLogger("gold")


# ---------- Logging ----------
def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda ts: datetime.fromtimestamp(ts, WIB).timetuple()
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"gold_{today}.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


# ---------- DB ----------
def get_db_connection():
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        options=f"-c timezone={TZ}",
    )


# ---------- Window ----------
def window_start(days):
    """Awal jendela hitung ulang (00:00 WIB). days=2 -> kemarin 00:00. None -> semua data."""
    if not days:
        return None
    today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1)


# ---------- dim_location ----------
def load_locations(path=LOCATIONS):
    import pandas as pd
    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", dtype=str)
    df.columns = df.columns.str.strip()
    rename = {"ID": "location_id", "Titik Jalan": "location_name", "Latitude": "latitude",
              "Longitude": "longitude", "Area": "area"}
    missing = [c for c in rename if c not in df.columns]
    if missing:
        raise ValueError(f"Kolom wajib tidak ada di {Path(path).name}: {missing}")
    df = df[list(rename)].rename(columns=rename)
    for c in df.columns:
        df[c] = df[c].str.strip()
    df = df.dropna(subset=["location_id"]).reset_index(drop=True)
    for c in ("latitude", "longitude"):
        df[c] = df[c].str.replace(",", ".", regex=False).astype(float)
    if df["location_id"].duplicated().any():
        raise ValueError("location_id duplikat di locations.csv")
    return df


def upsert_dim_location(cur, df):
    from psycopg2.extras import execute_values
    values = [(r.location_id, r.location_name, r.area, r.latitude, r.longitude) for r in df.itertuples()]
    execute_values(cur, """
        INSERT INTO gold.dim_location (location_id, location_name, area, latitude, longitude)
        VALUES %s
        ON CONFLICT (location_id) DO UPDATE SET
            location_name = EXCLUDED.location_name, area = EXCLUDED.area,
            latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, updated_at = now()
        WHERE (gold.dim_location.location_name, gold.dim_location.area,
               gold.dim_location.latitude, gold.dim_location.longitude)
              IS DISTINCT FROM (EXCLUDED.location_name, EXCLUDED.area,
                                EXCLUDED.latitude, EXCLUDED.longitude)""", values)
    return len(values)


# ---------- gold.traffic_hourly ----------
SQL_TRAFFIC_HOURLY = f"""
INSERT INTO gold.traffic_hourly
    (location_id, hour_ts, avg_speed, min_speed, avg_congestion_ratio, max_congestion_ratio,
     observation_count, computed_at)
SELECT location_id,
       date_trunc('hour', observation_ts, '{TZ}') AS hour_ts,
       avg(current_speed), min(current_speed),
       avg(congestion_ratio), max(congestion_ratio),
       count(*), now()
FROM (
    SELECT location_id, observation_ts, current_speed,
           CASE WHEN free_flow_speed > 0
                THEN GREATEST(0, 1 - current_speed / free_flow_speed) END AS congestion_ratio
    FROM silver.traffic_30min
    WHERE %(start)s::timestamptz IS NULL OR observation_ts >= %(start)s::timestamptz
) s
GROUP BY 1, 2
ON CONFLICT (location_id, hour_ts) DO UPDATE SET
    avg_speed = EXCLUDED.avg_speed, min_speed = EXCLUDED.min_speed,
    avg_congestion_ratio = EXCLUDED.avg_congestion_ratio,
    max_congestion_ratio = EXCLUDED.max_congestion_ratio,
    observation_count = EXCLUDED.observation_count, computed_at = now()
WHERE (gold.traffic_hourly.avg_speed, gold.traffic_hourly.min_speed,
       gold.traffic_hourly.avg_congestion_ratio, gold.traffic_hourly.max_congestion_ratio,
       gold.traffic_hourly.observation_count)
      IS DISTINCT FROM
      (EXCLUDED.avg_speed, EXCLUDED.min_speed, EXCLUDED.avg_congestion_ratio,
       EXCLUDED.max_congestion_ratio, EXCLUDED.observation_count)
RETURNING (xmax = 0) AS inserted
"""


# ---------- gold.weather_traffic_hourly ----------
SQL_WEATHER_TRAFFIC = f"""
INSERT INTO gold.weather_traffic_hourly
    (location_id, hour_ts, obs_date, hour_of_day, day_of_week, is_weekend,
     avg_speed, min_speed, avg_congestion_ratio, max_congestion_ratio, observation_count,
     temperature, humidity, precipitation, wind_speed, cloud_cover, weather_code, computed_at)
SELECT t.location_id, t.hour_ts,
       l.local_ts::date,
       EXTRACT(HOUR FROM l.local_ts)::smallint,
       EXTRACT(ISODOW FROM l.local_ts)::smallint,
       EXTRACT(ISODOW FROM l.local_ts) IN (6, 7),
       t.avg_speed, t.min_speed, t.avg_congestion_ratio, t.max_congestion_ratio, t.observation_count,
       w.temperature, w.humidity, w.precipitation, w.wind_speed, w.cloud_cover, w.weather_code,
       now()
FROM gold.traffic_hourly t
CROSS JOIN LATERAL (SELECT t.hour_ts AT TIME ZONE '{TZ}' AS local_ts) l
LEFT JOIN silver.weather_hourly w
       ON w.location_id = t.location_id AND w.hour_ts = t.hour_ts
WHERE %(start)s::timestamptz IS NULL OR t.hour_ts >= %(start)s::timestamptz
ON CONFLICT (location_id, hour_ts) DO UPDATE SET
    obs_date = EXCLUDED.obs_date, hour_of_day = EXCLUDED.hour_of_day,
    day_of_week = EXCLUDED.day_of_week, is_weekend = EXCLUDED.is_weekend,
    avg_speed = EXCLUDED.avg_speed, min_speed = EXCLUDED.min_speed,
    avg_congestion_ratio = EXCLUDED.avg_congestion_ratio,
    max_congestion_ratio = EXCLUDED.max_congestion_ratio,
    observation_count = EXCLUDED.observation_count,
    temperature = EXCLUDED.temperature, humidity = EXCLUDED.humidity,
    precipitation = EXCLUDED.precipitation, wind_speed = EXCLUDED.wind_speed,
    cloud_cover = EXCLUDED.cloud_cover, weather_code = EXCLUDED.weather_code,
    computed_at = now()
WHERE (gold.weather_traffic_hourly.avg_speed, gold.weather_traffic_hourly.min_speed,
       gold.weather_traffic_hourly.avg_congestion_ratio, gold.weather_traffic_hourly.max_congestion_ratio,
       gold.weather_traffic_hourly.observation_count, gold.weather_traffic_hourly.temperature,
       gold.weather_traffic_hourly.humidity, gold.weather_traffic_hourly.precipitation,
       gold.weather_traffic_hourly.wind_speed, gold.weather_traffic_hourly.cloud_cover,
       gold.weather_traffic_hourly.weather_code)
      IS DISTINCT FROM
      (EXCLUDED.avg_speed, EXCLUDED.min_speed, EXCLUDED.avg_congestion_ratio,
       EXCLUDED.max_congestion_ratio, EXCLUDED.observation_count, EXCLUDED.temperature,
       EXCLUDED.humidity, EXCLUDED.precipitation, EXCLUDED.wind_speed, EXCLUDED.cloud_cover,
       EXCLUDED.weather_code)
RETURNING (xmax = 0) AS inserted
"""


def run_upsert(cur, sql, start):
    """Return (inserted, updated)."""
    cur.execute(sql, {"start": start})
    res = cur.fetchall()
    ins = sum(1 for (i,) in res if i)
    return ins, len(res) - ins


# ---------- Validasi ----------
def validate(cur, start):
    """Raise jika join menggandakan/menghilangkan baris traffic. Return statistik."""
    p = {"start": start}
    cur.execute("""SELECT count(*) FROM gold.traffic_hourly
                   WHERE %(start)s::timestamptz IS NULL OR hour_ts >= %(start)s::timestamptz""", p)
    n_traffic = cur.fetchone()[0]
    cur.execute("""SELECT count(*), count(*) FILTER (WHERE temperature IS NULL AND weather_code IS NULL),
                          count(*) FILTER (WHERE observation_count < 2)
                   FROM gold.weather_traffic_hourly
                   WHERE %(start)s::timestamptz IS NULL OR hour_ts >= %(start)s::timestamptz""", p)
    n_joined, n_no_weather, n_partial = cur.fetchone()
    if n_joined != n_traffic:
        raise RuntimeError(f"Validasi gagal: traffic_hourly={n_traffic} != weather_traffic_hourly={n_joined}")
    return {"traffic_hourly": n_traffic, "joined": n_joined,
            "tanpa_cuaca": n_no_weather, "jam_tidak_lengkap": n_partial}


# ---------- Orkestrasi ----------
def run(days=None, dry_run=False):
    """Satu transaksi untuk semua langkah. Exception diteruskan ke pemanggil."""
    start = window_start(days)
    log.info(f"Gold build started | jendela={'semua data' if start is None else start.isoformat()} "
             f"| dry_run={dry_run}")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            n = upsert_dim_location(cur, load_locations())
            log.info(f"dim_location: {n} lokasi diproses")

            ins, upd = run_upsert(cur, SQL_TRAFFIC_HOURLY, start)
            log.info(f"traffic_hourly: insert={ins} update={upd}")

            ins, upd = run_upsert(cur, SQL_WEATHER_TRAFFIC, start)
            log.info(f"weather_traffic_hourly: insert={ins} update={upd}")

            stats = validate(cur, start)
            log.info("validasi OK | " + " | ".join(f"{k}={v}" for k, v in stats.items()))
        if dry_run:
            conn.rollback()
            log.info("DRY-RUN: semua perubahan di-rollback")
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        log.exception("Gold build GAGAL, transaksi di-rollback")
        raise
    finally:
        conn.close()
    log.info("Gold build completed")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, help="hitung ulang N hari terakhir (WIB); kosong = semua data")
    ap.add_argument("--dry-run", action="store_true", help="jalankan semua langkah lalu rollback")
    a = ap.parse_args()
    setup_logging()
    run(a.days, a.dry_run)


if __name__ == "__main__":
    main()
