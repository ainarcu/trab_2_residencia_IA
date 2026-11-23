"""
Cliente de ingestao para estacoes automaticas do INMET (chuva e vento).
Alimenta `hydro_stations` e `hydro_readings` (rain_mm, wind_speed_ms, wind_dir_deg).
"""

from datetime import datetime, timedelta, timezone
import os
import httpx
from sqlalchemy import create_engine, text

INMET_BASE_URL = "https://apitempo.inmet.gov.br"

# Estacoes automaticas do INMET em Porto Alegre (catalogo oficial INMET)
DEFAULT_INMET_STATIONS: dict[str, dict] = {
    "A801": {
        "name": "Porto Alegre - Jardim Botanico",
        "basin": "Guaiba",
        "lat": -30.053611,
        "lon": -51.174722,
    },
    "B807": {
        "name": "Porto Alegre - Belem Novo",
        "basin": "Guaiba",
        "lat": -30.186900,
        "lon": -51.181900,
    },
}


def _parse_float(value: str | float | None) -> float | None:
    if value in (None, "", "null"):
        return None
    return float(str(value).replace(",", "."))


def _parse_inmet_utc(dt_medicao: str, hr_medicao: str) -> datetime:
    # INMET retorna data "YYYY-MM-DD" e hora UTC "HHMM" (ex.: "1300")
    hhmm = str(hr_medicao).zfill(4)[:4]
    dt = datetime.strptime(f"{dt_medicao} {hhmm}", "%Y-%m-%d %H%M")
    return dt.replace(tzinfo=timezone.utc)


def ensure_inmet_stations(engine, stations: dict[str, dict]) -> None:
    sql = text(
        """
        INSERT INTO hydro_stations (station_id, source, name, basin, geom)
        VALUES (:station_id, 'INMET', :name, :basin, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        ON CONFLICT (station_id) DO UPDATE SET
            name  = EXCLUDED.name,
            basin = EXCLUDED.basin,
            geom  = EXCLUDED.geom;
        """
    )
    rows = [
        {"station_id": code, "name": meta["name"], "basin": meta["basin"], "lat": meta["lat"], "lon": meta["lon"]}
        for code, meta in stations.items()
    ]
    with engine.begin() as conn:
        conn.execute(sql, rows)


def fetch_inmet_hourly(
    station_code: str,
    start_date: str,
    end_date: str,
    token: str | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Consulta leituras horarias de uma estacao automatica do INMET."""
    if token:
        url = f"{INMET_BASE_URL}/token/estacao/{start_date}/{end_date}/{station_code}/{token}"
    else:
        url = f"{INMET_BASE_URL}/estacao/{start_date}/{end_date}/{station_code}"

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, list) else []


def ingest_inmet_stations(
    stations: dict[str, dict] | None = None,
    days_back: int = 2,
    database_url: str | None = None,
) -> int:
    db_url = database_url or os.environ["DATABASE_URL"]
    token = os.getenv("INMET_TOKEN") or None
    target_stations = stations or DEFAULT_INMET_STATIONS

    engine = create_engine(db_url)
    ensure_inmet_stations(engine, target_stations)

    end_dt = datetime.now(timezone.utc).date()
    start_dt = end_dt - timedelta(days=days_back)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    records: list[dict] = []
    for code in target_stations:
        rows = fetch_inmet_hourly(
            station_code=code,
            start_date=start_str,
            end_date=end_str,
            token=token,
        )
        for item in rows:
            dt_str = item.get("DT_MEDICAO")
            hr_str = item.get("HR_MEDICAO")
            if not dt_str or hr_str is None:
                continue

            rain = _parse_float(item.get("CHUVA"))
            wind_spd = _parse_float(item.get("VEN_VEL"))
            wind_dir = _parse_float(item.get("VEN_DIR"))
            if rain is None and wind_spd is None and wind_dir is None:
                continue

            records.append(
                {
                    "time": _parse_inmet_utc(dt_str, str(hr_str)),
                    "station_id": code,
                    "rain_mm": rain,
                    "wind_speed_ms": wind_spd,
                    "wind_dir_deg": wind_dir,
                    "quality_flag": 0,
                }
            )

    if not records:
        return 0

    upsert_sql = text(
        """
        INSERT INTO hydro_readings (
            time, station_id, rain_mm, wind_speed_ms, wind_dir_deg, quality_flag
        )
        VALUES (
            :time, :station_id, :rain_mm, :wind_speed_ms, :wind_dir_deg, :quality_flag
        )
        ON CONFLICT (time, station_id) DO UPDATE SET
            rain_mm       = COALESCE(EXCLUDED.rain_mm, hydro_readings.rain_mm),
            wind_speed_ms = COALESCE(EXCLUDED.wind_speed_ms, hydro_readings.wind_speed_ms),
            wind_dir_deg  = COALESCE(EXCLUDED.wind_dir_deg, hydro_readings.wind_dir_deg),
            quality_flag  = EXCLUDED.quality_flag;
        """
    )

    with engine.begin() as conn:
        conn.execute(upsert_sql, records)

    return len(records)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingestao INMET (chuva e vento) -> TimescaleDB")
    parser.add_argument("--days-back", type=int, default=2, help="Dias anteriores para buscar")
    args = parser.parse_args()

    total = ingest_inmet_stations(days_back=args.days_back)
    print(f"Leituras meteorologicas gravadas em hydro_readings: {total}")
