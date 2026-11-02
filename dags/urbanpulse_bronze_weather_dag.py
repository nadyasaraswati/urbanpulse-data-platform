"""DAG UrbanPulse: ingestion Bronze tiap jam (00:00-23:00 WIB).

ingest_weather ┐
               ├─> check_run_summary (alert jika ada lokasi gagal)
ingest_traffic ┘

- weather dan traffic jalan paralel; kegagalan salah satu tidak menghentikan yang lain.
- retries=0: Bronze append-only, retry otomatis akan menggandakan record.
  Jalankan ulang task yang gagal secara manual dari UI jika perlu.
- Kegagalan sebagian lokasi TIDAK membuat task gagal; dicatat di Bronze dan ringkasan run.
"""
from datetime import datetime, timedelta, timezone

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable

WIB = timezone(timedelta(hours=7))
TZ = "Asia/Jakarta"


def _scheduled_hour(logical_date, dag_run):
    """Jam target WIB 'YYYY-MM-DDTHH:00' dari jadwal Airflow (bukan jam mulai task)."""
    ts = logical_date or getattr(dag_run, "run_after", None)
    if ts is None:
        return None  # fallback: jam saat run dimulai
    return ts.astimezone(WIB).strftime("%Y-%m-%dT%H:00")


with DAG(
    dag_id="urbanpulse_ingestion",
    description="Ingestion Bronze UrbanPulse (TomTom traffic + Open-Meteo weather), tiap jam WIB",
    start_date=pendulum.datetime(2026, 10, 31, tz=TZ),
    schedule=CronTriggerTimetable("0 * * * *", timezone=TZ),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=50),
    default_args={"retries": 0},
    tags=["urbanpulse", "ingestion", "bronze"],
) as dag:

    @task(task_id="ingest_weather")
    def ingest_weather(logical_date=None, dag_run=None):
        from ingestion.ingestion_weather import run_weather_ingestion
        run_weather_ingestion(scheduled_hour=_scheduled_hour(logical_date, dag_run))

    @task(task_id="check_run_summary", trigger_rule="all_done")
    def check_run_summary(logical_date=None, dag_run=None):
        """Baca runs.jsonl; log peringatan (dan kirim Slack jika SLACK_WEBHOOK_URL di-set)."""
        import logging
        from ingestion.alerts import build_alert, send_slack
        hour = _scheduled_hour(logical_date, dag_run)
        msg = build_alert(hour, sources=("weather",))
        if msg is None:
            logging.info("Semua lokasi sukses untuk jam %s", hour)
            return
        logging.warning(msg)
        send_slack(msg)

    [ingest_weather()] >> check_run_summary()
