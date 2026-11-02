"""UrbanPulse - helper bersama untuk ingestion Bronze (weather + traffic).

Konvensi waktu:
  fetched_at       -> UTC (kapan request dilakukan)
  observation_time -> WIB (waktu kejadian yang diukur)
  scheduled_hour   -> WIB (jam target, mis. "2026-10-31T14:00")
  nama file/folder -> tanggal & bulan WIB

Layout Bronze (append-only JSONL):
  data/bronze/<weather|traffic>/<YYYY-MM>/<YYYY-MM-DD>.jsonl
"""
import os
import json
import uuid
import time
import random
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv
from filelock import FileLock, Timeout

BASE = Path(__file__).resolve().parents[2]
load_dotenv(BASE / ".env")

DATA_DIR = BASE / "data"
BRONZE = DATA_DIR / "bronze"
LOCATIONS = BASE / "config" / "locations.csv"
LOG_DIR = DATA_DIR / "logs"
LOCK_DIR = DATA_DIR / "locks"
RUNS_SUMMARY = LOG_DIR / "runs.jsonl"  # 1 baris ringkasan per run per sumber
LOCK_TIMEOUT = int(os.getenv("LOCK_TIMEOUT_SECONDS", "120"))

WIB = timezone(timedelta(hours=7))
REQUEST_TIMEOUT = 20
MAX_WAIT = 60  # batas atas waktu tunggu retry (detik)
RETRY_STATUS = {429, 500, 502, 503, 504}

log = logging.getLogger("ingest")


class ConfigError(RuntimeError):
    """Konfigurasi tidak valid (API key, locations.csv, argumen)."""


class LockBusy(RuntimeError):
    """Proses lain sedang menulis ke Bronze sumber yang sama."""


# ---------- Logging (satu file per tanggal WIB, penanda [SUMBER]) ----------
class TagAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['tag']}] {msg}", kwargs


def get_logger(tag):
    return TagAdapter(logging.getLogger("ingest"), {"tag": tag})


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fmt.converter = lambda ts: datetime.fromtimestamp(ts, WIB).timetuple()

    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"ingest_{today}.log", encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


# ---------- Config ----------
_RENAME = {"ID": "id", "Titik Jalan": "name", "Latitude": "lat",
           "Longitude": "lon", "Area": "area"}


def load_locations(path=LOCATIONS):
    """Baca dan validasi config/locations.csv. Raise ConfigError jika tidak valid."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"File lokasi tidak ditemukan: {path}")

    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", dtype=str)
    df.columns = df.columns.str.strip()

    missing = [c for c in _RENAME if c not in df.columns]
    if missing:
        raise ConfigError(f"Kolom wajib tidak ada di {path.name}: {missing}")

    for c in [c for c in df.columns if c not in _RENAME]:
        if df[c].notna().any():
            log.warning(f"Kolom tambahan '{c}' berisi data dan diabaikan")

    df = df[list(_RENAME)].rename(columns=_RENAME)
    for c in df.columns:
        df[c] = df[c].str.strip()
        df.loc[df[c] == "", c] = None
    df = df.dropna(how="all").reset_index(drop=True)

    errors = []
    if df.empty:
        errors.append("File lokasi kosong")

    for c in ["id", "name", "area"]:
        bad = df.index[df[c].isna()].tolist()
        if bad:
            errors.append(f"Kolom '{c}' kosong di baris data ke-{[i + 1 for i in bad]}")

    dup = df.loc[df["id"].notna() & df["id"].duplicated(keep=False), "id"].unique().tolist()
    if dup:
        errors.append(f"ID duplikat: {dup}")

    for c, lo, hi in [("lat", -90, 90), ("lon", -180, 180)]:
        num = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
        bad = df.index[num.isna()].tolist()
        if bad:
            errors.append(f"'{c}' kosong/bukan angka di baris data ke-{[i + 1 for i in bad]}")
        out = df.index[num.notna() & ((num < lo) | (num > hi))].tolist()
        if out:
            errors.append(f"'{c}' di luar rentang {lo}..{hi} di baris data ke-{[i + 1 for i in out]}")
        df[c] = num

    if errors:
        raise ConfigError("locations.csv tidak valid: " + "; ".join(errors))
    return df[["id", "name", "lat", "lon", "area"]]


# ---------- Lock per sumber (live + backfill untuk sumber yang sama) ----------
@contextmanager
def source_lock(kind, timeout=None):
    """Lock per folder Bronze. Weather dan traffic menulis file berbeda, jadi boleh paralel;
    live dan backfill weather memakai lock yang sama."""
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCK_DIR / f"bronze_{kind}.lock"
    lock = FileLock(str(path))
    try:
        lock.acquire(timeout=timeout)
    except Timeout:
        raise LockBusy(f"Proses ingestion {kind} lain masih berjalan "
                       f"(lock: {path.name}, menunggu {timeout}s)") from None
    try:
        yield
    finally:
        lock.release()


# ---------- HTTP (exponential backoff + jitter + Retry-After) ----------
def redact(text):
    key = os.getenv("TOMTOM_API_KEY")
    return text.replace(key, "***") if key else text


def _retry_after_seconds(resp):
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, min(float(value), MAX_WAIT))
    except ValueError:  # format HTTP-date tidak didukung -> pakai backoff biasa
        return None


def http_get(url, params, safe_params, logger=None, retries=3, backoff=2):
    """Mengembalikan (status, payload mentah, params tanpa key)."""
    logger = logger or log
    last = (None, {"_error": "no attempt"}, safe_params)
    for attempt in range(1, retries + 1):
        retry_after = None
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            try:
                payload = r.json()
            except ValueError:
                payload = {"_non_json_body": redact(r.text[:2000])}
            last = (r.status_code, payload, safe_params)
            if r.status_code not in RETRY_STATUS:
                return last
            retry_after = _retry_after_seconds(r)
        except requests.RequestException as e:
            last = (None, {"_error": redact(str(e))}, safe_params)

        if attempt < retries:
            base_wait = retry_after if retry_after is not None else min(backoff * 2 ** (attempt - 1), MAX_WAIT)
            wait = base_wait + random.uniform(0, 1)
            logger.warning(f"retry {attempt}/{retries - 1} | status={last[0]} | tunggu {wait:.1f}s | {safe_params}")
            time.sleep(wait)
    return last


def check_payload(status, payload, validator):
    """HTTP 200 belum tentu data valid. Mengembalikan (valid, error)."""
    if status is None:
        return False, "Request failed (no HTTP response)"
    if status != 200:
        return False, f"HTTP {status}"
    return validator(payload)


# ---------- Record & write ----------
def hour_bucket(ts):
    """Waktu apa pun -> jam WIB, mis. '2026-10-31T20:00'."""
    return ts.astimezone(WIB).strftime("%Y-%m-%dT%H:00")


def build_record(source, p, fetched_at, run_id, status, payload, params, *,
                 mode, scheduled_hour, payload_valid, validation_error,
                 observation_time=None):
    if observation_time is None and source == "open-meteo" and isinstance(payload, dict):
        cur = payload.get("current")
        if isinstance(cur, dict):
            observation_time = cur.get("time")
    return {
        "ingestion_id": str(uuid.uuid4()),
        "run_id": run_id,
        "source": source,
        "ingestion_mode": mode,                      # "live" | "backfill"
        "fetched_at": fetched_at.isoformat(),        # UTC
        "scheduled_hour": scheduled_hour,            # WIB
        "observation_time": observation_time,        # WIB
        "location_id": str(p["id"]),
        "location_name": p["name"],
        "area": p["area"],
        "latitude": p["lat"],
        "longitude": p["lon"],
        "http_status": status,
        "payload_valid": payload_valid,
        "validation_error": validation_error,
        "request_params": params,
        "raw_payload": payload,
    }


def bronze_path(kind, day):
    """day: 'YYYY-MM-DD' (tanggal WIB) -> data/bronze/<kind>/<YYYY-MM>/<day>.jsonl"""
    return BRONZE / kind / day[:7] / f"{day}.jsonl"


def append_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------- Satu kali tarik untuk satu sumber ----------
def run_source(*, tag, source, kind, fetch, validator, locs, scheduled_hour=None):
    """Fetch -> validasi -> tulis per lokasi (live). Mengembalikan ringkasan run.

    fetch(lat, lon, logger) -> (status, payload, params)
    scheduled_hour: jam target WIB 'YYYY-MM-DDTHH:00'. Default: jam WIB saat run dimulai.
    """
    lg = get_logger(tag)
    started = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    scheduled_hour = scheduled_hour or hour_bucket(started)
    path = bronze_path(kind, scheduled_hour[:10])

    total = len(locs)
    ok = unexpected = 0
    failed = []
    t0 = time.time()
    lg.info(f"START run {run_id[:8]} | {total} titik | jam {scheduled_hour}")

    for i, (_, p) in enumerate(locs.iterrows(), 1):
        t1 = time.time()
        try:
            fetched_at = datetime.now(timezone.utc)
            status, payload, params = fetch(p["lat"], p["lon"], lg)
            valid, err = check_payload(status, payload, validator)
            rec = build_record(source, p, fetched_at, run_id, status, payload, params,
                               mode="live", scheduled_hour=scheduled_hour,
                               payload_valid=valid, validation_error=err)
            append_jsonl(path, [rec])
        except Exception:
            unexpected += 1
            failed.append({"location": p["name"], "reason": "unexpected error"})
            lg.exception(f"[{i}/{total}] {p['name']} | error tak terduga")
            continue

        ok += valid
        if not valid:
            failed.append({"location": p["name"], "reason": err})
        detail = f"status={status}" + ("" if valid else f" ({err})")
        lg.log(logging.INFO if valid else logging.WARNING,
               f"[{i}/{total}] {p['name']} | {detail} | {time.time() - t1:.1f}s")

    lg.info(f"WRITE {path.relative_to(BASE)}")
    lg.info(f"DONE run {run_id[:8]} | {ok}/{total} valid | gagal {len(failed)} | {time.time() - t0:.1f}s")
    if failed:
        lg.warning(f"FAILED {len(failed)}/{total}:\n" + "\n".join(f"- {x['location']} ({x['reason']})" for x in failed))

    summary = {"run_id": run_id, "source": kind, "mode": "live", "scheduled_hour": scheduled_hour,
               "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
               "total": total, "ok": ok, "failed": failed, "unexpected_errors": unexpected}
    try:
        append_jsonl(RUNS_SUMMARY, [summary])
    except OSError:
        lg.exception("Gagal menulis ringkasan run")
    return summary
