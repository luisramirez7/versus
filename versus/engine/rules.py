from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PRESETS: dict[str, int] = {"day": 1, "week": 5, "month": 21, "quarter": 63}
PRESET_LABELS: dict[str, str] = {"day": "Day Sprint", "week": "Week", "month": "Month", "quarter": "Quarter"}
CASH_OPTIONS: tuple[int, ...] = (1_000, 10_000, 100_000)


@dataclass(frozen=True)
class Rules:
    starting_cash: Decimal = Decimal(1_000)
    preset: str = "day"
    spread_bps: int = 5
    min_price: Decimal = Decimal(5)
    min_market_cap: int = 300_000_000
    min_avg_volume: int = 500_000
    adv_cap_pct: Decimal = Decimal("0.02")
    min_notional: Decimal = Decimal(1)
    fill_delay_s: float = 1.0
    fill_timeout_s: float = 30.0

    @property
    def sessions(self) -> int:
        return PRESETS[self.preset]

    @property
    def preset_label(self) -> str:
        return PRESET_LABELS[self.preset]

    def half_spread(self) -> Decimal:
        return Decimal(self.spread_bps) / Decimal(20_000)  # bps → fraction, halved
