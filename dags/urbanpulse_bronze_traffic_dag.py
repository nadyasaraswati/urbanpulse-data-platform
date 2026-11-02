"""DAG UrbanPulse: ingestion Bronze traffic tiap 30 menit (00:00, 00:30, ... 23:30 WIB)."""
from datetime import datetime, timedelta, timezone

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable

WIB = timezone(timedelta(hours=7))
TZ = "Asia/Jakarta"


def _slot(logical_date, dag_run):
    """Slot WIB 'YYYY-MM-DDTHH:MM' dari jadwal Airflow (HH:00 atau HH:30)."""
    ts = logical_date or getattr(dag_run, "run_after", None)
    if ts is None:
        return None
    return ts.astimezone(WIB).strftime("%Y-%m-%dT%H:%M")


with DAG(
    dag_id="urbanpulse_traffic",
    description="Ingestion Bronze TomTom traffic, tiap 30 menit WIB",
    start_date=pendulum.datetime(2026, 10, 31, tz=TZ),
    schedule=CronTriggerTimetable("0,30 * * * *", timezone=TZ),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=25),
    default_args={"retries": 0},
    tags=["urbanpulse", "ingestion", "bronze"],
) as dag:

    @task(task_id="ingest_traffic")
    def ingest_traffic(logical_date=None, dag_run=None):
        from ingestion.ingestion_traffic import run_traffic_ingestion
        run_traffic_ingestion(scheduled_hour=_slot(logical_date, dag_run))

    @task(task_id="check_run_summary", trigger_rule="all_done")
    def check_run_summary(logical_date=None, dag_run=None):
        import logging
        from ingestion.alerts import build_alert, send_slack
        slot = _slot(logical_date, dag_run)
        msg = build_alert(slot, sources=("traffic",))
        if msg is None:
            logging.info("Semua lokasi sukses untuk slot %s", slot)
            return
        logging.warning(msg)
        send_slack(msg)

    ingest_traffic() >> check_run_summary()