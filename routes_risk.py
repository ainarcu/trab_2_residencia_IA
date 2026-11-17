from datetime import datetime, timezone
from fastapi import APIRouter, Query

try:
    from src.core.schemas import (
        EbapScenario,
        FloodProbabilityForecast,
        MicrozoneImpact,
        RiskSimulationResponse,
    )
except ImportError:
    from schemas import (
        EbapScenario,
        FloodProbabilityForecast,
        MicrozoneImpact,
        RiskSimulationResponse,
    )

router = APIRouter(prefix="/v1/risk", tags=["risk"])


@router.get("/forecast", response_model=FloodProbabilityForecast)
def get_guaiba_forecast(
    horizon_hours: int = Query(default=48, ge=1, le=168),
) -> FloodProbabilityForecast:
    """Retorna quantis probabilisticos de cota do Guaiba (stub inicial para integracao do LSTM)."""
    return FloodProbabilityForecast(
        timestamp_ref=datetime.now(timezone.utc),
        horizon_hours=horizon_hours,
        guaiba_p10_m=2.65,
        guaiba_p50_m=3.15,
        guaiba_p90_m=3.70,
        prob_exceed_alert_3m=0.62,
    )


@router.get("/simulate", response_model=RiskSimulationResponse)
def simulate_localized_impact(
    horizon_hours: int = Query(default=48, ge=1, le=168),
    ebap_scenario: EbapScenario = Query(default=EbapScenario.NORMAL),
    neighborhood: str | None = Query(default=None, description="Filtrar por bairro (ex.: Menino Deus)"),
) -> RiskSimulationResponse:
    """Simula mancha focalizada e impacto por microzona conforme cenario das EBAPs."""
    forecast = get_guaiba_forecast(horizon_hours=horizon_hours)

    depth_multiplier = {
        EbapScenario.NORMAL: 1.0,
        EbapScenario.PARTIAL_FAILURE: 1.45,
        EbapScenario.SEVERE_FAILURE: 2.1,
    }[ebap_scenario]

    mock_zones = [
        MicrozoneImpact(
            sector_id="431490205000101",
            neighborhood="4o Distrito / Navegantes",
            scenario=ebap_scenario,
            prob_depth_gt_30cm=min(0.99, round(0.45 * depth_multiplier, 2)),
            expected_depth_m=round(0.40 * depth_multiplier, 2),
            exposed_population=1840,
            critical_assets_hit=["Subestacao", "Corredor de Onibus"],
            impact_score=min(100.0, round(42.0 * depth_multiplier, 1)),
        ),
        MicrozoneImpact(
            sector_id="431490205000214",
            neighborhood="Menino Deus",
            scenario=ebap_scenario,
            prob_depth_gt_30cm=min(0.99, round(0.38 * depth_multiplier, 2)),
            expected_depth_m=round(0.32 * depth_multiplier, 2),
            exposed_population=1290,
            critical_assets_hit=["Hospital", "Via Arterial"],
            impact_score=min(100.0, round(39.0 * depth_multiplier, 1)),
        ),
        MicrozoneImpact(
            sector_id="431490205000388",
            neighborhood="Sarandi",
            scenario=ebap_scenario,
            prob_depth_gt_30cm=min(0.99, round(0.52 * depth_multiplier, 2)),
            expected_depth_m=round(0.55 * depth_multiplier, 2),
            exposed_population=2410,
            critical_assets_hit=["Escola Municipal", "UBS"],
            impact_score=min(100.0, round(47.0 * depth_multiplier, 1)),
        ),
    ]

    if neighborhood:
        needle = neighborhood.strip().lower()
        mock_zones = [z for z in mock_zones if needle in z.neighborhood.lower()]

    mock_zones.sort(key=lambda z: z.impact_score, reverse=True)

    return RiskSimulationResponse(
        forecast=forecast,
        scenario=ebap_scenario,
        raster_tile_url=(
            f"http://localhost:8001/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}"
            f"?url=/data/mancha_{ebap_scenario.value}_{horizon_hours}h.tif"
        ),
        top_impacted_microzones=mock_zones,
    )