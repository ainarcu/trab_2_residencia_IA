import json
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Query
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

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


def _get_engine():
    db_url = os.getenv("DATABASE_URL")
    return create_engine(db_url, pool_pre_ping=True) if db_url else None


def _mock_forecast(horizon_hours: int) -> FloodProbabilityForecast:
    return FloodProbabilityForecast(
        timestamp_ref=datetime.now(timezone.utc),
        horizon_hours=horizon_hours,
        guaiba_p10_m=2.65,
        guaiba_p50_m=3.15,
        guaiba_p90_m=3.70,
        prob_exceed_alert_3m=0.62,
    )


def _mock_zones(
    ebap_scenario: EbapScenario,
    neighborhood: str | None = None,
) -> list[MicrozoneImpact]:
    depth_multiplier = {
        EbapScenario.NORMAL: 1.0,
        EbapScenario.PARTIAL_FAILURE: 1.45,
        EbapScenario.SEVERE_FAILURE: 2.1,
    }[ebap_scenario]

    zones = [
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
        zones = [z for z in zones if needle in z.neighborhood.lower()]

    zones.sort(key=lambda z: z.impact_score, reverse=True)
    return zones


@router.get("/forecast", response_model=FloodProbabilityForecast)
def get_guaiba_forecast(
    horizon_hours: int = Query(default=48, ge=1, le=168),
) -> FloodProbabilityForecast:
    """Retorna a previsao probabilistica mais recente do PostGIS, com fallback para mock."""
    engine = _get_engine()
    if engine is not None:
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT timestamp_ref, horizon_hours, guaiba_p10_m,
                               guaiba_p50_m, guaiba_p90_m, prob_exceed_alert_3m
                        FROM guaiba_forecasts
                        WHERE horizon_hours = :horizon_hours
                        ORDER BY timestamp_ref DESC
                        LIMIT 1
                        """
                    ),
                    {"horizon_hours": horizon_hours},
                ).mappings().first()
                if row:
                    return FloodProbabilityForecast(**dict(row))
        except SQLAlchemyError:
            pass

    return _mock_forecast(horizon_hours=horizon_hours)


@router.get("/simulate", response_model=RiskSimulationResponse)
def simulate_localized_impact(
    horizon_hours: int = Query(default=48, ge=1, le=168),
    ebap_scenario: EbapScenario = Query(default=EbapScenario.NORMAL),
    neighborhood: str | None = Query(default=None, description="Filtrar por bairro (ex.: Menino Deus)"),
) -> RiskSimulationResponse:
    """Consulta impactos reais em microzone_impacts ou retorna fallback mock se o banco estiver vazio."""
    forecast = _mock_forecast(horizon_hours=horizon_hours)
    zones: list[MicrozoneImpact] = []
    raster_uri: str | None = None

    engine = _get_engine()
    if engine is not None:
        try:
            with engine.connect() as conn:
                f_row = conn.execute(
                    text(
                        """
                        SELECT forecast_id, timestamp_ref, horizon_hours, guaiba_p10_m,
                               guaiba_p50_m, guaiba_p90_m, prob_exceed_alert_3m
                        FROM guaiba_forecasts
                        WHERE horizon_hours = :horizon_hours
                        ORDER BY timestamp_ref DESC
                        LIMIT 1
                        """
                    ),
                    {"horizon_hours": horizon_hours},
                ).mappings().first()

                if f_row:
                    forecast = FloodProbabilityForecast(
                        timestamp_ref=f_row["timestamp_ref"],
                        horizon_hours=f_row["horizon_hours"],
                        guaiba_p10_m=f_row["guaiba_p10_m"],
                        guaiba_p50_m=f_row["guaiba_p50_m"],
                        guaiba_p90_m=f_row["guaiba_p90_m"],
                        prob_exceed_alert_3m=f_row["prob_exceed_alert_3m"],
                    )

                    impact_rows = conn.execute(
                        text(
                            """
                            SELECT m.sector_id, m.neighborhood, m.critical_assets,
                                   i.scenario, i.prob_depth_gt_30cm, i.expected_depth_m,
                                   i.exposed_population, i.impact_score, i.raster_uri
                            FROM microzone_impacts i
                            JOIN microzones m ON m.sector_id = i.sector_id
                            WHERE i.forecast_id = :forecast_id
                              AND i.scenario = :scenario
                              AND (:neighborhood IS NULL OR m.neighborhood ILIKE :neighborhood_like)
                            ORDER BY i.impact_score DESC
                            LIMIT 50
                            """
                        ),
                        {
                            "forecast_id": f_row["forecast_id"],
                            "scenario": ebap_scenario.value,
                            "neighborhood": neighborhood,
                            "neighborhood_like": f"%{neighborhood.strip()}%" if neighborhood else None,
                        },
                    ).mappings().all()

                    for r in impact_rows:
                        assets = r["critical_assets"]
                        if isinstance(assets, str):
                            assets = json.loads(assets)
                        raster_uri = raster_uri or r["raster_uri"]
                        zones.append(
                            MicrozoneImpact(
                                sector_id=r["sector_id"],
                                neighborhood=r["neighborhood"],
                                scenario=EbapScenario(r["scenario"]),
                                prob_depth_gt_30cm=r["prob_depth_gt_30cm"],
                                expected_depth_m=r["expected_depth_m"],
                                exposed_population=r["exposed_population"],
                                critical_assets_hit=list(assets or []),
                                impact_score=r["impact_score"],
                            )
                        )
        except SQLAlchemyError:
            pass

    if not zones:
        zones = _mock_zones(ebap_scenario=ebap_scenario, neighborhood=neighborhood)

    raster_path = raster_uri or f"/data/mancha_{ebap_scenario.value}_{horizon_hours}h.tif"

    return RiskSimulationResponse(
        forecast=forecast,
        scenario=ebap_scenario,
        raster_tile_url=(
            f"http://localhost:8001/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}"
            f"?url={raster_path}"
        ),
        top_impacted_microzones=zones,
    )