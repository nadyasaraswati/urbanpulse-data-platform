"""UrbanPulse - ekspor tabel Silver dan Gold dari PostgreSQL ke CSV.

  python src/export_csv.py                    # semua tabel -> exports/silver/*.csv, exports/gold/*.csv
  python src/export_csv.py --layer gold       # hanya Gold
  python src/export_csv.py --out D:\\data\\up  # folder tujuan lain

Setiap run menimpa file lama (snapshot kondisi tabel saat ini). Encoding UTF-8, pemisah koma.
Kolom waktu ditulis dengan offset WIB, contoh: 2026-11-01 00:30:00+07.
Koneksi dari .env: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD.
"""
import os
import sys
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parents[1]   # src/export_csv.py -> root project
load_dotenv(BASE / ".env")

OUT_DIR = BASE / "exports"
LOG_DIR = BASE / "data" / "logs"
WIB = timezone(timedelta(hours=7))
TZ = "Asia/Jakarta"

EXPORTS = {  # layer -> {tabel: urutan baris}
    "silver": {
        "weather_hourly": "location_id, hour_ts",
        "traffic_30min": "location_id, observation_ts",
    },
    "gold": {
        "dim_location": "location_id",
        "traffic_hourly": "location_id, hour_ts",
        "weather_traffic_hourly": "location_id, hour_ts",
    },
}

log = logging.getLogger("export")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda ts: datetime.fromtimestamp(ts, WIB).timetuple()
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"export_{today}.log", encoding="utf-8")):
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


def select_list(cur, layer, table):
    """Semua kolom sesuai urutan tabel; boolean ditulis true/false (bukan t/f)."""
    cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (layer, table))
    cols = cur.fetchall()
    if not cols:
        raise RuntimeError(f"Tabel {layer}.{table} tidak ditemukan")
    return ", ".join(f"{c}::text AS {c}" if t == "boolean" else c for c, t in cols)


def export_table(cur, layer, table, order_by, out_dir):
    """COPY ke file sementara, lalu ganti file lama (tidak meninggalkan CSV setengah jadi)."""
    path = Path(out_dir) / layer / f"{table}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    sql = (f"COPY (SELECT {select_list(cur, layer, table)} FROM {layer}.{table} ORDER BY {order_by}) "
           f"TO STDOUT WITH (FORMAT CSV, HEADER true)")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        cur.copy_expert(sql, f)
    rows = max(sum(1 for _ in open(tmp, encoding="utf-8")) - 1, 0)
    os.replace(tmp, path)
    return path, rows


def run(layers, out_dir=OUT_DIR):
    conn = get_db_connection()
    conn.set_session(readonly=True)
    results = {}
    try:
        with conn.cursor() as cur:
            for layer in layers:
                for table, order_by in EXPORTS[layer].items():
                    path, rows = export_table(cur, layer, table, order_by, out_dir)
                    results[f"{layer}.{table}"] = rows
                    log.info(f"{layer}.{table} -> {path} | {rows} baris")
    finally:
        conn.close()
    log.info("Ekspor selesai")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=("all", "silver", "gold"), default="all")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="folder tujuan (default: exports/)")
    a = ap.parse_args()
    setup_logging()
    run(tuple(EXPORTS) if a.layer == "all" else (a.layer,), a.out)


if __name__ == "__main__":
    main()
