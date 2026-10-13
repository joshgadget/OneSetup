"""Trading costs converted into units of risk (R).

Every cost the spec asks the backtest to model - spread, commission, slippage and, for
perpetuals, funding - is a price distance. Converting to R divides by the entry-to-stop
distance, so a wide stop dilutes costs and a tight stop concentrates them. Nothing here
assumes a position size: the v1 study reports moneyness in R only (DECISIONS.md D-014).
Costs that cannot be resolved from the provider metadata are not invented; they are
reported as notes on the cost model so the result can say what was not modelled.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.models import Direction


@dataclass(frozen=True)
class CostModel:
    spread_price: float = 0.0        # round trip: buy at ask, sell at bid
    slippage_price: float = 0.0      # per side
    commission_price: float = 0.0    # round trip, in price units
    notes: tuple[str, ...] = ()

    def cost_R(self, risk_distance: float) -> float:
        """Spread, slippage and commission for one round trip, in R."""
        if risk_distance <= 0:
            raise ValueError("risk distance must be positive")
        return (self.spread_price + 2.0 * self.slippage_price + self.commission_price) / risk_distance

    @classmethod
    def from_instrument(cls, instrument, params, *, atr: float | None = None) -> "CostModel":
        """Read the provider's own numbers rather than hardcoding broker terms."""
        notes: list[str] = []
        spread = 0.0
        if instrument is not None and instrument.typical_spread:
            spread = float(instrument.typical_spread)
        else:
            notes.append("typical spread unknown for this instrument: spread cost is not modelled")
        slippage = float(params.execution.slippage_atr) * float(atr or 0.0)
        commission_price = 0.0
        per_lot = float(params.execution.commission_per_lot or 0.0)
        if per_lot:
            money_per_price_unit = None
            if (instrument is not None and instrument.contract_size
                    and instrument.value_per_price_unit):
                money_per_price_unit = (float(instrument.contract_size)
                                        * float(instrument.value_per_price_unit))
            if money_per_price_unit:
                commission_price = 2.0 * per_lot / money_per_price_unit
            else:
                notes.append("commission per lot is set but contract size or value per price "
                             "unit is unknown: commission is not modelled")
        return cls(spread_price=spread, slippage_price=slippage,
                   commission_price=commission_price, notes=tuple(notes))


def funding_R(rate_per_day: float, price: float, holding_days: float,
              risk_distance: float, direction: Direction) -> float:
    """Funding paid over the holding period, in R. A positive rate means longs pay shorts."""
    if risk_distance <= 0:
        raise ValueError("risk distance must be positive")
    cost = float(rate_per_day) * float(price) * float(holding_days)
    if direction is Direction.SHORT:
        cost = -cost
    return cost / risk_distance
