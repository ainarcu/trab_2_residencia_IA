-- Inicializacao PostgreSQL + PostGIS + TimescaleDB para POA Flood ML
-- Compativel com a imagem timescale/timescaledb-ha:pg16 do docker-compose.yml

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. Estacoes hidrometeorologicas (ANA, SEMA-RS, CEMADEN, INMET)
CREATE TABLE IF NOT EXISTS hydro_stations (
    station_id      TEXT PRIMARY KEY,
    source          TEXT NOT NULL,              -- 'ANA', 'SEMA_RS', 'CEMADEN', 'INMET'
    name            TEXT NOT NULL,
    basin           TEXT,                       -- 'Guaiba', 'Jacui', 'Taquari', 'Cai', 'Sinos', 'Gravatai'
    geom            GEOMETRY(Point, 4326) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hydro_stations_geom
    ON hydro_stations USING GIST (geom);

-- 2. Series temporais de telemetria (Hypertable TimescaleDB)
CREATE TABLE IF NOT EXISTS hydro_readings (
    time            TIMESTAMPTZ NOT NULL,
    station_id      TEXT NOT NULL REFERENCES hydro_stations(station_id),
    level_m         DOUBLE PRECISION,
    flow_m3s        DOUBLE PRECISION,
    rain_mm         DOUBLE PRECISION,
    wind_speed_ms   DOUBLE PRECISION,
    wind_dir_deg    DOUBLE PRECISION,
    quality_flag    SMALLINT NOT NULL DEFAULT 0, -- 0=ok, 1=interpolado, 2=suspeito
    PRIMARY KEY (time, station_id)
);

SELECT create_hypertable('hydro_readings', 'time', if_not_exists => TRUE);

-- 3. Casas de bombas (EBAPs) e comportas do sistema de protecao (DMAE)
CREATE TABLE IF NOT EXISTS ebap_assets (
    ebap_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    neighborhood    TEXT,
    capacity_m3s    DOUBLE PRECISION,
    is_operational  BOOLEAN NOT NULL DEFAULT TRUE,
    geom            GEOMETRY(Point, 4326) NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ebap_assets_geom
    ON ebap_assets USING GIST (geom);

-- 4. Previsoes probabilisticas de cota do Guaiba (saida do modelo LSTM)
CREATE TABLE IF NOT EXISTS guaiba_forecasts (
    forecast_id             BIGSERIAL PRIMARY KEY,
    timestamp_ref           TIMESTAMPTZ NOT NULL,
    horizon_hours           INTEGER NOT NULL CHECK (horizon_hours BETWEEN 1 AND 168),
    guaiba_p10_m            DOUBLE PRECISION NOT NULL,
    guaiba_p50_m            DOUBLE PRECISION NOT NULL,
    guaiba_p90_m            DOUBLE PRECISION NOT NULL,
    prob_exceed_alert_3m    DOUBLE PRECISION NOT NULL CHECK (prob_exceed_alert_3m BETWEEN 0 AND 1),
    model_version           TEXT NOT NULL DEFAULT 'v0.1.0',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_guaiba_forecasts_ref
    ON guaiba_forecasts (timestamp_ref DESC, horizon_hours);

-- 5. Microzonas / Setores censitarios de Porto Alegre (IBGE + CadUnico + ObservaPOA)
CREATE TABLE IF NOT EXISTS microzones (
    sector_id           TEXT PRIMARY KEY,
    neighborhood        TEXT NOT NULL,
    population          INTEGER NOT NULL DEFAULT 0,
    cadunico_families   INTEGER NOT NULL DEFAULT 0,
    critical_assets     JSONB NOT NULL DEFAULT '[]'::jsonb,
    geom                GEOMETRY(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_microzones_geom
    ON microzones USING GIST (geom);

-- 6. Impactos focalizados calculados por cenario de EBAP
CREATE TABLE IF NOT EXISTS microzone_impacts (
    impact_id           BIGSERIAL PRIMARY KEY,
    forecast_id         BIGINT NOT NULL REFERENCES guaiba_forecasts(forecast_id) ON DELETE CASCADE,
    sector_id           TEXT NOT NULL REFERENCES microzones(sector_id),
    scenario            TEXT NOT NULL CHECK (scenario IN ('all_operational', 'partial', 'severe')),
    prob_depth_gt_30cm  DOUBLE PRECISION NOT NULL CHECK (prob_depth_gt_30cm BETWEEN 0 AND 1),
    expected_depth_m    DOUBLE PRECISION NOT NULL CHECK (expected_depth_m >= 0),
    exposed_population  INTEGER NOT NULL CHECK (exposed_population >= 0),
    impact_score        DOUBLE PRECISION NOT NULL CHECK (impact_score BETWEEN 0 AND 100),
    raster_uri          TEXT,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (forecast_id, sector_id, scenario)
);

CREATE INDEX IF NOT EXISTS idx_microzone_impacts_score
    ON microzone_impacts (forecast_id, scenario, impact_score DESC);