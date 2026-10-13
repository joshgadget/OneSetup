"""SLP: structure, liquidity and point of interest.

There is no published source for SLP; the spec calls it a practitioner sequence turned
into rules and says the definitions are its own. This file implements that sequence: the
structure sets the bias, a liquidity event shows where the move may start, and a point of
interest is where entry is allowed. Nothing here is claimed to predict anything.

Specification, SLP section:

    Bias:     the SMC state machine on the higher timeframe, +1 bull, -1 bear, 0 none.
              The protected level is the swing low that formed before the latest higher
              high in a bull trend (mirror for bear). A close beyond it by more than
              0.05 x ATR ends the bias and sets it to 0. Two timeframes are enough to
              start, for example H4 for bias and M15 for confirmation.
    Liquidity: for a long, a sell-side grab of an active pool while bias = +1 and while
              price is in discount; for a short, a buy-side grab in premium while
              bias = -1. After the grab, require a lower-timeframe MSS in the bias
              direction within r = 10 candles.
    POI:      candidate zones from order blocks, fair value gaps, supply and demand
              zones, support and resistance intervals and MSNR zones. A candidate
              qualifies when all four hold:
                1. it formed in the leg that produced the MSS, at or before the
                   displacement candle and after the grab;
                2. it is unmitigated, N = 0 touches since it was confirmed;
                3. for a long it lies in discount, z_hi <= EQ, or at least its midpoint
                   does;
                4. its distance from the current price is at most 3 ATR.
              score(Z_i) = sum_{j != i} omega_type(j) * ov(Z_i, Z_j) + omega_type(i),
              with the overlap ov(A, B) = |A ∩ B| / |A|; POI = argmax score.
    Entry, stop, target: entry at the proximal edge of the POI (midpoint is a separate
              variant); stop beyond the farther of the POI's distal edge and the swept
              extreme, minus 0.1 x ATR; target the nearest active opposing pool.
    Event order: bias_{t0} -> LQ_{t1} -> MSS_{t2} -> POI_{t3} -> entry_{t4}, with
              t0 <= t1 < t2 < t3 <= t4. Cancel if price closes beyond the swept extreme,
              or if more than a set number of candles pass before entry.

Readings that are ours (DECISIONS.md D-013): the EQ that gates the liquidity event and the
POI discount rule comes from the lower-timeframe dealing range; the POI's formation window
is [grab.known_index, MSS candle] while its selection, t3, happens at the MSS close; the
distance rule is measured at the selection candle from the nearest edge of the zone; a candle that closes
beyond the swept extreme is never the entry candle; the
overlap weights are provisional unit weights until the Phase 7 fit, so the POI score is a
rank and never a probability; gap levels from MSNR are not used as POIs in v1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from engine.config import load_params
from engine.foundation import Foundation, Trend
from engine.models import Candle, Direction, ModuleSignal
from engine.modules.common import make_signal

MODULE = "slp"
VERSION = "1.1.0"

LONG = "long"
SHORT = "short"


@dataclass
class PoiCandidate:
    type: str
    lower: float
    upper: float
    formed_index: int
    known_index: int
    meta: dict = field(default_factory=dict)
    source: object | None = None

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "lower": self.lower,
            "upper": self.upper,
            "formed_index": self.formed_index,
            "known_index": self.known_index,
            "meta": self.meta,
        }


# ------------------------------------------------------------------- bias


def htf_bias_series(
    htf_candles: Sequence[Candle],
    htf_foundation: Foundation,
    params=None,
) -> list[int]:
    """+1, -1 or 0 per higher-timeframe candle, with the protected-level kill rule."""
    params = params or load_params()
    eps_atr = float(params.slp.protected_break_atr)
    out: list[int] = []
    killed: set[tuple[str, int]] | None = None
    for t, candle in enumerate(htf_candles):
        if killed is not None:
            new = [lb for lb in htf_foundation.labels
                   if lb.known_index <= t and (lb.kind, lb.index) not in killed]
            if new:
                killed = None
        trend = Trend.NONE if killed is not None else htf_foundation.trend_at(t)
        atr = htf_foundation.atr_at(t)
        bias = 0
        if trend is Trend.BULL:
            protected = htf_foundation.protected_level(t - 1, trend)
            if (protected is not None and atr and atr > 0
                    and candle.close < protected.price - eps_atr * atr):
                killed = {(lb.kind, lb.index) for lb in htf_foundation.labels
                          if lb.known_index <= t}
            else:
                bias = 1
        elif trend is Trend.BEAR:
            protected = htf_foundation.protected_level(t - 1, trend)
            if (protected is not None and atr and atr > 0
                    and candle.close > protected.price + eps_atr * atr):
                killed = {(lb.kind, lb.index) for lb in htf_foundation.labels
                          if lb.known_index <= t}
            else:
                bias = -1
        out.append(bias)
    return out


def bias_known_at(
    candle: Candle,
    htf_candles: Sequence[Candle],
    bias_series: Sequence[int],
) -> int | None:
    """The bias of the latest higher-timeframe candle that had fully closed."""
    best = None
    for i, htf in enumerate(htf_candles):
        from engine.modules.crt import interval_end
        if interval_end(htf) <= candle.open_time:
            best = i
    return bias_series[best] if best is not None else None


# -------------------------------------------------------------- candidates


def collect_candidates(
    *,
    fvgs: Sequence = (),
    order_blocks: Sequence = (),
    sd_zones: Sequence = (),
    sr_levels: Sequence = (),
    msnr_levels: Sequence = (),
    side: str,
) -> list[PoiCandidate]:
    """Zones of the requested side from every detector, with their formation indices.

    Mitigation is deliberately not checked here: whether a zone was unmitigated at the
    moment the setup is evaluated is a question for `unmitigated`, which takes the candle
    index, so nothing in this file can read a later touch.
    """
    want_bull = side == LONG
    out: list[PoiCandidate] = []
    for fvg in fvgs:
        if (fvg.direction == "bull") != want_bull:
            continue
        out.append(PoiCandidate("fvg", fvg.lower, fvg.upper, max(0, fvg.index - 2),
                                fvg.known_index, fvg.to_dict(), fvg))
    for block in order_blocks:
        if (block.direction == "bull") != want_bull:
            continue
        out.append(PoiCandidate("order_block", block.lower, block.upper, block.candle_index,
                                block.known_index, block.to_dict(), block))
    for zone in sd_zones:
        if (zone.kind == "demand") != want_bull:
            continue
        out.append(PoiCandidate("supply_demand", zone.lower, zone.upper, zone.base_start,
                                zone.known_index, zone.to_dict(), zone))
    for level in sr_levels:
        is_support = getattr(level.kind, "value", level.kind) == "support"
        if is_support != want_bull:
            continue
        out.append(PoiCandidate("support_resistance", level.lower, level.upper,
                                level.formed_index, level.known_index,
                                level.to_dict() if hasattr(level, "to_dict") else {}, level))
    for level in msnr_levels:
        is_v = level.kind == "V"
        if is_v != want_bull:
            continue
        out.append(PoiCandidate("msnr", level.lower, level.upper, level.index,
                                level.known_index, level.to_dict(), level))
    return out


def unmitigated(cand: PoiCandidate, before: int) -> bool:
    """Rule 2, evaluated at candle `before`: no touch and no break strictly before it."""
    src = cand.source
    if src is None:
        return True
    touches = getattr(src, "touch_indices", None)
    if touches is None:
        touches = getattr(src, "touches", [])
    if isinstance(touches, int):
        return touches == 0
    for i in touches:
        if i < before:
            return False
    for attr in ("invalidated_index", "broken_index", "mitigated_index"):
        idx = getattr(src, attr, None)
        if idx is not None and idx < before:
            return False
    state = getattr(src, "state", None)
    if state is not None and state != "active":
        return False
    return True


def overlap(a: PoiCandidate, b: PoiCandidate) -> float:
    """ov(A, B) = |A ∩ B| / |A|, the share of A covered by B."""
    if a.width <= 0:
        return 0.0
    inter = min(a.upper, b.upper) - max(a.lower, b.lower)
    return max(0.0, inter) / a.width


def score_candidates(candidates: Sequence[PoiCandidate], params=None) -> list[tuple[PoiCandidate, float]]:
    """score(Z_i) = sum_{j != i} omega_type(j) * ov(Z_i, Z_j) + omega_type(i)."""
    params = params or load_params()
    weights = params.slp.poi_weights
    scored: list[tuple[PoiCandidate, float]] = []
    for i, cand in enumerate(candidates):
        total = float(weights[cand.type])
        for j, other in enumerate(candidates):
            if i == j:
                continue
            total += float(weights[other.type]) * overlap(cand, other)
        scored.append((cand, total))
    return scored


# ------------------------------------------------------------ the model


def detect_slp(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    htf_candles: Sequence[Candle] | None = None,
    htf_foundation: Foundation | None = None,
    bias_series: Sequence[int] | None = None,
    pools: Sequence | None = None,
    grabs: Sequence | None = None,
    mss_events: Sequence | None = None,
    fvgs: Sequence | None = None,
    order_blocks: Sequence | None = None,
    sd_zones: Sequence | None = None,
    sr_levels: Sequence | None = None,
    msnr_levels: Sequence | None = None,
    stop_index: int | None = None,
) -> list[ModuleSignal]:
    """Run the full sequence on the entry timeframe and return the setups it produces."""
    from engine.foundation import compute_foundation
    from engine.modules.liquidity import Side, dealing_range, nearest_pool, position_of
    from engine.modules.smc import detect_fvgs, detect_mss, detect_order_blocks

    params = params or load_params()
    slp = params.slp
    r = int(slp.mss_window_candles)
    expiry = int(slp.entry_expiry_candles)
    max_distance = float(slp.poi_max_distance_atr)
    stop_buffer = float(slp.poi_stop_buffer_atr)
    cancel_buffer = float(slp.cancel_buffer_atr)

    htf_candles = list(htf_candles) if htf_candles is not None else list(candles)
    if htf_foundation is None:
        htf_foundation = (compute_foundation(htf_candles, k=foundation.k)
                          if htf_candles is not candles else foundation)
    if bias_series is None:
        bias_series = htf_bias_series(htf_candles, htf_foundation, params)

    grabs = list(grabs) if grabs is not None else []
    pools = list(pools) if pools is not None else []
    mss_events = list(mss_events) if mss_events is not None else detect_mss(
        candles, foundation, params, grabs=grabs, stop_index=stop_index)
    fvgs = list(fvgs) if fvgs is not None else detect_fvgs(candles, foundation, params,
                                                           stop_index=stop_index)
    order_blocks = list(order_blocks) if order_blocks is not None else detect_order_blocks(
        candles, foundation, params, stop_index=stop_index)
    sd_zones = list(sd_zones) if sd_zones is not None else []
    sr_levels = list(sr_levels) if sr_levels is not None else []
    msnr_levels = list(msnr_levels) if msnr_levels is not None else []

    signals: list[ModuleSignal] = []
    for side in (LONG, SHORT):
        need_side = Side.SELL if side == LONG else Side.BUY
        want_bias = 1 if side == LONG else -1
        want_mss = "bull" if side == LONG else "bear"
        for mss in mss_events:
            if mss.direction != want_mss:
                continue
            t2 = mss.index
            if stop_index is not None and t2 > stop_index:
                break
            atr2 = foundation.atr_at(t2)
            if atr2 is None or atr2 <= 0:
                continue
            rng = dealing_range(foundation, t2)
            if rng is None:
                continue
            eq = (rng[0] + rng[1]) / 2.0
            chains = []
            for grab in grabs:
                if grab.side is not need_side or not grab.pool_active:
                    continue
                if not (t2 - r <= grab.known_index < t2):
                    continue
                t1 = grab.known_index
                bias = bias_known_at(candles[t1], htf_candles, bias_series)
                if bias != want_bias:
                    continue
                close1 = candles[t1].close
                if side == LONG and close1 >= eq:
                    continue
                if side == SHORT and close1 <= eq:
                    continue
                chains.append(grab)
            if not chains:
                continue
            grab = (min(chains, key=lambda g: g.extreme) if side == LONG
                    else max(chains, key=lambda g: g.extreme))
            t1 = grab.known_index
            staged = []
            for cand in collect_candidates(fvgs=fvgs, order_blocks=order_blocks,
                                           sd_zones=sd_zones, sr_levels=sr_levels,
                                           msnr_levels=msnr_levels, side=side):
                if cand.formed_index < t1 or cand.known_index > t2:
                    continue          # rule 1: formed in the leg, after the grab
                if side == LONG:
                    if not (cand.upper <= eq or cand.midpoint <= eq):
                        continue      # rule 3: discount
                else:
                    if not (cand.lower >= eq or cand.midpoint >= eq):
                        continue      # rule 3: premium
                staged.append(cand)
            if not staged:
                continue
            entry_index = None
            best_cand = None
            best_score = 0.0
            distance_atr = 0.0
            for t in range(t2 + 1, min(t2 + expiry, len(candles) - 1) + 1):
                if stop_index is not None and t > stop_index:
                    break
                cancelled = False
                # the candle that closes beyond the swept extreme cannot be the entry
                # candle: within one candle the touch and the close cannot be ordered, so
                # the cancel wins (DECISIONS.md D-013)
                for c in candles[t2 + 1: t + 1]:
                    if side == LONG and c.close < grab.extreme - cancel_buffer * atr2:
                        cancelled = True
                        break
                    if side == SHORT and c.close > grab.extreme + cancel_buffer * atr2:
                        cancelled = True
                        break
                if cancelled:
                    break
                atr = foundation.atr_at(t)
                if atr is None or atr <= 0:
                    continue
                candle = candles[t]
                live = []
                for cand in staged:
                    if cand.known_index >= t:
                        continue
                    if not unmitigated(cand, t):
                        continue
                    close = candle.close
                    distance = 0.0 if cand.lower <= close <= cand.upper else min(
                        abs(close - cand.lower), abs(close - cand.upper))
                    if distance / atr > max_distance:
                        continue      # rule 4: too far from the price on this candle
                    if candle.low <= cand.upper and candle.high >= cand.lower:
                        live.append((cand, distance / atr))
                if not live:
                    continue
                scored = score_candidates([c for c, _ in live], params)
                best_cand, best_score = max(
                    scored, key=lambda pair: (pair[1], -pair[0].known_index))
                distance_atr = next(d for c, d in live if c is best_cand)
                entry_index = t
                break
            if entry_index is None:
                continue
            atr = foundation.atr_at(entry_index)
            if atr is None or atr <= 0:
                continue
            if side == LONG:
                entry = best_cand.upper
                stop = min(best_cand.lower, grab.extreme) - stop_buffer * atr
                pool = nearest_pool(pools, foundation, entry_index, above=True)
                direction = Direction.LONG
                if pool is None or pool.level <= entry or stop >= entry:
                    continue
                target = pool.level
            else:
                entry = best_cand.lower
                stop = max(best_cand.upper, grab.extreme) + stop_buffer * atr
                pool = nearest_pool(pools, foundation, entry_index, above=False)
                direction = Direction.SHORT
                if pool is None or pool.level >= entry or stop <= entry:
                    continue
                target = pool.level
            rr = abs(target - entry) / abs(entry - stop)
            overlaps = {other.type: round(overlap(best_cand, other), 4)
                        for other in staged if other is not best_cand}
            signals.append(make_signal(
                module=MODULE, version=VERSION,
                symbol=candles[entry_index].symbol, timeframe=candles[entry_index].timeframe,
                direction=direction, entry=entry, stop=stop, target=target,
                candle=candles[entry_index], known_index=entry_index, candles=list(candles),
                formation_index=t1,
                reasons=[
                    f"higher-timeframe bias {want_bias:+d} at the grab, from the structure "
                    f"state machine and its protected level",
                    f"{need_side.value} grab at candle {t1} of the active pool at "
                    f"{grab.pool_level:.5f}, "
                    + ("while price was in discount" if side == LONG
                       else "while price was in premium"),
                    f"{want_mss} MSS at candle {t2}, within {r} candles of the grab, "
                    f"event order enforced",
                    f"point of interest: {best_cand.type} zone "
                    f"[{best_cand.lower:.5f}, {best_cand.upper:.5f}] formed at candle "
                    f"{best_cand.formed_index} with overlap score {best_score:.3f} "
                    f"(provisional weights, a rank and not a probability)",
                    f"entry at the proximal edge of the POI at candle {entry_index}, "
                    f"stop beyond the farther of the distal edge and the swept extreme, "
                    f"target the nearest active opposing pool {target:.5f}",
                ],
                invalidation=[
                    f"a close beyond the swept extreme {grab.extreme:.5f} cancels the setup",
                    f"the setup expires {expiry} candles after the MSS if the retrace "
                    f"does not arrive",
                    f"a close beyond {target:.5f} before entry voids the target",
                ],
                features={
                    "bias": float(want_bias),
                    "grab_depth_atr": grab.depth_atr,
                    "grab_rejection": grab.rejection_ratio,
                    "pool_size": float(grab.pool_size),
                    "candles_grab_to_mss": float(t2 - t1),
                    "candles_mss_to_entry": float(entry_index - t2),
                    "poi_score": float(best_score),
                    "poi_width_atr": best_cand.width / atr,
                    "poi_distance_atr": float(distance_atr),
                    "poi_type_ordinal": float(
                        ["fvg", "order_block", "supply_demand", "support_resistance",
                         "msnr"].index(best_cand.type)),
                    "reward_risk": rr,
                    "eq_distance_atr": abs(candles[t1].close - eq) / atr,
                },
                meta={
                    "grab": grab.to_dict(),
                    "mss": mss.to_dict(),
                    "poi": best_cand.to_dict(),
                    "poi_overlaps": overlaps,
                    "poi_scores": [
                        {"type": c.type, "lower": c.lower, "upper": c.upper,
                         "known_index": c.known_index, "score": round(s, 4)}
                        for c, s in scored
                    ],
                    "range": {"low": rng[0], "high": rng[1], "eq": eq},
                    "pool": pool.to_dict(),
                    "entry_variant": str(slp.poi_entry_variant),
                },
            ))
    return signals