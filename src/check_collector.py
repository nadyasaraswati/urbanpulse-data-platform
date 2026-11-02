import os, time, csv
from datetime import datetime, timezone
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("TOMTOM_API_KEY")
TOMTOM = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
METEO = "https://api.open-meteo.com/v1/forecast"
OUT = "data/raw.csv"


def get_traffic(lat, lon):
    r = requests.get(TOMTOM, params={"key": KEY, "point": f"{lat},{lon}"}, timeout=15)
    r.raise_for_status()
    d = r.json()["flowSegmentData"]
    return {
        "frc": d["frc"],
        "current_speed": d["currentSpeed"],
        "free_flow_speed": d["freeFlowSpeed"],
        "current_travel_time": d["currentTravelTime"],
        "free_flow_travel_time": d["freeFlowTravelTime"],
        "confidence": d["confidence"],
        "road_closure": d.get("roadClosure", False),
        "seg_lat": d["coordinates"]["coordinate"][0]["latitude"],
        "seg_lon": d["coordinates"]["coordinate"][0]["longitude"],
    }


def get_weather(lat, lon):
    r = requests.get(METEO, params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,precipitation,rain,weather_code,wind_speed_10m",
    }, timeout=15)
    r.raise_for_status()
    return r.json()["current"]

def load_locations(path="config/locations.csv"):
    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "ID": "id",
        "Titik Jalan": "name",
        "Latitude": "lat",
        "Longitude": "lon",
        "Area": "area",
    })
    for c in ["lat", "lon"]:
        df[c] = df[c].astype(str).str.replace(",", ".").astype(float)
    return df[["id", "name", "lat", "lon", "area"]]

def collect_once(locs):
    ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for _, p in locs.iterrows():
        try:            
            row = {"ts_utc": ts, "id": p["id"], "name": p["name"], "area": p["area"]}
            row |= get_traffic(p["lat"], p["lon"])
            w = get_weather(p["lat"], p["lon"])
            row |= {f"w_{k}": v for k, v in w.items() if k != "time"}
            rows.append(row)
        except Exception as e:
            print(f"[skip] {p['name']}: {e}")
    return rows


def append_csv(rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        if new:
            w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    locs = load_locations()
    while True:
        rows = collect_once(locs)
        append_csv(rows)
        print(datetime.now().strftime("%H:%M:%S"), f"saved {len(rows)} rows")
        time.sleep(900)  # 15 menits