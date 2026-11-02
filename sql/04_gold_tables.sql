-- Gold: agregasi per jam + gabungan cuaca. Semua bisa dibangun ulang dari Silver.
-- Aman dijalankan berulang.

CREATE TABLE IF NOT EXISTS gold.dim_location (
    location_id   TEXT PRIMARY KEY,
    location_name TEXT NOT NULL,
    area          TEXT,
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 1 baris per lokasi per jam (WIB). Slot 07:00 dan 07:30 -> jam 07:00.
CREATE TABLE IF NOT EXISTS gold.traffic_hourly (
    location_id          TEXT        NOT NULL,
    hour_ts              TIMESTAMPTZ NOT NULL,
    avg_speed            DOUBLE PRECISION,
    min_speed            DOUBLE PRECISION,
    avg_congestion_ratio DOUBLE PRECISION,   -- NULL jika free_flow_speed <= 0 di semua slot
    max_congestion_ratio DOUBLE PRECISION,
    observation_count    INTEGER     NOT NULL,   -- jumlah slot 30 menit yang tersedia (1 atau 2)
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (location_id, hour_ts)
);

-- Traffic sebagai tabel utama, cuaca LEFT JOIN. Fitur waktu dalam WIB.
CREATE TABLE IF NOT EXISTS gold.weather_traffic_hourly (
    location_id          TEXT        NOT NULL,
    hour_ts              TIMESTAMPTZ NOT NULL,
    obs_date             DATE        NOT NULL,
    hour_of_day          SMALLINT    NOT NULL,   -- 0-23
    day_of_week          SMALLINT    NOT NULL,   -- ISO 8601: 1=Senin ... 7=Minggu
    is_weekend           BOOLEAN     NOT NULL,   -- Sabtu/Minggu
    avg_speed            DOUBLE PRECISION,
    min_speed            DOUBLE PRECISION,
    avg_congestion_ratio DOUBLE PRECISION,
    max_congestion_ratio DOUBLE PRECISION,
    observation_count    INTEGER     NOT NULL,
    temperature          DOUBLE PRECISION,
    humidity             DOUBLE PRECISION,
    precipitation        DOUBLE PRECISION,
    wind_speed           DOUBLE PRECISION,
    cloud_cover          DOUBLE PRECISION,
    weather_code         SMALLINT,
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (location_id, hour_ts)
);

CREATE INDEX IF NOT EXISTS idx_wth_hour_ts ON gold.weather_traffic_hourly (hour_ts);
