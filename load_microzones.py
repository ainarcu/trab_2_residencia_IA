import json
import os
import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import create_engine, text


def load_microzones_from_file(
    vector_path: str,
    sector_col: str = "CD_SETOR",
    neighborhood_col: str = "NM_BAIRRO",
    population_col: str = "POP",
    cadunico_col: str | None = None,
    database_url: str | None = None,
) -> int:
    """Carrega shapefile/GeoJSON de setores censitarios para a tabela microzones no PostGIS."""
    db_url = database_url or os.environ["DATABASE_URL"]
    engine = create_engine(db_url)

    gdf = gpd.read_file(vector_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    records: list[dict] = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if isinstance(geom, Polygon):
            geom = MultiPolygon([geom])
        if geom is None or geom.is_empty:
            continue

        records.append(
            {
                "sector_id": str(row[sector_col]),
                "neighborhood": str(row.get(neighborhood_col) or "Nao informado"),
                "population": int(row.get(population_col) or 0),
                "cadunico_families": int(row.get(cadunico_col) or 0) if cadunico_col else 0,
                "critical_assets": json.dumps([]),
                "geom_wkt": geom.wkt,
            }
        )

    if not records:
        return 0

    upsert_sql = text(
        """
        INSERT INTO microzones (
            sector_id, neighborhood, population, cadunico_families, critical_assets, geom
        )
        VALUES (
            :sector_id, :neighborhood, :population, :cadunico_families,
            CAST(:critical_assets AS jsonb),
            ST_GeomFromText(:geom_wkt, 4326)
        )
        ON CONFLICT (sector_id) DO UPDATE SET
            neighborhood      = EXCLUDED.neighborhood,
            population        = EXCLUDED.population,
            cadunico_families = EXCLUDED.cadunico_families,
            geom              = EXCLUDED.geom;
        """
    )

    with engine.begin() as conn:
        conn.execute(upsert_sql, records)

    return len(records)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Carga inicial de microzonas no PostGIS")
    parser.add_argument("--file", required=True, help="Caminho do Shapefile ou GeoJSON")
    parser.add_argument("--sector-col", default="CD_SETOR")
    parser.add_argument("--neighborhood-col", default="NM_BAIRRO")
    parser.add_argument("--population-col", default="POP")
    parser.add_argument("--cadunico-col", default=None)
    args = parser.parse_args()

    total = load_microzones_from_file(
        vector_path=args.file,
        sector_col=args.sector_col,
        neighborhood_col=args.neighborhood_col,
        population_col=args.population_col,
        cadunico_col=args.cadunico_col,
    )
    print(f"Microzonas carregadas no PostGIS: {total}")
