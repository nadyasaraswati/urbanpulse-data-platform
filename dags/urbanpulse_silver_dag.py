"""DAG UrbanPulse: Bronze -> Silver tiap jam pada menit ke-10 (WIB).

build_silver : baca baris Bronze baru (incremental via silver.processed_files), validasi,
               dedup, upsert ke silver.weather_hourly dan silver.traffic_30min.

- retries=0: upsert idempotent, jadi aman dijalankan ulang manual dari UI.
- Task gagal (merah) jika koneksi DB gagal ATAU ada file Bronze yang gagal diproses.
  File lain tetap diproses lebih dulu; file yang gagal dicoba lagi di run berikutnya.
- Record yang ditolak validasi (mis. http_status=None) TIDAK membuat task gagal; dicatat di log.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable

TZ = "Asia/Jakarta"

with DAG(
    dag_id="urbanpulse_silver",
    description="Bronze -> Silver UrbanPulse (weather_hourly + traffic_30min), tiap jam menit ke-10 WIB",
    start_date=pendulum.datetime(2026, 11, 1, tz=TZ),
    schedule=CronTriggerTimetable("10 * * * *", timezone=TZ),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=20),
    default_args={"retries": 0},
    tags=["urbanpulse", "silver"],
) as dag:

    @task(task_id="build_silver")
    def build_silver():
        from silver.silver_build import SOURCES, run, setup_logging
        setup_logging()
        total = run(SOURCES, start=None, end=None, full=False, dry_run=False)
        if total["failed"]:
            raise RuntimeError(
                f"Silver: {total['failed']} file Bronze gagal diproses "
                "(detail di log dan kolom error_message silver.processed_files)")

    build_silver()
