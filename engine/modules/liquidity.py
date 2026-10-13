"""Liquidity: pools, grabs (sweeps) and liquidity structure.

The mechanism has research support (Osler 2001, 2003: stop-loss and take-profit orders
cluster at round numbers and stop-loss orders intensify a move once a level is crossed).
The labels pool, grab and sweep are practitioner terms: no study shows anyone
deliberately hunting those orders. A pool below is a proxy for where orders may sit, not
a measurement of them.

Specification, liquidity section:

    Pool:      |h_i - h_j| <= delta, delta = 0.1 x ATR, single-linkage clustering.
               Buy-side pool level = highest high in the cluster (buy stops rest above it);
               sell-side pool level = lowest low. An unclustered swing is a pool with n = 1.
               Stored per pool: level, number of swings n, formation time, last touch, state.
    Grab:      buy-side: H_t > P + eps and C_t < P;   sell-side: L_t < P - eps and C_t > P
               with eps = 0.05 x ATR. The loose variant allows the reclaim within r = 3
               candles, and is knowable at the close of the reclaiming candle, not at t.
               A candle that closes beyond P and keeps going is a breakout, not a sweep.
    Quality:   depth = (H_t - P)/ATR_t, rejection = (P - C_t)/R_t, pool size = n.
    Structure: external if P >= H_r or P <= L_r, internal if L_r < P < H_r, with the
               latest confirmed swing high/low as the dealing range.
    Target:    nearest active pool above (long) or below (short) by d(P,t) = |P - C|/ATR.

Every pool and grab is created at a candle whose index is at or before the data used, so
a prefix run gives the same answer; tests/test_liquidity.py asserts that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from engine.foundation import Foundation
from engine.models import Candle

MODULE = "liquidity"
VERSION = "1.0.0"


class Side(str, Enum):
    BUY = "buy_side"      # resting above price: stops of shorts, breakouts
    SELL = "sell_side"    # resting below price: stops of longs


@dataclass
class Pool:
    side: Side
    level: float
    delta: float
    formed_index: int
    known_index: int
    swing_indices: list[int] = field(default_factory=list)
    state: str = "active"                 # active | swept | invalidated
    swept_index: int | None = None
    invalidated_index: int | None = None
    last_touch_index: int | None = None
    broken_index: int | None = None       # first candle that ended the active state

    @property
    def size(self) -> int:
        return len(self.swing_indices)

    def to_dict(self) -> dict:
        return {
            "side": self.side.value,
            "level": self.level,
            "size": self.size,
            "formed_index": self.formed_index,
            "known_index": self.known_index,
            "state": self.state,
            "swept_index": self.swept_index,
            "inv_marker": self.invalidated_index,
            "last_touch_index": self.last_touch_index,
            "broken_index": self.broken_index,
        }


@dataclass(frozen=True)
class Grab:
    """A sweep: price pierced a pool and closed back inside the range."""

    side: Side
    pool_level: float
    pierce_index: int
    known_index: int
    variant: str                 # one_candle | reclaim_within_r
    depth_atr: float
    rejection_ratio: float
    pool_size: int
    extreme: float               # the wick that swept
    pool_active: bool = True     # the pool had not already been broken at the pierce

    def to_dict(self) -> dict:
        return {
            "side": self.side.value,
            "pool_level": self.pool_level,
            "pierce_index": self.pierce_index,
            "known_index": self.known_index,
            "variant": self.variant,
            "depth_atr": self.depth_atr,
            "rejection_ratio": self.rejection_ratio,
            "pool_size": self.pool_size,
            "extreme": self.extreme,
            "pool_active": self.pool_active,
        }


# ------------------------------------------------------------------ pools

def _cluster(current: list[Pool], side: Side, level: float, index: int, delta: float,
             known_index: int) -> Pool | None:
    """Single-linkage: join an existing pool when the new swing is within delta of it."""
    for pool in current:
        if pool.side is not side or pool.state != "active":
            continue
        if abs(pool.level - level) <= delta:
            return pool
    return None


def build_pools(
    candles: Sequence[Candle],
    foundation: Foundation,
    params,
    *,
    stop_index: int | None = None,
) -> list[Pool]:
    """Create pools from confirmed swings, online, with grab state applied."""
    liq = params.liquidity
    cluster_atr = float(liq.pool_cluster_atr)
    grab_buffer = float(liq.grab_buffer_atr)
    breakout_buffer = float(liq.breakout_confirm_atr)
    reclaim_r = int(liq.grab_reclaim_candles)

    pools: list[Pool] = []
    pivots_by_known: dict[int, list] = {}
    for pv in foundation.pivots:
        pivots_by_known.setdefault(pv.known_index, []).append(pv)

    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        atr = foundation.atr_at(t)
        delta = cluster_atr * atr if atr else None

        # 1. new confirmed swings extend or open pools
        if delta is not None and delta > 0:
            for pv in pivots_by_known.get(t, []):
                side = Side.BUY if pv.kind == "high" else Side.SELL
                pool = _cluster(pools, side, pv.price, t, delta, pv.known_index)
                if pool is None:
                    pools.append(Pool(
                        side=side, level=pv.price, delta=delta, formed_index=pv.index,
                        known_index=pv.known_index, swing_indices=[pv.index],
                    ))
                    continue
                pool.swing_indices.append(pv.index)
                pool.formed_index = min(pool.formed_index, pv.index)
                pool.known_index = max(pool.known_index, pv.known_index)
                pool.level = max(pool.level, pv.price) if side is Side.BUY else min(pool.level, pv.price)

        # 2. state: swept, invalidated, or touched
        if atr is None:
            continue
        eps = grab_buffer * atr
        for pool in pools:
            if pool.state != "active" or pool.known_index > t:
                continue
            if pool.side is Side.BUY:
                if candle.high > pool.level:
                    pool.last_touch_index = t
                if candle.high > pool.level + eps and candle.close < pool.level:
                    pool.state, pool.swept_index = "swept", t
                    if pool.broken_index is None:
                        pool.broken_index = t
                elif candle.close > pool.level + breakout_buffer * atr:
                    pool.state, pool.invalidated_index = "invalidated", t
                    if pool.broken_index is None:
                        pool.broken_index = t
            else:
                if candle.low < pool.level:
                    pool.last_touch_index = t
                if candle.low < pool.level - eps and candle.close > pool.level:
                    pool.state, pool.swept_index = "swept", t
                    if pool.broken_index is None:
                        pool.broken_index = t
                elif candle.close < pool.level - breakout_buffer * atr:
                    pool.state, pool.invalidated_index = "invalidated", t
                    if pool.broken_index is None:
                        pool.broken_index = t
    return pools


# ------------------------------------------------------------------ grabs

def detect_grabs(
    candles: Sequence[Candle],
    foundation: Foundation,
    pools: Sequence[Pool],
    params,
    *,
    stop_index: int | None = None,
) -> list[Grab]:
    """Sweeps, including the loose reclaim variant.

    This is a second pass over the same pools so a grab can be measured against pools
    that were known at the time. Every grab records the index of the candle that made it
    knowable: the piercing candle for the one-candle variant, the reclaiming candle for
    the loose one.
    """
    liq = params.liquidity
    eps_atr = float(liq.grab_buffer_atr)
    reclaim_r = int(liq.grab_reclaim_candles)
    grabs: list[Grab] = []

    for pool in pools:
        start = pool.known_index + 1
        end = len(candles) - 1 if stop_index is None else stop_index
        i = start
        while i <= end:
            candle = candles[i]
            atr = foundation.atr_at(i)
            if atr is None or atr <= 0:
                i += 1
                continue
            eps = eps_atr * atr
            if pool.side is Side.BUY:
                pierced = candle.high > pool.level + eps
                one_candle = pierced and candle.close < pool.level
                depth = (candle.high - pool.level) / atr
                rejection = (pool.level - candle.close) / max(candle.high - candle.low, 1e-12)
            else:
                pierced = candle.low < pool.level - eps
                one_candle = pierced and candle.close > pool.level
                depth = (pool.level - candle.low) / atr
                rejection = (candle.close - pool.level) / max(candle.high - candle.low, 1e-12)
            active = pool.broken_index is None or pool.broken_index >= i
            if one_candle:
                grabs.append(Grab(pool.side, pool.level, i, i, "one_candle", depth, rejection,
                                  pool.size, candle.high if pool.side is Side.BUY else candle.low,
                                  active))
                i += 1
                continue
            if pierced:
                # loose variant: a close back on the original side within r candles
                for j in range(i, min(i + reclaim_r, end) + 1):
                    c2 = candles[j]
                    reclaimed = c2.close < pool.level if pool.side is Side.BUY else c2.close > pool.level
                    if reclaimed:
                        grabs.append(Grab(pool.side, pool.level, i, j, "reclaim_within_r", depth,
                                          rejection, pool.size,
                                          candle.high if pool.side is Side.BUY else candle.low,
                                          active))
                        i = j + 1
                        break
                else:
                    i += 1
                continue
            i += 1
    return grabs


# ------------------------------------------------------- structure and targets

def dealing_range(foundation: Foundation, index: int) -> tuple[float, float] | None:
    """Latest confirmed swing high and low knowable at `index`."""
    from engine.foundation import last_pivot
    high = last_pivot(foundation.pivots, "high", index)
    low = last_pivot(foundation.pivots, "low", index)
    if high is None or low is None or high.price <= low.price:
        return None
    return low.price, high.price


def position_of(level: float, rng: tuple[float, float] | None) -> str:
    """external | internal | unknown for a price against the dealing range."""
    if rng is None:
        return "unknown"
    low, high = rng
    if level >= high or level <= low:
        return "external"
    return "internal"


def pool_position(pool: Pool, rng: tuple[float, float] | None) -> str:
    """external | internal | unknown, against the dealing range."""
    return position_of(pool.level, rng)


def active_pools(pools: Sequence[Pool], index: int, *, include_swept: bool = False) -> list[Pool]:
    out = []
    for pool in pools:
        if pool.known_index > index:
            continue
        if pool.state == "active" or (include_swept and pool.state == "swept"):
            out.append(pool)
    return out


def nearest_pool(
    pools: Sequence[Pool],
    foundation: Foundation,
    index: int,
    *,
    above: bool,
    include_swept: bool = False,
) -> Pool | None:
    """P* = argmin d(P,t) over active pools strictly above (or below) price."""
    close = foundation.candles[index].close
    candidates = [
        pool for pool in active_pools(pools, index, include_swept=include_swept)
        if (pool.level > close if above else pool.level < close)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda p: foundation.distance_in_atr(p.level, index) or float("inf"))


def liquidity_structure(pools: Sequence[Pool], foundation: Foundation, index: int) -> dict:
    """The state of all pools relative to the dealing range at `index`."""
    rng = dealing_range(foundation, index)
    rows = []
    for pool in active_pools(pools, index, include_swept=True):
        rows.append({**pool.to_dict(), "position": pool_position(pool, rng)})
    external = [r for r in rows if r["position"] == "external"]
    internal = [r for r in rows if r["position"] == "internal"]
    return {
        "range": {"low": rng[0], "high": rng[1]} if rng else None,
        "external": external,
        "internal": internal,
        "external_count": len(external),
        "internal_count": len(internal),
    }