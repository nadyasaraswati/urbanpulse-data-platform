"""UrbanPulse - baca ringkasan run (data/logs/runs.jsonl) dan susun pesan alert.

Tanpa dependency tambahan. Slack bersifat opsional: aktif jika env SLACK_WEBHOOK_URL di-set.
"""
import json
import os

import requests

from ingestion.common import RUNS_SUMMARY, redact

SOURCES = ("weather", "traffic")


def latest_summaries(scheduled_hour, path=RUNS_SUMMARY):
    """Ringkasan terakhir per sumber untuk jam tertentu (live)."""
    found = {}
    if not path.exists():
        return found
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if s.get("scheduled_hour") == scheduled_hour and s.get("mode") == "live":
                found[s.get("source")] = s
    return found


def build_alert(scheduled_hour, path=RUNS_SUMMARY, sources=SOURCES):
    """Mengembalikan teks alert, atau None jika semua lokasi sukses."""
    found = latest_summaries(scheduled_hour, path)
    lines = []
    for src in sources:
        s = found.get(src)
        if s is None:
            lines.append(f"{src.upper()}: tidak ada ringkasan run (task kemungkinan gagal sebelum selesai)")
        elif s["failed"]:
            lines.append(f"{src.upper()}: {s['ok']}/{s['total']} ok")
            lines += [f"- {x['location']} ({x['reason']})" for x in s["failed"]]
    if not lines:
        return None
    return f"UrbanPulse ingestion {scheduled_hour} WIB - ada kegagalan\n" + "\n".join(lines)

def send_slack(text):
    """Kirim ke Slack jika SLACK_WEBHOOK_URL di-set. Mengembalikan True jika terkirim."""
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return False
    try:
        requests.post(url, json={"text": text}, timeout=10).raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Gagal kirim Slack: {redact(str(e))}")
        return False
