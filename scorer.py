from dataclasses import dataclass

ASSET_WEIGHTS: dict[str, float] = {
    "Hospital": 15.0,
    "Subestacao": 12.0,
    "UBS": 10.0,
    "Escola Municipal": 8.0,
    "Via Arterial": 6.0,
    "Corredor de Onibus": 5.0,
}


@dataclass(frozen=True)
class ImpactResult:
    prob_depth_gt_30cm: float
    expected_depth_m: float
    exposed_population: int
    impact_score: float


def compute_microzone_impact(
    expected_depth_m: float,
    fraction_gt_30cm: float,
    population: int,
    cadunico_families: int,
    critical_assets: list[str],
) -> ImpactResult:
    """Calcula populacao exposta e score focalizado (0-100) para uma microzona."""
    depth = max(0.0, float(expected_depth_m))
    frac_30 = min(1.0, max(0.0, float(fraction_gt_30cm)))

    # Populacao exposta proporcional a fracao da microzona acima de 30 cm
    exposed_pop = int(round(max(0, population) * frac_30))

    # 1. Severidade fisica da lamina d'agua (ate 45 pts; satura em 2,0 m)
    depth_component = min(1.0, depth / 2.0) * 45.0

    # 2. Vulnerabilidade social: peso extra para familias no CadUnico (ate 35 pts)
    vuln_ratio = min(1.0, (cadunico_families * 3.0) / max(1, population))
    social_component = frac_30 * (20.0 + 15.0 * vuln_ratio)

    # 3. Infraestrutura critica atingida quando ha risco relevante (ate 20 pts)
    raw_asset_weight = sum(ASSET_WEIGHTS.get(a, 4.0) for a in critical_assets)
    asset_component = min(20.0, raw_asset_weight) * (1.0 if frac_30 >= 0.15 else frac_30 / 0.15)

    score = min(100.0, round(depth_component + social_component + asset_component, 1))

    return ImpactResult(
        prob_depth_gt_30cm=round(frac_30, 4),
        expected_depth_m=round(depth, 3),
        exposed_population=exposed_pop,
        impact_score=score,
    )
