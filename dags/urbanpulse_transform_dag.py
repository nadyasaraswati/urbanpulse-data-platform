"""DAG UrbanPulse: transformasi Bronze -> Silver -> Gold + data quality, tiap 30 menit.

silver_weather -> silver_traffic -> gold_build -> quality_checks

- Jadwal menit 10 dan 40 WIB: 10 menit setelah ingestion traffic (:00/:30) dan weather (:00).
  Urutan antar-DAG dijamin oleh jadwal, bukan sensor (hasilnya idempotent, run berikutnya memperbaiki).
- Menggantikan DAG urbanpulse_silver dan urbanpulse_gold: hapus/pause keduanya agar tidak jalan dobel.
- silver_traffic dan quality_checks memakai trigger_rule=all_done: kegagalan weather tidak menghentikan
  traffic, dan quality check selalu melapor. DAG run tetap merah jika ada task yang gagal.
- retries=0: semua langkah idempotent, aman dijalankan ulang manual dari UI.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable

TZ = "Asia/Jakarta"

with DAG(
    dag_id="urbanpulse_transform",
    description="Silver -> Gold + quality checks UrbanPulse, menit 10 dan 40 WIB",
    start_date=pendulum.datetime(2026, 11, 1, tz=TZ),
    schedule=CronTriggerTimetable("10,40 * * * *", timezone=TZ),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=25),
    default_args={"retries": 0},
    tags=["urbanpulse", "silver", "gold", "quality"],
) as dag:

    def _silver(source):
        from silver.silver_build import run, setup_logging
        setup_logging()
        total = run((source,), start=None, end=None, full=False, dry_run=False)
        if total["failed"]:
            raise RuntimeError(f"Silver {source}: {total['failed']} file Bronze gagal diproses")

    @task(task_id="silver_weather")
    def silver_weather():
        _silver("weather")

    @task(task_id="silver_traffic", trigger_rule="all_done")
    def silver_traffic():
        _silver("traffic")

    @task(task_id="gold_build")
    def gold_build():
        from gold.gold_build import run, setup_logging
        setup_logging()
        run(days=2)  # exception diteruskan -> task merah

    @task(task_id="quality_checks", trigger_rule="all_done")
    def quality_checks():
        from quality_checks import run_checks, setup_logging
        setup_logging()
        failures = run_checks()
        if failures:
            raise RuntimeError("Quality check gagal: " + "; ".join(failures))

    silver_weather() >> silver_traffic() >> gold_build() >> quality_checks()
