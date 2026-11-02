"""DAG UrbanPulse: Silver -> Gold tiap jam pada menit ke-20 (WIB).

build_gold : isi gold.dim_location, hitung ulang gold.traffic_hourly dan
             gold.weather_traffic_hourly untuk hari ini + kemarin (upsert idempotent).

- Jalan 10 menit setelah urbanpulse_silver (menit ke-10). Tanpa sensor: jika Silver sempat
  terlambat, run Gold berikutnya otomatis memperbaiki karena jendelanya 2 hari.
- retries=0: aman dijalankan ulang manual dari UI.
- Task merah jika koneksi DB, validasi join, atau query gagal (transaksi di-rollback).
"""
from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable

TZ = "Asia/Jakarta"
RECOMPUTE_DAYS = 2  # hari ini + kemarin (WIB)

with DAG(
    dag_id="urbanpulse_gold",
    description="Silver -> Gold UrbanPulse (traffic_hourly + weather_traffic_hourly), tiap jam menit ke-20 WIB",
    start_date=pendulum.datetime(2026, 11, 1, tz=TZ),
    schedule=CronTriggerTimetable("20 * * * *", timezone=TZ),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=20),
    default_args={"retries": 0},
    tags=["urbanpulse", "gold"],
) as dag:

    @task(task_id="build_gold")
    def build_gold():
        from gold.gold_build import run, setup_logging
        setup_logging()
        run(days=RECOMPUTE_DAYS)  # exception diteruskan -> task merah

    build_gold()
