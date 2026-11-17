from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class EbapScenario(str, Enum):
    NORMAL = "all_operational"      # 100% das casas de bombas ativas
    PARTIAL_FAILURE = "partial"     # Falha seletiva (ex.: 4o Distrito / Zona Norte)
    SEVERE_FAILURE = "severe"       # Cenario critico com falha generalizada


class FloodProbabilityForecast(BaseModel):
    timestamp_ref: datetime
    horizon_hours: int = Field(ge=1, le=168, description="Horizonte de previsao em horas")
    guaiba_p10_m: float = Field(description="Cota estimada no percentil 10 (m)")
    guaiba_p50_m: float = Field(description="Cota mediana estimada (m)")
    guaiba_p90_m: float = Field(description="Cota estimada no percentil 90 (m)")
    prob_exceed_alert_3m: float = Field(ge=0.0, le=1.0, description="Probabilidade de superar 3,0 m")


class MicrozoneImpact(BaseModel):
    sector_id: str = Field(description="Codigo do setor censitario ou microzona")
    neighborhood: str = Field(description="Nome oficial do bairro em Porto Alegre")
    scenario: EbapScenario
    prob_depth_gt_30cm: float = Field(ge=0.0, le=1.0)
    expected_depth_m: float = Field(ge=0.0)
    exposed_population: int = Field(ge=0)
    critical_assets_hit: list[str] = Field(default_factory=list)
    impact_score: float = Field(ge=0.0, le=100.0)


class RiskSimulationResponse(BaseModel):
    forecast: FloodProbabilityForecast
    scenario: EbapScenario
    raster_tile_url: str
    top_impacted_microzones: list[MicrozoneImpact]
