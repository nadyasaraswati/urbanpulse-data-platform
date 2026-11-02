import argparse, uuid, time, logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from ingest_bronze import (load_locations, build_record, append_jsonl, setup_logging,
                           _get, METEO_URL, WIB, BRONZE, BASE, log)

METEO_HOURLY = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,cloud_cover,weather_code"


def fetch_weather_hourly(lat, lon, day):
    params = {"latitude": lat, "longitude": lon, "hourly": METEO_HOURLY,
              "start_date": day, "end_date": day, "timezone": "Asia/Jakarta"}
    return _get(METEO_URL, params, params)


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
    """Return list of (tanggal_observasi, record)."""
    if status != 200 or "hourly" not in payload:  # request gagal: tetap dicatat
        rec = build_record("open-meteo", p, fetched_at, run_id, status, payload, params, mode="backfill")
        rec["scheduled_hour"] = None
        return [(params["start_date"], rec)]

    out = []
    for i, t in enumerate(payload["hourly"]["time"]):  # t = "2026-10-31T05:00"
        if not (hour_from <= int(t[11:13]) <= hour_to):
            continue
        rec = build_record("open-meteo", p, fetched_at, run_id, status,
                           slice_hour(payload, i), params, mode="backfill")
        rec["observation_time"] = t
        rec["scheduled_hour"] = t
        out.append((t[:10], rec))
    return out


def run_backfill(locs, day, hour_from=0, hour_to=23):
    fetched_at = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    by_day = defaultdict(list)
    total, t0 = len(locs), time.time()
    log.info(f"START backfill {run_id[:8]} | {day} jam {hour_from:02d}-{hour_to:02d} | {total} titik")

    for i, (_, p) in enumerate(locs.iterrows(), 1):
        s, pl, pr = fetch_weather_hourly(p["lat"], p["lon"], day)
        recs = build_hour_records(p, fetched_at, run_id, s, pl, pr, hour_from, hour_to)
        for d, r in recs:
            by_day[d].append(r)
        lvl = logging.INFO if s == 200 else logging.WARNING
        log.log(lvl, f"[{i}/{total}] {p['name']} | status={s} | {len(recs)} record")

    for d, recs in by_day.items():
        path = BRONZE / "weather" / f"{d}.jsonl"
        append_jsonl(path, recs)
        log.info(f"WRITE {path.relative_to(BASE)} (+{len(recs)})")
    log.info(f"DONE {day} | {time.time()-t0:.1f}s")


def parse_hour(s):
    return datetime.strptime(s, "%Y-%m-%d %H")


def run_range(locs, start, end):
    """start/end: datetime WIB per jam, inklusif."""
    d = start.date()
    while d <= end.date():
        hf = start.hour if d == start.date() else 0
        ht = end.hour if d == end.date() else 23
        run_backfill(locs, d.isoformat(), hf, ht)
        d += timedelta(days=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, type=parse_hour, help='contoh: "2026-10-30 00"')
    ap.add_argument("--end", type=parse_hour, help='contoh: "2026-10-31 20" (default: jam WIB sekarang)')
    ap.add_argument("--only", help='filter nama titik, contoh: "HR Muhammad"')
    a = ap.parse_args()

    end = a.end or datetime.now(WIB).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    if a.start > end:
        raise SystemExit("--start harus <= --end")

    locs = load_locations()
    if a.only:
        locs = locs[locs["name"].str.contains(a.only, case=False)]
        if locs.empty:
            raise SystemExit(f'tidak ada titik yang cocok dengan "{a.only}"')

    setup_logging()
    run_range(locs, a.start, end)