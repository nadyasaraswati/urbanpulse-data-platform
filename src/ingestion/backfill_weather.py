"""UrbanPulse - Backfill weather historis ke Bronze (ingestion_mode = "backfill").

Memakai lock weather yang sama dengan live ingestion (satu lock per tanggal yang di-backfill),
jadi live dan backfill tidak menulis ke JSONL yang sama secara bersamaan.

Contoh: python src/ingestion/backfill_weather.py --start "2026-10-30 00" --end "2026-10-31 20"
Exit code: 0 = selesai, 1 = gagal fatal (argumen/config/lock).
"""
import sys
import argparse
import uuid
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.common import (ConfigError, LockBusy, append_jsonl, bronze_path, build_record,
                              check_payload, get_logger, http_get, load_locations, log,
                              setup_logging, source_lock, WIB)
from ingestion.ingestion_weather import METEO_URL, METEO_VARS, validate_weather_hourly

METEO_HOURLY = ",".join(METEO_VARS)
MAX_PAST_DAYS = 92  # perkiraan batas data historis Open-Meteo forecast API (hanya peringatan)
lg = get_logger("WEATHER")


def fetch_weather_hourly(lat, lon, day, logger=None):
    params = {"latitude": lat, "longitude": lon, "hourly": METEO_HOURLY,
              "start_date": day, "end_date": day, "timezone": "Asia/Jakarta"}
    return http_get(METEO_URL, params, params, logger)


def slice_hour(payload, i):
    return {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "elevation": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
        "hourly_units": payload.get("hourly_units"),
        "hourly": {k: v[i] for k, v in payload["hourly"].items()},
    }


def build_hour_records(p, fetched_at, run_id, status, payload, params, hour_from, hour_to):
    """Return list of (tanggal_observasi, record). Request/payload gagal tetap dicatat."""
    ok, err = check_payload(status, payload, validate_weather_hourly)
    if not ok:
        rec = build_record("open-meteo", p, fetched_at, run_id, status, payload, params,
                           mode="backfill", scheduled_hour=None,
                           payload_valid=False, validation_error=err)
        return [(params["start_date"], rec)]

    out = []
    for i, t in enumerate(payload["hourly"]["time"]):  # t = "2026-10-31T05:00" (WIB)
        if not (hour_from <= int(t[11:13]) <= hour_to):
            continue
        rec = build_record("open-meteo", p, fetched_at, run_id, status, slice_hour(payload, i), params,
                           mode="backfill", scheduled_hour=t, observation_time=t,
                           payload_valid=True, validation_error=None)
        out.append((t[:10], rec))
    return out


def run_backfill(locs, day, hour_from=0, hour_to=23):
    """Backfill satu tanggal (WIB). Data ditulis per lokasi."""
    run_id = str(uuid.uuid4())
    total, t0 = len(locs), time.time()
    ok_loc = failed_loc = 0

    with source_lock("weather"):
        lg.info(f"START backfill {run_id[:8]} | {day} jam {hour_from:02d}-{hour_to:02d} | {total} titik")
        for i, (_, p) in enumerate(locs.iterrows(), 1):
            n_rec, status = 0, None
            try:
                fetched_at = datetime.now(timezone.utc)
                status, pl, pr = fetch_weather_hourly(p["lat"], p["lon"], day, lg)
                recs = build_hour_records(p, fetched_at, run_id, status, pl, pr, hour_from, hour_to)
                by_day = {}
                for d, r in recs:
                    by_day.setdefault(d, []).append(r)
                for d, rs in by_day.items():
                    append_jsonl(bronze_path("weather", d), rs)
                n_rec = len(recs)
                good = bool(recs) and all(r["payload_valid"] for _, r in recs)
            except Exception:
                good = False
                lg.exception(f"[{i}/{total}] {p['name']} | error tak terduga")
            ok_loc += good
            failed_loc += not good
            lg.log(logging.INFO if good else logging.WARNING,
                   f"[{i}/{total}] {p['name']} | status={status} | {n_rec} record")
        lg.info(f"DONE backfill {run_id[:8]} {day} | lokasi ok {ok_loc}/{total}, gagal {failed_loc} "
                f"| {time.time() - t0:.1f}s")
    return {"day": day, "ok": ok_loc, "failed": failed_loc}


def parse_hour(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H")
    except ValueError:
        raise argparse.ArgumentTypeError(f'format harus "YYYY-MM-DD HH", dapat: {s!r}')


def validate_range(start, end):
    """start/end: datetime WIB naive per jam. Raise ConfigError jika tidak valid."""
    now = datetime.now(WIB).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    if start > end:
        raise ConfigError("--start harus <= --end")
    if end > now:
        raise ConfigError(f"--end tidak boleh di masa depan (jam WIB sekarang: {now:%Y-%m-%d %H})")
    if start < now - timedelta(days=MAX_PAST_DAYS):
        lg.warning(f"--start lebih dari {MAX_PAST_DAYS} hari ke belakang; "
                   "Open-Meteo mungkin menolak atau mengembalikan data kosong")


def run_range(locs, start, end):
    """start/end: datetime WIB per jam, inklusif."""
    results = []
    d = start.date()
    while d <= end.date():
        hf = start.hour if d == start.date() else 0
        ht = end.hour if d == end.date() else 23
        results.append(run_backfill(locs, d.isoformat(), hf, ht))
        d += timedelta(days=1)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, type=parse_hour, help='contoh: "2026-10-30 00"')
    ap.add_argument("--end", type=parse_hour, help='contoh: "2026-10-31 20" (default: jam WIB sekarang)')
    ap.add_argument("--only", help='filter nama titik, contoh: "HR Muhammad"')
    a = ap.parse_args(argv)

    setup_logging()
    try:
        end = a.end or datetime.now(WIB).replace(tzinfo=None, minute=0, second=0, microsecond=0)
        validate_range(a.start, end)

        locs = load_locations()
        if a.only:
            locs = locs[locs["name"].str.contains(a.only, case=False, regex=False)]
            if locs.empty:
                raise ConfigError(f'tidak ada titik yang cocok dengan "{a.only}"')

        run_range(locs, a.start, end)
    except (ConfigError, LockBusy) as e:
        lg.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
