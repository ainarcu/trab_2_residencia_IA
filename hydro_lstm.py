"""
Esqueleto base para previsao probabilistica da cota do Guaiba (P10, P50, P90).
Destinado a analise preliminar e integracao com a tabela `guaiba_forecasts`.
"""

from dataclasses import dataclass
import torch
import torch.nn as nn


QUANTILES = (0.10, 0.50, 0.90)


@dataclass(frozen=True)
class ForecastConfig:
    num_features: int = 12      # Ex.: niveis Jacui/Taquari/Cai/Sinos/Gravatai, chuva, vento
    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.2
    horizon_hours: int = 48
    quantiles: tuple[float, ...] = QUANTILES


class PinballLoss(nn.Module):
    """Funcao de perda quantilica: permite que o modelo ajuste a largura de P10-P90."""

    def __init__(self, quantiles: tuple[float, ...] = QUANTILES) -> None:
        super().__init__()
        self.quantiles = quantiles

    def forward(self, preds: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        # preds: (batch, num_quantiles), target: (batch, 1)
        losses = []
        for idx, q in enumerate(self.quantiles):
            errors = target.squeeze(-1) - preds[:, idx]
            losses.append(torch.maximum((q - 1.0) * errors, q * errors))
        return torch.mean(torch.stack(losses, dim=1))


class GuaibaQuantileLSTM(nn.Module):
    """Rede LSTM base que recebe janela historica e preve [P10, P50, P90]."""

    def __init__(self, cfg: ForecastConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.lstm = nn.LSTM(
            input_size=cfg.num_features,
            hidden_size=cfg.hidden_size,
            num_layers=cfg.num_layers,
            dropout=cfg.dropout if cfg.num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(cfg.hidden_size, 32),
            nn.ReLU(),
            nn.Linear(32, len(cfg.quantiles)),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len_horas, num_features)
        out, _ = self.lstm(x)
        last_hidden = out[:, -1, :]
        raw_quantiles = self.head(last_hidden)
        # Garante monotonicidade aproximada na inferencia ordenando P10 <= P50 <= P90
        sorted_q, _ = torch.sort(raw_quantiles, dim=-1)
        return sorted_q


def estimate_exceedance_prob(p10: float, p50: float, p90: float, alert_m: float = 3.0) -> float:
    """Estimativa preliminar de P(cota > alert_m) a partir dos quantis previstos."""
    if alert_m <= p10:
        return 0.95
    if alert_m >= p90:
        return 0.05
    if alert_m <= p50:
        frac = (p50 - alert_m) / max(1e-6, p50 - p10)
        return round(0.50 + 0.40 * frac, 4)
    frac = (alert_m - p50) / max(1e-6, p90 - p50)
    return round(0.50 - 0.40 * frac, 4)


if __name__ == "__main__":
    # Teste de sanidade com tensor sintetico (sem dados reais ainda)
    cfg = ForecastConfig()
    model = GuaibaQuantileLSTM(cfg)
    criterion = PinballLoss(cfg.quantiles)

    x_dummy = torch.randn(8, 72, cfg.num_features)   # 8 janelas de 72h
    y_dummy = torch.full((8, 1), 2.8)                # cota alvo de exemplo

    preds = model(x_dummy)
    loss = criterion(preds, y_dummy)
    p10, p50, p90 = preds[0].tolist()
    print(f"Loss={loss.item():.4f} | Exemplo P10={p10:.2f} P50={p50:.2f} P90={p90:.2f}")
    # TODO (colaborador ML):
    # 1. Carregar series de `hydro_readings` (TimescaleDB) + previsao meteorologica
    # 2. Calibrar banda com Conformal Prediction pos-treino
    # 3. Gravar previsao em `guaiba_forecasts` para consumo do `routes_risk.py`