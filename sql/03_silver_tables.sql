-- Silver: data bersih, 1 baris per kunci waktu. Aman dijalankan berulang.

CREATE TABLE IF NOT EXISTS silver.weather_hourly (
    location_id    TEXT        NOT NULL,
    hour_ts        TIMESTAMPTZ NOT NULL,          -- jam pengamatan (WIB), dari scheduled_hour
    temperature    DOUBLE PRECISION,
    humidity       DOUBLE PRECISION,
    precipitation  DOUBLE PRECISION,
    wind_speed     DOUBLE PRECISION,
    cloud_cover    DOUBLE PRECISION,
    weather_code   SMALLINT,
    ingestion_mode TEXT        NOT NULL,          -- live | backfill (jejak asal data)
    ingestion_id   TEXT        NOT NULL,          -- rujukan balik ke record Bronze
    fetched_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (location_id, hour_ts)
);

CREATE TABLE IF NOT EXISTS silver.traffic_30min (
    location_id           TEXT        NOT NULL,
    observation_ts        TIMESTAMPTZ NOT NULL,   -- slot 30 menit (WIB), dari scheduled_hour
    current_speed         DOUBLE PRECISION NOT NULL,
    free_flow_speed       DOUBLE PRECISION NOT NULL,
    current_travel_time   DOUBLE PRECISION,
    free_flow_travel_time DOUBLE PRECISION,
    confidence            DOUBLE PRECISION,
    road_closure          BOOLEAN,
    frc                   TEXT,
    segment_coordinates   JSONB,
    ingestion_mode        TEXT        NOT NULL,
    ingestion_id          TEXT        NOT NULL,
    fetched_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (location_id, observation_ts)
);

-- Penanda kemajuan baca file Bronze. byte_offset = posisi terakhir yang sudah diproses,
-- jadi baris baru yang ditambahkan ke file yang sama tetap terbaca di run berikutnya.
CREATE TABLE IF NOT EXISTS silver.processed_files (
    id               BIGSERIAL PRIMARY KEY,
    file_name        TEXT        NOT NULL,
    source           TEXT        NOT NULL CHECK (source IN ('weather', 'traffic')),
    status           TEXT        NOT NULL CHECK (status IN ('success', 'failed')),
    byte_offset      BIGINT      NOT NULL DEFAULT 0,
    records_read     INTEGER     NOT NULL DEFAULT 0,   -- angka di bawah = run terakhir
    records_skipped  INTEGER     NOT NULL DEFAULT 0,
    records_valid    INTEGER     NOT NULL DEFAULT 0,
    records_rejected INTEGER     NOT NULL DEFAULT 0,
    records_upserted INTEGER     NOT NULL DEFAULT 0,
    processed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_message    TEXT,
    UNIQUE (file_name, source)
);
