"""Supply and demand zones: base, leg in, leg out, proximal and distal lines.

The method comes from Sam Seiden and Online Trading Academy teaching. There is no
academic definition, so every threshold here is a convention, and they are named and
versioned rather than hidden.

Specification, supply and demand section:

    Base:      a run of m consecutive candles (1 <= m <= 6) each with B_i <= beta x ATR_i
    Leg in:    C_{s-1} - C_{s-1-L_in} >= alpha_in x ATR_{s-1}   (rally in)
               or <= -alpha_in x ATR_{s-1}                       (drop in)
    Leg out:   D+ = (max_{1<=j<=L_out} C_{e+j} - H_base) / ATR_e
               D- = (L_base - min_{1<=j<=L_out} C_{e+j}) / ATR_e
               demand confirms when D+ >= alpha_out, supply when D- >= alpha_out
    Lines:     demand proximal = max over base of max(O_i, C_i), distal = min L_i
               supply proximal = min over base of min(O_i, C_i), distal = max H_i
               reject zones wider than 1.5 x ATR
    Fresh:     N = candles after confirmation whose range reached the proximal line;
               fresh when N = 0
    Invalid:   demand on a close below dist - 0.1 x ATR, supply above dist + 0.1 x ATR
    Score:     S = w1 min(D, D_max) + w2 (1/m) + w3 exp(-lambda N) + w4 c

The zone becomes knowable at the close of the first candle that satisfies the departure
rule and is frozen from then on, so a prefix run gives the same zones as a full run.

The score weights are fitted on training data in Phase 7. Until that fit exists they are
provisional unit weights, and no client-facing setup is produced from them: the score is
reported as a rank with the weights named in `meta`, never as a probability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from engine.foundation import Foundation
from engine.models import Candle

MODULE = "supply_demand"
VERSION = "1.0.0"

DEMAND = "demand"
SUPPLY = "supply"

# formation label table from the specification
_FORMATION = {
    ("rally", "rally"): "RBR",
    ("drop", "rally"): "DBR",
    ("drop", "drop"): "DBD",
    ("rally", "drop"): "RBD",
}


@dataclass
class Zone:
    kind: str                       # demand | supply
    formation: str                  # RBR | DBR | DBD | RBD
    proximal: float
    distal: float
    lower: float
    upper: float
    base_start: int
    base_end: int
    base_candles: int
    known_index: int                # the candle whose close confirmed the zone
    departure_atr: float
    leg_in: str
    touches: list[int] = field(default_factory=list)
    invalidated_index: int | None = None
    score: float = 0.0
    score_parts: dict = field(default_factory=dict)

    @property
    def fresh(self) -> bool:
        return not self.touches

    @property
    def width(self) -> float:
        return self.upper - self.lower

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "formation": self.formation,
            "proximal": self.proximal,
            "distal": self.distal,
            "lower": self.lower,
            "upper": self.upper,
            "base_start": self.base_start,
            "base_end": self.base_end,
            "base_candles": self.base_candles,
            "known_index": self.known_index,
            "departure_atr": self.departure_atr,
            "leg_in": self.leg_in,
            "touches": len(self.touches),
            "fresh": self.fresh,
            "invalidated_index": self.invalidated_index,
            "score": self.score,
            "score_parts": self.score_parts,
        }


def _base_run(candles: Sequence[Candle], foundation: Foundation, end: int, beta: float,
              max_len: int) -> tuple[int, int] | None:
    """The maximal run of small-body candles ending at `end`, capped at `max_len`."""
    atr = foundation.atr_at(end)
    if atr is None or atr <= 0:
        return None
    start = end
    while start - 1 >= 0 and (end - start + 1) < max_len:
        prev_atr = foundation.atr_at(start - 1)
        if prev_atr is None or prev_atr <= 0:
            break
        if abs(candles[start - 1].close - candles[start - 1].open) <= beta * prev_atr:
            start -= 1
        else:
            break
    if abs(candles[end].close - candles[end].open) > beta * atr:
        return None
    return start, end


def _leg_in(candles: Sequence[Candle], foundation: Foundation, base_start: int,
            l_in: int, alpha_in: float) -> str | None:
    """Rally or drop in front of the base, or None when there is no leg in."""
    idx = base_start - 1
    if idx - l_in < 0:
        return None
    atr = foundation.atr_at(idx)
    if atr is None or atr <= 0:
        return None
    move = candles[idx].close - candles[idx - l_in].close
    if move >= alpha_in * atr:
        return "rally"
    if move <= -alpha_in * atr:
        return "drop"
    return None


def score_zone(zone: Zone, foundation: Foundation, index: int, params) -> float:
    """S = w1 min(D, Dmax) + w2 (1/m) + w3 exp(-lambda N) + w4 c, c from the HTF range."""
    sd = params.supply_demand
    weights = sd.score
    range_ = None
    from engine.modules.liquidity import dealing_range
    range_ = dealing_range(foundation, index)
    price = foundation.candles[index].close
    if range_ is None:
        curve = 0.5
    else:
        low, high = range_
        if high <= low:
            curve = 0.5
        elif zone.kind == DEMAND:
            curve = 1.0 - (price - low) / (high - low)
        else:
            curve = (price - low) / (high - low)
    d_clamped = min(zone.departure_atr, float(weights.departure_max))
    n_touches = len(zone.touches)
    parts = {
        "departure": float(weights.w1_departure) * d_clamped,
        "base": float(weights.w2_base) * (1.0 / max(1, zone.base_candles)),
        "freshness": float(weights.w3_freshness) * pow(2.718281828459045,
                                                       -float(weights.freshness_lambda) * n_touches),
        "curve": float(weights.w4_range) * curve,
    }
    zone.score_parts = parts
    zone.score = sum(parts.values())
    return zone.score


def detect_zones(
    candles: Sequence[Candle],
    foundation: Foundation,
    params,
    *,
    stop_index: int | None = None,
) -> list[Zone]:
    """All confirmed zones, discovered online."""
    sd = params.supply_demand
    beta = float(sd.base_body_atr)
    max_base = int(sd.base_max_candles)
    l_in = int(sd.leg_in_candles)
    alpha_in = float(sd.leg_in_atr)
    l_out = int(sd.leg_out_candles)
    alpha_out = float(sd.leg_out_atr)
    max_width = float(sd.max_zone_width_atr)
    invalidation_buffer = float(sd.invalidation_buffer_atr)

    zones: list[Zone] = []
    pending: list[dict] = []

    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        atr = foundation.atr_at(t)

        # 1. a base ending here, with a leg in, opens a pending formation
        if atr is not None and atr > 0:
            run = _base_run(candles, foundation, t, beta, max_base)
            if run is not None:
                leg_in = _leg_in(candles, foundation, run[0], l_in, alpha_in)
                if leg_in is not None:
                    base = candles[run[0]: run[1] + 1]
                    pending.append({
                        "start": run[0], "end": run[1], "leg_in": leg_in,
                        "atr": atr,
                        "high": max(c.high for c in base),
                        "low": min(c.low for c in base),
                        "prox_demand": max(max(c.open, c.close) for c in base),
                        "dist_demand": min(c.low for c in base),
                        "prox_supply": min(min(c.open, c.close) for c in base),
                        "dist_supply": max(c.high for c in base),
                    })

        # 2. evaluate the departure on every pending formation still inside its window
        still_pending: list[dict] = []
        for p in pending:
            if t <= p["end"]:
                still_pending.append(p)
                continue
            if t - p["end"] > l_out:
                continue                     # window closed without a departure
            run_max = max(c.close for c in candles[p["end"] + 1: t + 1])
            run_min = min(c.close for c in candles[p["end"] + 1: t + 1])
            d_plus = (run_max - p["high"]) / p["atr"]
            d_minus = (p["low"] - run_min) / p["atr"]
            confirmed = False
            if d_plus >= alpha_out:
                zone = _make_zone(p, DEMAND, d_plus, "rally", l_out)
                confirmed = True
            elif d_minus >= alpha_out:
                zone = _make_zone(p, SUPPLY, d_minus, "drop", l_out)
                confirmed = True
            if confirmed:
                if zone.width <= max_width * p["atr"]:
                    zone.known_index = t
                    score_zone(zone, foundation, t, params)
                    zones.append(zone)
                still_pending = [q for q in still_pending if q is not p]
                continue
            still_pending.append(p)
        pending = still_pending

        # 3. freshness, invalidation, and re-scoring for existing zones
        for zone in zones:
            if zone.known_index >= t:
                continue
            if zone.kind == DEMAND:
                reached = candle.low <= zone.proximal
                invalid = candle.close < zone.distal - invalidation_buffer * atr if atr else False
            else:
                reached = candle.high >= zone.proximal
                invalid = candle.close > zone.distal + invalidation_buffer * atr if atr else False
            if reached and zone.invalidated_index is None:
                zone.touches.append(t)
                score_zone(zone, foundation, t, params)
            if invalid and zone.invalidated_index is None:
                zone.invalidated_index = t
    return zones


def _make_zone(p: dict, kind: str, departure: float, leg_out: str, l_out: int) -> Zone:
    if kind == DEMAND:
        proximal, distal = p["prox_demand"], p["dist_demand"]
        lower, upper = distal, proximal
    else:
        proximal, distal = p["prox_supply"], p["dist_supply"]
        lower, upper = proximal, distal
    return Zone(
        kind=kind,
        formation=_FORMATION[(p["leg_in"], leg_out)],
        proximal=proximal, distal=distal, lower=lower, upper=upper,
        base_start=p["start"], base_end=p["end"],
        base_candles=p["end"] - p["start"] + 1,
        known_index=p["end"] + l_out,      # replaced with the confirming candle below
        departure_atr=departure, leg_in=p["leg_in"],
    )


def first_touch_candidates(zones: Sequence[Zone], candles: Sequence[Candle]) -> list[dict]:
    """The entry rule's raw material: the first return to a zone while it is still fresh.

    Rule v1 is a limit order at the proximal line on the first return to a fresh zone,
    stop at distal -/+ 0.1 x ATR, target before the nearest opposing zone's proximal
    line. This returns the touch events; turning them into a ranked setup is Phase 7's
    job, because the score weights are not fitted yet.
    """
    out: list[dict] = []
    for zone in zones:
        if zone.touches:
            first = zone.touches[0]
            out.append({
                "zone": zone,
                "entry_index": first,
                "limit_price": zone.proximal,
                "stop_price": zone.distal,
                "fresh": True,
                "known_index": zone.known_index,
            })
    return out