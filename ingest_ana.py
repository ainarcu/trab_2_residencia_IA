"""
Cliente de ingestao para a API oficial HidroWebService da ANA.
Documentacao: https://www.ana.gov.br/hidrowebservice/swagger-ui/index.html
Requer credenciais obtidas via hidro@ana.gov.br (ANA_IDENTIFICADOR e ANA_SENHA).
"""

from datetime import datetime, timedelta, timezone
import os
import time
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import create_engine, text

ANA_BASE_URL = "https://www.ana.gov.br/hidrowebservice/EstacoesTelemetricas"
BR_TZ = ZoneInfo("America/Sao_Paulo")

# Estacoes de referencia das bacias contribuintes do Guaiba (codigos ANA - ajustar conforme inventario)
DEFAULT_STATIONS: dict[str, dict] = {
    "87450004": {"name": "Cais Maua C6 (Guaiba)", "basin": "Guaiba", "lat": -30.0277, "lon": -51.2287},
    "85900000": {"name": "Rio Pardo (Jacui)", "basin": "Jacui", "lat": -29.9900, "lon": -52.3780},
    "86895000": {"name": "Estrela (Taquari)", "basin": "Taquari", "lat": -29.4950, "lon": -51.9660},
    "87170000": {"name": "Sao Sebastiao do Cai (Cai)", "basin": "Cai", "lat": -29.5860, "lon": -51.3750},
    "87382000": {"name": "Sao Leopoldo (Sinos)", "basin": "Sinos", "lat": -29.7540, "lon": -51.1470},
    "87406900": {"name": "Passo das Canoas (Gravatai)", "basin": "Gravatai", "lat": -29.9530, "lon": -51.0550},
}


class AnaHidroClient:
    """Gerencia autenticacao (token de 60 min) e consultas na API HidroWebService."""

    def __init__(self, identificador: str, senha: str, timeout: float = 30.0) -> None:
        self.identificador = identificador
        self.senha = senha
        self.client = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        # Reutiliza o token por ate 55 min (validade oficial: 60 min) para evitar bloqueio de IP
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = self.client.get(
            f"{ANA_BASE_URL}/OAUth/v1",
            headers={"Identificador": self.identificador, "Senha": self.senha},
        )
        resp.raise_for_status()
        payload = resp.json()
        token = (payload.get("items") or {}).get("tokenautenticacao")
        if not token:
            raise RuntimeError(f"Token nao retornado pela API da ANA: {payload}")

        self._token = token
        self._token_expires_at = time.time() + 55 * 60
        return token

    def fetch_telemetry(self, station_code: str, date_ref: datetime) -> list[dict]:
        """Busca medicoes telemétricas de uma estacao para a data de referencia (YYYY-MM-DD)."""
        token = self._get_token()
        resp = self.client.get(
            f"{ANA_BASE_URL}/HidroTelemetricaDetalhada/v1",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "Codigo_Estacao": station_code,
                "Tipo_Filtro_Data": "DATA_LEITURA",
                "Data_Busca": date_ref.strftime("%Y-%m-%d"),
                "Range_Intervalo": "DIAS_1",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items") or []


def _parse_float(value: str | float | None) -> float | None:
    if value in (None, "", "null"):
        return None
    return float(value)


def _parse_ana_timestamp(raw_dt: str) -> datetime:
    # Formato padrao ANA: "2024-01-01 23:15:00.0" (horario de Brasilia)
    clean = raw_dt.split(".")[0].strip()
    dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=BR_TZ).astimezone(timezone.utc)


def ensure_stations(engine, stations: dict[str, dict]) -> None:
    sql = text(
        """
        INSERT INTO hydro_stations (station_id, source, name, basin, geom)
        VALUES (:station_id, 'ANA', :name, :basin, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
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


def ingest_ana_stations(
    stations: dict[str, dict] | None = None,
    days_back: int = 1,
    database_url: str | None = None,
) -> int:
    identificador = os.environ["ANA_IDENTIFICADOR"]
    senha = os.environ["ANA_SENHA"]
    db_url = database_url or os.environ["DATABASE_URL"]

    target_stations = stations or DEFAULT_STATIONS
    engine = create_engine(db_url)
    ensure_stations(engine, target_stations)

    ana = AnaHidroClient(identificador=identificador, senha=senha)
    now_br = datetime.now(BR_TZ)
    records: list[dict] = []

    for code in target_stations:
        for offset in range(days_back + 1):
            ref_date = now_br - timedelta(days=offset)
            items = ana.fetch_telemetry(station_code=code, date_ref=ref_date)
            for item in items:
                raw_ts = item.get("Data_Hora_Medicao")
                if not raw_ts:
                    continue
                cota_cm = _parse_float(item.get("Cota_Adotada"))
                records.append(
                    {
                        "time": _parse_ana_timestamp(raw_ts),
                        "station_id": str(item.get("codigoestacao") or code),
                        "level_m": round(cota_cm / 100.0, 4) if cota_cm is not None else None,
                        "flow_m3s": _parse_float(item.get("Vazao_Adotada")),
                        "rain_mm": _parse_float(item.get("Chuva_Adotada")),
                        "quality_flag": 0,
                    }
                )

    if not records:
        return 0

    upsert_sql = text(
        """
        INSERT INTO hydro_readings (time, station_id, level_m, flow_m3s, rain_mm, quality_flag)
        VALUES (:time, :station_id, :level_m, :flow_m3s, :rain_mm, :quality_flag)
        ON CONFLICT (time, station_id) DO UPDATE SET
            level_m      = COALESCE(EXCLUDED.level_m, hydro_readings.level_m),
            flow_m3s     = COALESCE(EXCLUDED.flow_m3s, hydro_readings.flow_m3s),
            rain_mm      = COALESCE(EXCLUDED.rain_mm, hydro_readings.rain_mm),
            quality_flag = EXCLUDED.quality_flag;
        """
    )

    with engine.begin() as conn:
        conn.execute(upsert_sql, records)

    return len(records)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingestao HidroWebService ANA -> TimescaleDB")
    parser.add_argument("--days-back", type=int, default=1, help="Dias anteriores para buscar")
    args = parser.parse_args()

    total = ingest_ana_stations(days_back=args.days_back)
    print(f"Leituras gravadas em hydro_readings: {total}")