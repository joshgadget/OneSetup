"""One candidate builder per module, all returning the shared signal record.

Two of these are readings of ours rather than published rules, and both are recorded in
DECISIONS.md D-014:

* liquidity has no entry rule in the spec. The spec defines pools, grabs and target
  selection, and says the module feeds SMC and SLP. Two standalone readings are built
  and both are reported, because the first one was invented first and the second one was
  added after it failed; counting variants is part of the protocol:

      liquidity            enter at the close of the reclaiming candle, stop beyond the
                           swept extreme minus/plus 0.1 x ATR, target the nearest active
                           opposing pool. This chases the reclaim bar.
      liquidity_retrace    the same stop and target, but the entry is a limit at the
                           swept pool level, so the trade waits for the retrace.
* supply and demand does have an entry rule v1 in the spec: a limit at the proximal
  line on the first return to a fresh zone, stop at the distal line minus/plus
  0.1 x ATR, target the nearest opposing zone's proximal line. It is implemented here
  because the module file stops at the touch events and leaves the trade to Phase 7.

Everything else delegates to the module that owns the rule. Nothing here reads a candle
after the index it stamps as known_index.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.config import load_params
from engine.foundation import Foundation, compute_foundation
from engine.models import Candle, Direction
from engine.modules.common import make_signal
from engine.modules import crt as crt_module
from engine.modules import ict as ict_module
from engine.modules import liquidity as liquidity_module
from engine.modules import msnr as msnr_module
from engine.modules import slp as slp_module
from engine.modules import smc as smc_module
from engine.modules import supply_demand as sd_module
from engine.modules import support_resistance as sr_module
from engine.modules.liquidity import Side, build_pools, detect_grabs, nearest_pool


@dataclass
class SeriesContext:
    """Everything the builders share: candles, foundation, pools, grabs and neighbours."""

    candles: list[Candle]
    params: object = None
    htf_candles: list[Candle] | None = None
    htf_foundation: Foundation | None = None
    reference_candles: list[Candle] | None = None
    reference_foundation: Foundation | None = None
    tick_size: float | None = None
    k: int | None = None
    foundation: Foundation = field(init=False, default=None)
    pools: list = field(init=False, default_factory=list)
    grabs: list = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.params = self.params or load_params()
        k = self.k
        if k is None:
            tf = self.candles[0].timeframe.value
            k = int(self.params.foundation.pivot.k_by_timeframe.get(
                tf, self.params.foundation.pivot.k_default))
        self.k = k
        self.foundation = compute_foundation(self.candles, k=k)
        self.pools = build_pools(self.candles, self.foundation, self.params)
        self.grabs = detect_grabs(self.candles, self.foundation, self.pools, self.params)
        if self.htf_candles is not None and self.htf_foundation is None:
            self.htf_foundation = compute_foundation(self.htf_candles, k=k)
        if self.reference_candles is not None and self.reference_foundation is None:
            self.reference_foundation = compute_foundation(self.reference_candles, k=k)


def support_resistance_signals(ctx: SeriesContext) -> list:
    _, signals = sr_module.discover_levels(ctx.candles, ctx.foundation, ctx.params,
                                           collect_signals=True)
    return list(signals)


def _liquidity_candidates(ctx: SeriesContext, entry_mode: str) -> list:
    """Shared body for the two standalone liquidity readings (DECISIONS.md D-014)."""
    buffer = float(ctx.params.liquidity.standalone_stop_buffer_atr)
    out = []
    for grab in ctx.grabs:
        index = grab.known_index
        candle = ctx.candles[index]
        atr = ctx.foundation.atr_at(index)
        if not atr or atr <= 0:
            continue
        long = grab.side is Side.SELL
        if entry_mode == "reclaim_close":
            entry = candle.close
        elif entry_mode == "pool_retrace":
            entry = grab.pool_level
        else:
            raise ValueError(f"unknown liquidity entry mode {entry_mode!r}")
        stop = grab.extreme - buffer * atr if long else grab.extreme + buffer * atr
        pool = nearest_pool(ctx.pools, ctx.foundation, index, above=long)
        if pool is None:
            continue
        target = pool.level
        if long and (stop >= entry or target <= entry):
            continue
        if not long and (stop <= entry or target >= entry):
            continue
        out.append(make_signal(
            module="liquidity", version=liquidity_module.VERSION,
            symbol=candle.symbol, timeframe=candle.timeframe,
            direction=Direction.LONG if long else Direction.SHORT,
            entry=entry, stop=stop, target=target,
            candle=candle, known_index=index, candles=ctx.candles,
            formation_index=grab.pierce_index,
            reasons=[
                f"{grab.side.value} grab of the pool at {grab.pool_level:.5f}, "
                f"depth {grab.depth_atr:.2f} ATR, rejection {grab.rejection_ratio:.2f}",
                f"target the nearest active opposing pool at {target:.5f}",
            ],
            invalidation=[
                f"a close beyond the swept extreme {grab.extreme:.5f} voids the read",
            ],
            features={"grab_depth_atr": grab.depth_atr, "grab_rejection": grab.rejection_ratio,
                      "pool_size": float(grab.pool_size)},
            meta={"grab": grab.to_dict(), "pool": pool.to_dict(),
                  "entry_mode": entry_mode},
        ))
    return out


def liquidity_signals(ctx: SeriesContext) -> list:
    """First standalone reading: enter at the close of the reclaiming candle."""
    return _liquidity_candidates(ctx, "reclaim_close")


def liquidity_retrace_signals(ctx: SeriesContext) -> list:
    """Second standalone reading: wait for the retrace to the swept pool level."""
    return _liquidity_candidates(ctx, "pool_retrace")


def supply_demand_signals(ctx: SeriesContext) -> list:
    """The spec's entry rule v1 for supply and demand zones."""
    zones = sd_module.detect_zones(ctx.candles, ctx.foundation, ctx.params)
    buffer = float(ctx.params.supply_demand.stop_buffer_atr)
    out = []
    for zone in zones:
        if not zone.touches:
            continue
        touch = int(zone.touches[0])
        if zone.invalidated_index is not None and zone.invalidated_index <= touch:
            continue
        atr = ctx.foundation.atr_at(touch)
        if not atr or atr <= 0:
            continue
        long = zone.kind == "demand"
        entry = zone.proximal
        stop = zone.distal - buffer * atr if long else zone.distal + buffer * atr
        target = None
        for other in zones:
            if other.known_index > touch:
                continue        # not knowable at the touch candle
            if other.invalidated_index is not None and other.invalidated_index <= touch:
                continue
            if long and other.kind == "supply" and other.proximal > entry:
                target = other.proximal if target is None else min(target, other.proximal)
            if not long and other.kind == "demand" and other.proximal < entry:
                target = other.proximal if target is None else max(target, other.proximal)
        if target is None:
            continue
        candle = ctx.candles[touch]
        out.append(make_signal(
            module="supply_demand", version=sd_module.VERSION,
            symbol=candle.symbol, timeframe=candle.timeframe,
            direction=Direction.LONG if long else Direction.SHORT,
            entry=entry, stop=stop, target=target,
            candle=candle, known_index=touch, candles=ctx.candles,
            formation_index=zone.base_start,
            reasons=[
                f"first return to a fresh {zone.kind} zone, formation {zone.formation}",
                f"limit at the proximal line {entry:.5f}, stop at the distal line "
                f"minus/plus {buffer:.2f} ATR",
                f"target the nearest opposing zone's proximal line {target:.5f}",
            ],
            invalidation=[
                f"a close beyond the distal line {zone.distal:.5f} minus/plus "
                f"0.1 ATR invalidates the zone",
            ],
            features={"zone_score": float(zone.score),
                      "departure_atr": float(zone.departure_atr),
                      "zone_width_atr": float(zone.width / atr)},
            meta={"zone": zone.to_dict()},
        ))
    return out


def smc_signals(ctx: SeriesContext) -> list:
    return smc_module.detect_entry_model_v1(
        ctx.candles, ctx.foundation, ctx.params, pools=ctx.pools, grabs=ctx.grabs)


def ict_signals(ctx: SeriesContext) -> list:
    return ict_module.detect_entry_model_v1(
        ctx.candles, ctx.foundation, ctx.params, pools=ctx.pools, grabs=ctx.grabs)


def crt_signals(ctx: SeriesContext) -> list:
    return crt_module.detect_crt_signals(
        ctx.candles, ctx.foundation, ctx.params, reference_candles=ctx.reference_candles)


def msnr_signals(ctx: SeriesContext) -> list:
    return msnr_module.detect_msnr_setup_v1(
        ctx.candles, ctx.foundation, ctx.params, tick_size=ctx.tick_size)


def slp_signals(ctx: SeriesContext) -> list:
    zones = sd_module.detect_zones(ctx.candles, ctx.foundation, ctx.params)
    levels, _ = sr_module.discover_levels(ctx.candles, ctx.foundation, ctx.params,
                                          collect_signals=False)
    msnr_levels = msnr_module.detect_levels(ctx.candles, ctx.foundation, ctx.params,
                                            tick_size=ctx.tick_size)
    return slp_module.detect_slp(
        ctx.candles, ctx.foundation, ctx.params,
        htf_candles=ctx.htf_candles or ctx.candles,
        htf_foundation=ctx.htf_foundation,
        pools=ctx.pools, grabs=ctx.grabs,
        sd_zones=zones, sr_levels=levels, msnr_levels=msnr_levels)


CANDIDATE_BUILDERS = {
    "support_resistance": support_resistance_signals,
    "liquidity": liquidity_signals,
    "liquidity_retrace": liquidity_retrace_signals,
    "supply_demand": supply_demand_signals,
    "smc": smc_signals,
    "ict": ict_signals,
    "crt": crt_signals,
    "msnr": msnr_signals,
    "slp": slp_signals,
}

MODULE_ORDER = tuple(CANDIDATE_BUILDERS)
