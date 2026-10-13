"""SMC: Smart Money Concepts, one reproducible reading of a practitioner vocabulary.

SMC has no published formal definition and educators disagree on terms, so nothing here
is described as a proven or standard method. The spec fixes one reading; this file
implements that reading with every threshold as a named parameter.

Specification, SMC section:

    BOS+:     trend = bull and C_t > P^H + eps, eps = 0.05 x ATR. P^H is the latest
              confirmed swing high known before t. P^L mirrors. A wick-through version
              would be a separate variant and is not mixed in here.
    CHoCH-:   trend = bull and C_t < P^L_protected - eps, where the protected low is the
              swing low that formed immediately before the latest HH. Trend flips to
              none until new pivots confirm. The mirror gives CHoCH+.
    Displacement: C_t > O_t, body >= alpha x ATR_t, (C_t - L_t)/R_t >= q, alpha = 1.0,
              q = 0.75. Bearish uses (H_t - C_t)/R_t. Invalid when R_t = 0.
    MSS:      CHoCH whose breaking candle is also displacement, with the option to
              require a liquidity grab in the previous r = 10 candles. For MSS- the grab
              is a buy-side grab, for MSS+ a sell-side grab.
    FVG:      three candles. Bullish: L_t > H_{t-2}, Z+ = [H_{t-2}, L_t]. Bearish:
              H_t < L_{t-2}, Z- = [H_t, L_{t-2}]. Known at the close of t. Drop widths
              below 0.1 x ATR. Mitigation variants: touch, midpoint, full.
    OB:       bullish candidate: a bullish displacement candle d with C_d above the
              latest confirmed swing high (so it is also a BOS); scan back m = 10 candles
              for the latest bearish candle j; zone [L_j, H_j] or the body only.
              Confirmed at the close of d. The bearish block mirrors this.
    Entry model v1: grab of an external low, then bullish MSS, then a retrace into a
              bullish FVG or OB that formed in the MSS leg. Entry at the zone edge, stop
              below the swept low minus 0.1 x ATR, target the nearest active buy-side
              pool. Order enforced in code, with an expiry in candles.

Reading notes that are ours, not the spec's (DECISIONS.md D-009):
    * "known before t" for P^H and P^L is read strictly: known_index <= t - 1.
    * "the swept low" in the stop rule is the sweep candle's wick extreme, the deepest
      point of the sweep, which also sits at or below the pool level.
    * "formed in the MSS leg" means the zone is confirmed at or after the MSS candle.

OHLC candles cannot show that institutions placed orders in any candle. These detectors
find patterns with those names and nothing more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from engine.config import load_params
from engine.foundation import Foundation, Trend
from engine.models import Candle, Direction, EventStatus, ModuleSignal, Timeframe
from engine.modules.common import make_signal

MODULE = "smc"
VERSION = "1.0.0"

BULL = "bull"
BEAR = "bear"


# ------------------------------------------------------------- structure


@dataclass(frozen=True)
class StructureEvent:
    """A BOS or a CHoCH. Knowable at the close of `index`, never earlier."""

    kind: str            # BOS | CHoCH
    direction: str       # bull | bear
    index: int           # the candle whose close made it knowable
    level: float         # the swing level that was broken
    level_index: int     # formation index of that swing
    trend_before: str

    @property
    def known_index(self) -> int:
        return self.index

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "index": self.index,
            "known_index": self.known_index,
            "level": self.level,
            "level_index": self.level_index,
            "trend_before": self.trend_before,
        }


@dataclass(frozen=True)
class Mss:
    """A market structure shift: CHoCH plus displacement, optionally with a grab."""

    direction: str            # bull | bear
    index: int                # the breaking candle
    level: float
    chch_index: int
    grab_index: int | None
    displacement_body_atr: float
    close_ratio: float

    @property
    def known_index(self) -> int:
        return self.index

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "index": self.index,
            "known_index": self.known_index,
            "level": self.level,
            "chch_index": self.chch_index,
            "grab_index": self.grab_index,
            "displacement_body_atr": self.displacement_body_atr,
            "close_ratio": self.close_ratio,
        }


def displacement(candle: Candle, atr: float, alpha: float, q: float) -> tuple[str, float, float] | None:
    """(direction, body/ATR, close ratio) when the candle is a displacement candle."""
    if atr is None or atr <= 0:
        return None
    rng = candle.high - candle.low
    if rng <= 0:
        return None
    body = abs(candle.close - candle.open)
    if candle.close > candle.open:
        ratio = (candle.close - candle.low) / rng
        if body >= alpha * atr and ratio >= q:
            return BULL, body / atr, ratio
    elif candle.close < candle.open:
        ratio = (candle.high - candle.close) / rng
        if body >= alpha * atr and ratio >= q:
            return BEAR, body / atr, ratio
    return None


def _pivot_known_before(foundation: Foundation, kind: str, t: int):
    """The latest confirmed pivot of `kind` with known_index <= t - 1.

    The spec says P^H and P^L are known *before* t, so a pivot whose confirmation
    window includes candle t itself is not used here.
    """
    candidates = [pv for pv in foundation.pivots if pv.kind == kind and pv.known_index <= t - 1]
    return max(candidates, key=lambda pv: (pv.index, pv.known_index)) if candidates else None


def detect_structure_events(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    stop_index: int | None = None,
) -> list[StructureEvent]:
    """BOS and CHoCH, online, with the post-CHoCH suppression the spec asks for."""
    params = params or load_params()
    eps_atr = float(params.smc.bos_buffer_atr)
    events: list[StructureEvent] = []
    suppressed: set[tuple[str, int]] | None = None

    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        if suppressed is not None:
            made_new = [lb for lb in foundation.labels
                        if lb.known_index <= t and (lb.kind, lb.index) not in suppressed]
            if made_new:
                suppressed = None
        trend = Trend.NONE if suppressed is not None else foundation.trend_at(t)
        atr = foundation.atr_at(t)
        if atr is None or atr <= 0 or trend is Trend.NONE:
            continue
        eps = eps_atr * atr
        if trend is Trend.BULL:
            pivot = _pivot_known_before(foundation, "high", t)
            if pivot is not None and candle.close > pivot.price + eps:
                events.append(StructureEvent("BOS", BULL, t, pivot.price, pivot.index, trend.value))
                continue
            protected = foundation.protected_level(t - 1, trend)
            if protected is not None and candle.close < protected.price - eps:
                events.append(StructureEvent("CHoCH", BEAR, t, protected.price,
                                             protected.pivot.index, trend.value))
                suppressed = {(lb.kind, lb.index) for lb in foundation.labels
                              if lb.known_index <= t}
        else:
            pivot = _pivot_known_before(foundation, "low", t)
            if pivot is not None and candle.close < pivot.price - eps:
                events.append(StructureEvent("BOS", BEAR, t, pivot.price, pivot.index, trend.value))
                continue
            protected = foundation.protected_level(t - 1, trend)
            if protected is not None and candle.close > protected.price + eps:
                events.append(StructureEvent("CHoCH", BULL, t, protected.price,
                                             protected.pivot.index, trend.value))
                suppressed = {(lb.kind, lb.index) for lb in foundation.labels
                              if lb.known_index <= t}
    return events


def detect_mss(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    events: Sequence[StructureEvent] | None = None,
    grabs: Sequence | None = None,
    stop_index: int | None = None,
) -> list[Mss]:
    """MSS = CHoCH candle that is also a displacement candle (optionally after a grab)."""
    params = params or load_params()
    smc = params.smc
    events = list(events) if events is not None else detect_structure_events(
        candles, foundation, params, stop_index=stop_index)
    alpha = float(smc.displacement_body_atr)
    q = float(smc.displacement_close_ratio)
    require_grab = bool(smc.mss_require_liquidity_grab)
    r = int(smc.mss_grab_window_candles)

    out: list[Mss] = []
    for ev in events:
        if ev.kind != "CHoCH":
            continue
        if stop_index is not None and ev.index > stop_index:
            break
        atr = foundation.atr_at(ev.index)
        d = displacement(candles[ev.index], atr, alpha, q)
        if d is None or d[0] != ev.direction:
            continue
        grab_index = None
        if require_grab:
            need = "sell_side" if ev.direction == BULL else "buy_side"
            window = [g for g in (grabs or [])
                      if g.side.value == need and ev.index - r <= g.known_index <= ev.index]
            if not window:
                continue
            grab_index = max(g.known_index for g in window)
        out.append(Mss(ev.direction, ev.index, ev.level, ev.index, grab_index, d[1], d[2]))
    return out


# ------------------------------------------------------------------- FVG


@dataclass
class Fvg:
    direction: str
    lower: float
    upper: float
    index: int                     # the third candle: known at its close
    width_atr: float
    variant: str                   # the configured mitigation variant
    touch_indices: list[int] = field(default_factory=list)
    first_touch_index: int | None = None
    midpoint_index: int | None = None
    full_index: int | None = None
    mitigated_index: int | None = None

    @property
    def known_index(self) -> int:
        return self.index

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def unmitigated(self) -> bool:
        return self.mitigated_index is None and not self.touch_indices

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "lower": self.lower,
            "upper": self.upper,
            "index": self.index,
            "known_index": self.known_index,
            "width_atr": self.width_atr,
            "variant": self.variant,
            "touches": len(self.touch_indices),
            "first_touch_index": self.first_touch_index,
            "midpoint_index": self.midpoint_index,
            "full_index": self.full_index,
            "mitigated_index": self.mitigated_index,
        }


def detect_fvgs(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    stop_index: int | None = None,
) -> list[Fvg]:
    """Three-candle fair value gaps, online, with the three mitigation variants tracked."""
    params = params or load_params()
    smc = params.smc
    min_width = float(smc.fvg_min_width_atr)
    variant = str(smc.fvg_mitigation)
    out: list[Fvg] = []

    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        atr = foundation.atr_at(t)
        if atr is None or atr <= 0:
            continue
        if t >= 2:
            two = candles[t - 2]
            if candle.low > two.high:
                width = candle.low - two.high
                if width >= min_width * atr:
                    out.append(Fvg(BULL, two.high, candle.low, t, width / atr, variant))
            elif candle.high < two.low:
                width = two.low - candle.high
                if width >= min_width * atr:
                    out.append(Fvg(BEAR, candle.high, two.low, t, width / atr, variant))
        for fvg in out:
            if fvg.known_index >= t:
                continue
            touched = (candle.low <= fvg.upper and candle.high >= fvg.lower)
            if touched:
                fvg.touch_indices.append(t)
            if fvg.direction == BULL:
                if fvg.first_touch_index is None and candle.low <= fvg.upper:
                    fvg.first_touch_index = t
                if fvg.midpoint_index is None and candle.low <= fvg.midpoint:
                    fvg.midpoint_index = t
                if fvg.full_index is None and candle.low <= fvg.lower:
                    fvg.full_index = t
            else:
                if fvg.first_touch_index is None and candle.high >= fvg.lower:
                    fvg.first_touch_index = t
                if fvg.midpoint_index is None and candle.high >= fvg.midpoint:
                    fvg.midpoint_index = t
                if fvg.full_index is None and candle.high >= fvg.upper:
                    fvg.full_index = t
            fvg.mitigated_index = {
                "touch": fvg.first_touch_index,
                "midpoint": fvg.midpoint_index,
                "full": fvg.full_index,
            }[fvg.variant]
    return out


# --------------------------------------------------------- order blocks


@dataclass
class OrderBlock:
    direction: str
    lower: float
    upper: float
    index: int                      # the displacement candle d, confirmed at its close
    candle_index: int               # the selected candle j
    variant: str                    # full_range | body
    swing_broken: float
    is_bos: bool
    touch_indices: list[int] = field(default_factory=list)
    invalidated_index: int | None = None

    @property
    def known_index(self) -> int:
        return self.index

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def unmitigated(self) -> bool:
        return not self.touch_indices and self.invalidated_index is None

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "lower": self.lower,
            "upper": self.upper,
            "index": self.index,
            "known_index": self.known_index,
            "candle_index": self.candle_index,
            "variant": self.variant,
            "swing_broken": self.swing_broken,
            "is_bos": self.is_bos,
            "touches": len(self.touch_indices),
            "invalidated_index": self.invalidated_index,
        }


def detect_order_blocks(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    stop_index: int | None = None,
) -> list[OrderBlock]:
    """Single-candle order blocks at displacement candles that are also a BOS."""
    params = params or load_params()
    smc = params.smc
    ob = params.order_block
    alpha = float(smc.displacement_body_atr)
    q = float(smc.displacement_close_ratio)
    eps_atr = float(smc.bos_buffer_atr)
    m = int(ob.lookback_candles)
    variant = str(ob.zone_variant)
    out: list[OrderBlock] = []

    for d, candle in enumerate(candles):
        if stop_index is not None and d > stop_index:
            break
        atr = foundation.atr_at(d)
        if atr is None or atr <= 0:
            continue
        disp = displacement(candle, atr, alpha, q)
        if disp is None:
            continue
        direction, _, _ = disp
        kind = "high" if direction == BULL else "low"
        pivot = _pivot_known_before(foundation, kind, d)
        if pivot is None:
            continue
        eps = eps_atr * atr
        is_bos = (candle.close > pivot.price + eps if direction == BULL
                  else candle.close < pivot.price - eps)
        if not is_bos:
            continue
        start = max(0, d - m)
        found = None
        for j in range(d - 1, start - 1, -1):
            body_bearish = candles[j].close < candles[j].open
            body_bullish = candles[j].close > candles[j].open
            if (direction == BULL and body_bearish) or (direction == BEAR and body_bullish):
                found = j
                break
        if found is None:
            continue
        j = found
        if variant == "body":
            low = min(candles[j].open, candles[j].close)
            high = max(candles[j].open, candles[j].close)
        else:
            low, high = candles[j].low, candles[j].high
        out.append(OrderBlock(direction, low, high, d, j, variant, pivot.price, is_bos))
    return out


# ------------------------------------------------------ entry model v1


def detect_entry_model_v1(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    pools: Sequence | None = None,
    grabs: Sequence | None = None,
    structure_events: Sequence[StructureEvent] | None = None,
    mss_events: Sequence[Mss] | None = None,
    fvgs: Sequence[Fvg] | None = None,
    order_blocks: Sequence[OrderBlock] | None = None,
    stop_index: int | None = None,
) -> list[ModuleSignal]:
    """The SMC entry model v1: external-low grab -> bullish MSS -> retrace into the leg.

    Every step is checked in order, and the returned signal is stamped at the entry
    candle. Long side only: the spec writes the bullish chain and says the bearish side
    mirrors it; the mirror is left off until the long side has backtest evidence.
    """
    params = params or load_params()
    smc = params.smc
    window = int(smc.entry_model_mss_window_candles)
    expiry = int(smc.entry_model_expiry_candles)
    stop_buffer = float(smc.entry_model_stop_buffer_atr)

    from engine.modules.liquidity import Side, dealing_range, nearest_pool, position_of

    grabs = list(grabs) if grabs is not None else []
    pools = list(pools) if pools is not None else []
    mss_events = list(mss_events) if mss_events is not None else detect_mss(
        candles, foundation, params, events=structure_events, grabs=grabs, stop_index=stop_index)
    fvgs = list(fvgs) if fvgs is not None else detect_fvgs(candles, foundation, params, stop_index=stop_index)
    order_blocks = list(order_blocks) if order_blocks is not None else detect_order_blocks(
        candles, foundation, params, stop_index=stop_index)

    signals: list[ModuleSignal] = []
    for mss in mss_events:
        if mss.direction != BULL:
            continue
        t2 = mss.index
        if stop_index is not None and t2 > stop_index:
            break
        candidates = []
        for grab in grabs:
            if grab.side is not Side.SELL or not grab.pool_active:
                continue
            if not (t2 - window <= grab.known_index < t2):
                continue
            rng = dealing_range(foundation, grab.known_index)
            if rng is None or position_of(grab.pool_level, rng) != "external":
                continue
            candidates.append(grab)
        if not candidates:
            continue
        # one setup per MSS: when a run of pools was swept in the chain, the deepest
        # sweep is the one the stop is measured from (DECISIONS.md D-009).
        grab = min(candidates, key=lambda g: g.extreme)
        t0 = grab.known_index
        zones: list[tuple[int, float, float, dict]] = []
        for fvg in fvgs:
            if fvg.direction == BULL and t2 <= fvg.known_index:
                zones.append((fvg.known_index, fvg.lower, fvg.upper, fvg.to_dict()))
        for block in order_blocks:
            if block.direction == BULL and t2 <= block.known_index:
                zones.append((block.known_index, block.lower, block.upper, block.to_dict()))
        if not zones:
            continue
        zones.sort(key=lambda z: z[0])
        entry_index = None
        chosen = None
        for t in range(t2 + 1, min(t2 + expiry, len(candles) - 1) + 1):
            if stop_index is not None and t > stop_index:
                break
            for known, lower, upper, meta in zones:
                if known >= t:
                    continue
                candle = candles[t]
                if candle.low <= upper:
                    entry_index, chosen = t, (lower, upper, meta)
                    break
            if entry_index is not None:
                break
        if entry_index is None or chosen is None:
            continue
        lower, upper, zone_meta = chosen
        atr = foundation.atr_at(entry_index)
        if atr is None or atr <= 0:
            continue
        entry = upper
        stop = grab.extreme - stop_buffer * atr
        pool = nearest_pool(pools, foundation, entry_index, above=True)
        if pool is None or pool.level <= entry:
            continue
        target = pool.level
        if stop >= entry or target <= entry:
            continue
        rr = (target - entry) / (entry - stop)
        signals.append(make_signal(
            module=MODULE, version=VERSION,
            symbol=candles[entry_index].symbol, timeframe=candles[entry_index].timeframe,
            direction=Direction.LONG, entry=entry, stop=stop, target=target,
            candle=candles[entry_index], known_index=entry_index, candles=list(candles),
            formation_index=t0,
            reasons=[
                f"sell-side grab at candle {t0}: the candle pierced the active pool at "
                f"{grab.pool_level:.5f} and closed back above it",
                f"bullish MSS at candle {t2} (displacement {mss.displacement_body_atr:.2f} x ATR, "
                f"close ratio {mss.close_ratio:.2f}) after the grab, in that order",
                f"the retrace at candle {entry_index} reaches the bullish zone "
                f"[{lower:.5f}, {upper:.5f}] confirmed in the MSS leg",
                "the setup is long into a discount-side sweep with the target at the "
                f"nearest active buy-side pool {target:.5f}",
            ],
            invalidation=[
                f"a close below the swept low {grab.extreme:.5f} minus the ATR buffer",
                f"the setup expires if no entry within {expiry} candles of the MSS",
                "a close beyond the target before entry voids the target",
            ],
            features={
                "grab_depth_atr": grab.depth_atr,
                "grab_rejection": grab.rejection_ratio,
                "pool_size": float(grab.pool_size),
                "candles_grab_to_mss": float(t2 - t0),
                "candles_mss_to_entry": float(entry_index - t2),
                "zone_width_atr": (upper - lower) / atr,
                "reward_risk": rr,
                "displacement_body_atr": mss.displacement_body_atr,
                "close_ratio": mss.close_ratio,
            },
            meta={"grab": grab.to_dict(), "mss": mss.to_dict(), "zone": zone_meta,
                  "pool": pool.to_dict()},
        ))
    return signals
