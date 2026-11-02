"""UrbanPulse - Bronze -> Silver (manual).

  python src/silver_build.py                              # weather + traffic, semua file
  python src/silver_build.py --source traffic --start 2026-10-31 --end 2026-11-01
  python src/silver_build.py --dry-run                    # parse + validasi saja, tanpa DB
  python src/silver_build.py --full                       # abaikan offset, baca ulang dari awal

Koneksi dari .env: POSTGRES_HOST (default localhost), POSTGRES_PORT (default 5432),
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD.
"""
import os
import re
import json
import math
import argparse
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parents[2]   # src/silver/silver_build.py -> root project
load_dotenv(BASE / ".env")

BRONZE = BASE / "data" / "bronze"
LOG_DIR = BASE / "data" / "logs"
WIB = timezone(timedelta(hours=7))
SOURCES = ("weather", "traffic")

log = logging.getLogger("silver")


# ---------- Logging ----------
def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda ts: datetime.fromtimestamp(ts, WIB).timetuple()
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"silver_{today}.log", encoding="utf-8")):
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
        options="-c timezone=Asia/Jakarta",
    )


# ---------- Discover & read ----------
def discover_bronze_files(source, start=None, end=None):
    """File Bronze <source>, difilter tanggal pada nama file (inklusif)."""
    out = []
    for p in sorted((BRONZE / source).glob("**/*.jsonl")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem):
            continue
        if (start and p.stem < start) or (end and p.stem > end):
            continue
        out.append(p)
    return out


def read_bronze_file(path, offset=0):
    """Baca baris lengkap sejak offset. Return (records, new_offset, bad_lines)."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    cut = data.rfind(b"\n") + 1  # baris terakhir yang belum selesai ditulis tidak ikut
    records, bad = [], 0
    for line in data[:cut].splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            bad += 1
    return records, offset + cut, bad


# ---------- Parse / normalize / validate ----------
def parse_ts(s):
    """'YYYY-MM-DDTHH:MM' (WIB) -> datetime aware WIB."""
    return datetime.strptime(s[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=WIB)


def normalize_timestamp(rec, source):
    """Weather: scheduled_hour, fallback observation_time. Traffic: scheduled_hour."""
    raw = rec.get("scheduled_hour")
    if not raw and source == "weather":
        raw = rec.get("observation_time")
    return parse_ts(raw) if raw else None


def _num(v, name):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} bukan angka: {v!r}")
    return float(v)


def _range(v, name, lo=None, hi=None):
    if v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi)):
        raise ValueError(f"{name} di luar rentang: {v}")
    return v


def parse_weather(payload):
    # live -> payload["current"]; backfill -> payload["hourly"] (sudah 1 jam)
    b = payload.get("current") or payload.get("hourly")
    if not isinstance(b, dict):
        raise ValueError("payload tanpa blok current/hourly")
    row = {
        "temperature": _range(_num(b.get("temperature_2m"), "temperature"), "temperature", -10, 60),
        "humidity": _range(_num(b.get("relative_humidity_2m"), "humidity"), "humidity", 0, 100),
        "precipitation": _range(_num(b.get("precipitation"), "precipitation"), "precipitation", 0),
        "wind_speed": _range(_num(b.get("wind_speed_10m"), "wind_speed"), "wind_speed", 0),
        "cloud_cover": _range(_num(b.get("cloud_cover"), "cloud_cover"), "cloud_cover", 0, 100),
    }
    code = _num(b.get("weather_code"), "weather_code")
    row["weather_code"] = None if code is None else int(_range(code, "weather_code", 0, 99))
    if all(v is None for v in row.values()):
        raise ValueError("semua variabel cuaca null")
    return row


def parse_traffic(payload):
    seg = payload.get("flowSegmentData")
    if not isinstance(seg, dict):
        raise ValueError("payload tanpa flowSegmentData")
    cs, fs = _num(seg.get("currentSpeed"), "current_speed"), _num(seg.get("freeFlowSpeed"), "free_flow_speed")
    if cs is None or fs is None:
        raise ValueError("currentSpeed/freeFlowSpeed wajib ada")
    closure = seg.get("roadClosure")
    if closure is not None and not isinstance(closure, bool):
        raise ValueError(f"roadClosure bukan boolean: {closure!r}")
    coords = (seg.get("coordinates") or {}).get("coordinate")
    if coords is not None and not isinstance(coords, list):
        raise ValueError("coordinates bukan list")
    return {
        "current_speed": _range(cs, "current_speed", 0),
        "free_flow_speed": _range(fs, "free_flow_speed", 0),  # 0 disimpan; congestion dihitung di Gold
        "current_travel_time": _range(_num(seg.get("currentTravelTime"), "current_travel_time"), "current_travel_time", 0),
        "free_flow_travel_time": _range(_num(seg.get("freeFlowTravelTime"), "free_flow_travel_time"), "free_flow_travel_time", 0),
        "confidence": _range(_num(seg.get("confidence"), "confidence"), "confidence", 0, 1),
        "road_closure": closure,
        "frc": seg.get("frc"),
        "segment_coordinates": coords,
    }


PARSERS = {"weather": parse_weather, "traffic": parse_traffic}


def process_record(rec, source):
    """Return (status, data): ('skip'|'reject', alasan) atau ('ok', baris Silver)."""
    if not rec.get("ingestion_mode"):
        return "skip", "record lama tanpa ingestion_mode"
    if rec.get("http_status") != 200:
        return "reject", f"http_status={rec.get('http_status')}"
    if rec.get("payload_valid") is False:
        return "reject", "payload_valid=false"
    if not rec.get("location_id"):
        return "reject", "location_id kosong"
    try:
        ts = normalize_timestamp(rec, source)
        if ts is None:
            return "reject", "timestamp kunci kosong"
        row = PARSERS[source](rec.get("raw_payload") or {})
    except (ValueError, TypeError) as e:
        return "reject", str(e)
    row.update({
        "location_id": str(rec["location_id"]),
        "ts": ts,
        "ingestion_mode": rec["ingestion_mode"],
        "ingestion_id": rec["ingestion_id"],
        "fetched_at": datetime.fromisoformat(rec["fetched_at"]),
    })
    return "ok", row


def deduplicate_records(rows):
    """Satu baris per (location_id, ts): fetched_at terbaru menang."""
    best = {}
    for r in rows:
        k = (r["location_id"], r["ts"])
        if k not in best or r["fetched_at"] > best[k]["fetched_at"]:
            best[k] = r
    return list(best.values())


# ---------- Upsert ----------
WEATHER_COLS = ["temperature", "humidity", "precipitation", "wind_speed", "cloud_cover", "weather_code"]
TRAFFIC_COLS = ["current_speed", "free_flow_speed", "current_travel_time", "free_flow_travel_time",
                "confidence", "road_closure", "frc", "segment_coordinates"]
TABLES = {
    "weather": ("silver.weather_hourly", "hour_ts", WEATHER_COLS),
    "traffic": ("silver.traffic_30min", "observation_ts", TRAFFIC_COLS),
}


def _upsert(cur, source, rows):
    """Return (inserted, updated). Update hanya jika fetched_at lebih baru."""
    from psycopg2.extras import execute_values, Json
    table, ts_col, cols = TABLES[source]
    all_cols = ["location_id", ts_col] + cols + ["ingestion_mode", "ingestion_id", "fetched_at"]
    values = []
    for r in rows:
        vals = [Json(r[c]) if c == "segment_coordinates" and r[c] is not None else r[c] for c in cols]
        values.append((r["location_id"], r["ts"], *vals, r["ingestion_mode"], r["ingestion_id"], r["fetched_at"]))
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_cols[2:])
    sql = (f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES %s "
           f"ON CONFLICT (location_id, {ts_col}) DO UPDATE SET {sets} "
           f"WHERE EXCLUDED.fetched_at > {table}.fetched_at "
           f"RETURNING (xmax = 0) AS inserted")
    res = execute_values(cur, sql, values, page_size=1000, fetch=True)
    ins = sum(1 for (i,) in res if i)
    return ins, len(res) - ins


def get_offset(cur, file_name, source):
    cur.execute("SELECT byte_offset FROM silver.processed_files WHERE file_name=%s AND source=%s",
                (file_name, source))
    row = cur.fetchone()
    return row[0] if row else 0


def mark_success(cur, file_name, source, offset, st):
    cur.execute("""
        INSERT INTO silver.processed_files
            (file_name, source, status, byte_offset, records_read, records_skipped,
             records_valid, records_rejected, records_upserted, processed_at, error_message)
        VALUES (%s, %s, 'success', %s, %s, %s, %s, %s, %s, now(), NULL)
        ON CONFLICT (file_name, source) DO UPDATE SET
            status='success', byte_offset=EXCLUDED.byte_offset, records_read=EXCLUDED.records_read,
            records_skipped=EXCLUDED.records_skipped, records_valid=EXCLUDED.records_valid,
            records_rejected=EXCLUDED.records_rejected, records_upserted=EXCLUDED.records_upserted,
            processed_at=now(), error_message=NULL""",
                (file_name, source, offset, st["read"], st["skipped"], st["valid"], st["rejected"], st["upserted"]))


def mark_failed(conn, file_name, source, error):
    """Dicatat di transaksi terpisah; byte_offset lama dibiarkan agar file bisa diulang."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO silver.processed_files (file_name, source, status, error_message)
            VALUES (%s, %s, 'failed', %s)
            ON CONFLICT (file_name, source) DO UPDATE SET
                status='failed', error_message=EXCLUDED.error_message, processed_at=now()""",
                    (file_name, source, error[:1000]))
    conn.commit()


# ---------- Orkestrasi ----------
def process_file(conn, path, source, full=False):
    """Satu transaksi per file Bronze. conn=None -> dry-run."""
    name = path.relative_to(BRONZE).as_posix()
    st = Counter()
    try:
        cur = conn.cursor() if conn else None
        offset = 0 if (full or conn is None) else get_offset(cur, name, source)
        if path.stat().st_size <= offset:
            log.info(f"{name} | tidak ada baris baru")
            return st
        records, new_offset, bad = read_bronze_file(path, offset)
        st["read"] = len(records)
        if bad:
            log.warning(f"{name} | {bad} baris bukan JSON valid dilewati")

        rows, reasons = [], Counter()
        for rec in records:
            status, data = process_record(rec, source)
            if status == "ok":
                rows.append(data)
            else:
                st["skipped" if status == "skip" else "rejected"] += 1
                reasons[(status, data)] += 1
        for (status, why), n in reasons.most_common():
            log.warning(f"{name} | {status} x{n}: {why}")

        st["valid"] = len(rows)
        rows = deduplicate_records(rows)
        st["duplicate"] = st["valid"] - len(rows)

        if conn is None:
            log.info(f"{name} | DRY-RUN | read={st['read']} valid={st['valid']} unik={len(rows)} "
                     f"rejected={st['rejected']} skipped={st['skipped']}")
            return st

        ins, upd = _upsert(cur, source, rows) if rows else (0, 0)
        st["inserted"], st["updated"] = ins, upd
        st["upserted"] = ins + upd
        mark_success(cur, name, source, new_offset, st)
        conn.commit()
        log.info(f"{name} | read={st['read']} valid={st['valid']} rejected={st['rejected']} "
                 f"skipped={st['skipped']} dup={st['duplicate']} | insert={ins} update={upd}")
    except Exception as e:
        log.error(f"{name} | GAGAL: {e}")
        st["failed"] += 1
        if conn:
            conn.rollback()
            mark_failed(conn, name, source, str(e))
    return st


def run(sources, start, end, full, dry_run):
    log.info(f"Silver build started | source={','.join(sources)} | {start or '-'} s/d {end or '-'} "
             f"| full={full} dry_run={dry_run}")
    conn = None if dry_run else get_db_connection()
    total = Counter()
    try:
        for source in sources:
            files = discover_bronze_files(source, start, end)
            log.info(f"[{source}] file Bronze ditemukan: {len(files)}")
            for p in files:
                total.update(process_file(conn, p, source, full))
    finally:
        if conn:
            conn.close()
    log.info("SUMMARY " + " | ".join(f"{k}={total[k]}" for k in
             ("read", "valid", "rejected", "skipped", "duplicate", "inserted", "updated", "failed")))
    log.info("Silver build completed")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("all",) + SOURCES, default="all")
    ap.add_argument("--start", help="tanggal awal file, YYYY-MM-DD")
    ap.add_argument("--end", help="tanggal akhir file, YYYY-MM-DD")
    ap.add_argument("--full", action="store_true", help="abaikan offset, baca ulang dari awal file")
    ap.add_argument("--dry-run", action="store_true", help="parse + validasi saja, tanpa DB")
    a = ap.parse_args()
    setup_logging()
    run(SOURCES if a.source == "all" else (a.source,), a.start, a.end, a.full, a.dry_run)


if __name__ == "__main__":
    main()