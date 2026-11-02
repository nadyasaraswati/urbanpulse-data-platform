# UrbanPulse

Real-time traffic & weather analytics pipeline for Surabaya — ingests data from APIs, processes through Bronze → Silver → Gold layers, and visualizes via Power BI and Streamlit.

## Architecture

```
APIs (Traffic + Weather)
        ↓
   Bronze Layer          ← raw JSON, partitioned by month
        ↓
   Silver Layer          ← cleaned, enriched, 30-min slots
        ↓
   Gold Layer            ← hourly aggregates, dim tables
        ↓
  Power BI / Streamlit
```

## Stack

- **Orchestration:** Apache Airflow (Docker)
- **Storage:** PostgreSQL (Bronze → Gold)
- **Processing:** Python (pandas)
- **Visualization:** Power BI (PBIP), Streamlit
- **Locations:** 10 roads across 5 areas in Surabaya

## Project Structure

```
urbanpulse/
├── dags/                    # Airflow DAGs
├── src/
│   ├── ingestion/           # Traffic & weather ingestors
│   ├── silver/              # Silver layer transforms
│   ├── gold/                # Gold layer aggregations
│   └── quality_checks.py    # Data quality checks
├── sql/                     # DDL schemas
├── config/
│   └── locations.csv        # Road locations config
├── notebooks/               # EDA
├── UrbanPulse/              # Power BI PBIP project
├── urbanpulse_powerbi/      # Power BI assets (DAX, M query)
├── urbanpulse_streamlit/    # Streamlit dashboard
├── docker-compose.yml
├── docker-compose.airflow.yml
└── Dockerfile.airflow
```

## Quick Start

### 1. Setup environment

```bash
cp .env.example .env   # fill in API keys & DB credentials
```

`.env` variables needed:
```
TRAFFIC_API_KEY=
WEATHER_API_KEY=
POSTGRES_HOST=
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
```

### 2. Start services

```bash
docker compose -f docker-compose.yml up -d          # PostgreSQL
docker compose -f docker-compose.airflow.yml up -d  # Airflow
```

### 3. Run pipeline

Enable DAGs in order:
1. `urbanpulse_ingestion` — hourly weather ingest
2. `urbanpulse_traffic` — 30-min traffic ingest
3. `urbanpulse_silver` — silver transforms
4. `urbanpulse_transform` — silver + gold build
5. `urbanpulse_gold` — gold aggregations

### 4. Streamlit dashboard

```bash
cd urbanpulse_streamlit
pip install -r requirements.txt
streamlit run app.py
```

Place CSV exports in `urbanpulse_streamlit/data/`:
- `gold_dim_location.csv`
- `gold_weather_traffic_hourly.csv`
- `silver_traffic_30min.csv`
- `silver_weather_hourly.csv`

### 5. Power BI

Open `UrbanPulse/UrbanPulse.pbip` in Power BI Desktop, or follow the manual build guide in [`UrbanPulse/README_PowerBI.md`](UrbanPulse/README_PowerBI.md).

## Data

| Layer | Content |
|---|---|
| Bronze | Raw JSON from APIs, partitioned `data/bronze/{traffic,weather}/YYYY-MM/` |
| Silver | `silver_traffic_enriched.csv` — 30-min slots with congestion flags |
| Gold | `gold_weather_traffic_hourly.csv`, `gold_dim_location.csv` |

> Data files are excluded from this repo (see `.gitignore`). Run the pipeline to generate them.

## Locations

10 roads monitored: Basuki Rahmat, Mayjen Sungkono, HR Muhammad, Tunjungan, Ahmad Yani, Darmo, Kertajaya, Gubeng, Kenjeran, Rajawali — across areas: Pusat, Barat, Selatan, Timur, Utara.
