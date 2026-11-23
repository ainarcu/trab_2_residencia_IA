import json
import os
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from sqlalchemy import create_engine, text

try:
    from src.impact.scorer import compute_microzone_impact
except ImportError:
    from scorer import compute_microzone_impact


def run_spatial_impact_join(
    raster_path: str,
    forecast_id: int,
    scenario: str,
    database_url: str | None = None,
) -> int:
    """Cruza um GeoTIFF de profundidade com microzones no PostGIS e grava microzone_impacts."""
    db_url = database_url or os.environ["DATABASE_URL"]
    engine = create_engine(db_url)

    microzones = gpd.read_postgis(
        """
        SELECT sector_id, population, cadunico_families, critical_assets, geom
        FROM microzones
        """,
        con=engine,
        geom_col="geom",
    )

    if microzones.empty:
        return 0

    rows_to_upsert: list[dict] = []

    with rasterio.open(raster_path) as src:
        if microzones.crs != src.crs:
            microzones = microzones.to_crs(src.crs)

        nodata = src.nodata

        for _, row in microzones.iterrows():
            try:
                out_image, _ = mask(src, [row.geom], crop=True, filled=False)
            except ValueError:
                # Poligono fora da extensao do raster
                continue

            band = out_image[0]
            if np.ma.isMaskedArray(band):
                valid = band.compressed()
            else:
                valid = band[band != nodata] if nodata is not None else band.ravel()

            valid = valid[np.isfinite(valid) & (valid >= 0.0)]
            if valid.size == 0:
                continue

            expected_depth = float(np.mean(valid))
            fraction_gt_30cm = float(np.mean(valid > 0.30))

            assets = row["critical_assets"]
            if isinstance(assets, str):
                assets = json.loads(assets)

            impact = compute_microzone_impact(
                expected_depth_m=expected_depth,
                fraction_gt_30cm=fraction_gt_30cm,
                population=int(row["population"]),
                cadunico_families=int(row["cadunico_families"]),
                critical_assets=list(assets or []),
            )

            rows_to_upsert.append(
                {
                    "forecast_id": forecast_id,
                    "sector_id": row["sector_id"],
                    "scenario": scenario,
                    "prob_depth_gt_30cm": impact.prob_depth_gt_30cm,
                    "expected_depth_m": impact.expected_depth_m,
                    "exposed_population": impact.exposed_population,
                    "impact_score": impact.impact_score,
                    "raster_uri": raster_path,
                }
            )

    if not rows_to_upsert:
        return 0

    upsert_sql = text(
        """
        INSERT INTO microzone_impacts (
            forecast_id, sector_id, scenario,
            prob_depth_gt_30cm, expected_depth_m,
            exposed_population, impact_score, raster_uri
        )
        VALUES (
            :forecast_id, :sector_id, :scenario,
            :prob_depth_gt_30cm, :expected_depth_m,
            :exposed_population, :impact_score, :raster_uri
        )
        ON CONFLICT (forecast_id, sector_id, scenario)
        DO UPDATE SET
            prob_depth_gt_30cm = EXCLUDED.prob_depth_gt_30cm,
            expected_depth_m   = EXCLUDED.expected_depth_m,
            exposed_population = EXCLUDED.exposed_population,
            impact_score       = EXCLUDED.impact_score,
            raster_uri         = EXCLUDED.raster_uri,
            computed_at        = NOW();
        """
    )

    with engine.begin() as conn:
        conn.execute(upsert_sql, rows_to_upsert)

    return len(rows_to_upsert)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cruzamento espacial GeoTIFF x PostGIS")
    parser.add_argument("--raster", required=True, help="Caminho para o GeoTIFF de profundidade")
    parser.add_argument("--forecast-id", type=int, required=True)
    parser.add_argument(
        "--scenario",
        choices=["all_operational", "partial", "severe"],
        default="all_operational",
    )
    args = parser.parse_args()

    updated = run_spatial_impact_join(
        raster_path=args.raster,
        forecast_id=args.forecast_id,
        scenario=args.scenario,
    )
    print(f"Microzonas atualizadas em microzone_impacts: {updated}")