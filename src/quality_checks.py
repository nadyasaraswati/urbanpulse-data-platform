"""UrbanPulse - data quality checks (Silver + Gold).

  python src/quality/quality_checks.py

Setiap pemeriksaan = query yang HARUS mengembalikan 0 baris. Baris yang muncul = ada masalah.
Exit code 1 jika ada pemeriksaan yang gagal (dipakai juga oleh DAG urbanpulse_transform).
Koneksi dari .env: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD.
"""
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parents[1]   # src/quality/quality_checks.py -> root project
load_dotenv(BASE / ".env")

LOG_DIR = BASE / "data" / "logs"
WIB = timezone(timedelta(hours=7))
TZ = "Asia/Jakarta"
FRESH_HOURS = 2          # data terbaru tidak boleh lebih tua dari ini
MAX_ROWS_SHOWN = 20

log = logging.getLogger("quality")


# ---------- Logging & DB ----------
def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda ts: datetime.fromtimestamp(ts, WIB).timetuple()
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"quality_{today}.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


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


# ---------- Definisi pemeriksaan ----------
KEY_TABLES = {  # tabel -> kolom kunci
    "silver.weather_hourly": "location_id, hour_ts",
    "silver.traffic_30min": "location_id, observation_ts",
    "gold.traffic_hourly": "location_id, hour_ts",
    "gold.weather_traffic_hourly": "location_id, hour_ts",
}


def _union(parts):
    return "\nUNION ALL\n".join(parts)


def build_checks():
    checks = []

    # 1. location_id NULL / kosong
    checks.append(("location_id NULL/kosong", _union([
        f"SELECT '{t}' AS tabel, count(*) AS n FROM {t} "
        f"WHERE location_id IS NULL OR btrim(location_id) = '' HAVING count(*) > 0"
        for t in KEY_TABLES])))

    # 2. duplikat key (satu pemeriksaan per tabel)
    for t, keys in KEY_TABLES.items():
        checks.append((f"duplikat key: {t}",
                       f"SELECT {keys}, count(*) AS n FROM {t} GROUP BY {keys} HAVING count(*) > 1"))

    # 3. kecepatan negatif
    checks.append(("kecepatan negatif", _union([
        "SELECT 'silver.traffic_30min' AS tabel, count(*) AS n FROM silver.traffic_30min "
        "WHERE current_speed < 0 OR free_flow_speed < 0 HAVING count(*) > 0",
        "SELECT 'gold.traffic_hourly', count(*) FROM gold.traffic_hourly "
        "WHERE avg_speed < 0 OR min_speed < 0 HAVING count(*) > 0",
        "SELECT 'gold.weather_traffic_hourly', count(*) FROM gold.weather_traffic_hourly "
        "WHERE avg_speed < 0 OR min_speed < 0 HAVING count(*) > 0",
    ])))

    # 4. location_id tanpa dimensi (FK logis ke gold.dim_location)
    checks.append(("location_id tanpa gold.dim_location", _union([
        f"SELECT '{t}' AS tabel, x.location_id, count(*) AS n FROM {t} x "
        f"LEFT JOIN gold.dim_location d ON d.location_id = x.location_id "
        f"WHERE d.location_id IS NULL GROUP BY x.location_id"
        for t in KEY_TABLES])))

    # 5. freshness: data terbaru tidak boleh lebih tua dari FRESH_HOURS
    checks.append((f"freshness > {FRESH_HOURS} jam", _union([
        f"SELECT '{t}' AS tabel, max({c}) AS terbaru FROM {t} "
        f"HAVING max({c}) IS NULL OR max({c}) < now() - interval '{FRESH_HOURS} hours'"
        for t, c in [("silver.traffic_30min", "observation_ts"),
                     ("silver.weather_hourly", "hour_ts"),
                     ("gold.weather_traffic_hourly", "hour_ts")]])))

    # 6. jumlah baris
    checks.append(("jumlah baris: gold.weather_traffic_hourly <> gold.traffic_hourly",
                   "SELECT a.n AS weather_traffic_hourly, b.n AS traffic_hourly "
                   "FROM (SELECT count(*) n FROM gold.weather_traffic_hourly) a, "
                   "(SELECT count(*) n FROM gold.traffic_hourly) b WHERE a.n <> b.n"))
    checks.append(("jumlah baris: gold.traffic_hourly <> jam unik silver.traffic_30min",
                   "SELECT g.n AS gold_traffic_hourly, s.n AS silver_jam_unik "
                   "FROM (SELECT count(*) n FROM gold.traffic_hourly) g, "
                   f"(SELECT count(*) n FROM (SELECT DISTINCT location_id, "
                   f"date_trunc('hour', observation_ts, '{TZ}') FROM silver.traffic_30min) u) s "
                   "WHERE g.n <> s.n"))
    return checks


# ---------- Eksekusi ----------
def run_checks(conn=None):
    """Return dict {nama_pemeriksaan: [baris bermasalah]}. Kosong = semua lolos."""
    own = conn is None
    conn = conn or get_db_connection()
    failures = {}
    try:
        with conn.cursor() as cur:
            for name, sql in build_checks():
                cur.execute(f"SELECT * FROM ({sql}) q LIMIT {MAX_ROWS_SHOWN}")
                rows = cur.fetchall()
                if rows:
                    failures[name] = rows
                    log.error(f"GAGAL | {name} | {len(rows)} baris bermasalah (maks {MAX_ROWS_SHOWN} ditampilkan)")
                    for r in rows[:5]:
                        log.error(f"        {r}")
                else:
                    log.info(f"OK    | {name}")
    finally:
        conn.rollback()
        if own:
            conn.close()
    log.info(f"Quality checks selesai | gagal={len(failures)}")
    return failures


def main():
    setup_logging()
    sys.exit(1 if run_checks() else 0)


if __name__ == "__main__":
    main()
