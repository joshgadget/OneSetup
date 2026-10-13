"""MSNR: Malaysian Support and Resistance, a community method, no published definition.

The spec says the rules come from the common description and that details vary by
teacher, so this is one named reading. Levels are built from closing prices only, each
level is widened into a zone, and fresh zones are trusted most. Nothing here claims
untouched zones hold more reliably.

Specification, MSNR section:

    A-level (local peak of the close), with k closes each side:
        A_t = 1[ C_t > max_{1<=j<=k} C_{t-j}  and  C_t >= max_{1<=j<=k} C_{t+j} ]
    V-level mirrors with the inequalities reversed. The price is the close itself,
    P = C_t, and the level is knowable at t + k. k = 1 confirms one candle after.
    Zone:  Z = [P - gamma, P + gamma], gamma = max(gamma_min, beta x ATR_t), beta = 0.1.
           gamma_min is a few ticks. Body-based zones are a separate module and are not
           mixed in here.
    Gap:   g_t = O_t - C_{t-1}; a gap level when |g_t| >= theta x ATR_t, theta = 0.2.
    Freshness: fresh until price trades into the zone again; graded F = exp(-lambda N),
           lambda = 0.7, N = qualifying retests. The spec calls this a proposal, and it
           is reported as a score here, never as a probability.
    Touch: any candle range intersecting the zone. Break: a close beyond the far edge by
           0.1 x ATR. Bounce: a touch followed by a close back on the original side.
           These are the events the support and resistance section counts, so each level
           is scored with the same Bayesian bounce probability.
    Setup v1: long at a V-level zone when a candle touches the zone and closes bullish
           above it, and a CHoCH or MSS from the SMC section follows within r = 5
           candles. Stop below the zone minus 0.25 x ATR, target the nearest A-level zone.

Readings that are ours (DECISIONS.md D-011): the gap level price is the midpoint of the
gap; the target for setup v1 is the near edge of the nearest A-level zone above entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from engine.config import load_params
from engine.foundation import Foundation
from engine.models import Candle, Direction, ModuleSignal
from engine.modules.common import make_signal
from engine.scoring.stats import beta_posterior_mean

MODULE = "msnr"
VERSION = "1.0.0"


@dataclass
class MsnrLevel:
    kind: str                    # A | V | gap
    price: float
    index: int                   # formation index (the turn candle, or the gap candle)
    known_index: int
    gamma: float
    touch_indices: list[int] = field(default_factory=list)
    bounce_episodes: list[tuple[int, int]] = field(default_factory=list)
    penetration_episodes: list[tuple[int, int]] = field(default_factory=list)
    expired_episodes: list[tuple[int, int]] = field(default_factory=list)
    invalidated_index: int | None = None
    last_close: float | None = None
    position: str = "unknown"
    pending: list[tuple[int, int]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def lower(self) -> float:
        return self.price - self.gamma

    @property
    def upper(self) -> float:
        return self.price + self.gamma

    @property
    def touches(self) -> int:
        return len(self.touch_indices)

    @property
    def bounces(self) -> int:
        return len(self.bounce_episodes)

    @property
    def penetrations(self) -> int:
        return len(self.penetration_episodes)

    @property
    def resolved(self) -> int:
        return self.bounces + self.penetrations

    @property
    def fresh(self) -> bool:
        return not self.touch_indices

    @property
    def bounce_probability(self) -> float:
        """(n + 1) / (N + 2), the same Bayesian strength the S and R section uses."""
        return beta_posterior_mean(self.bounces, self.penetrations)

    def freshness_score(self, lam: float) -> float:
        return pow(2.718281828459045, -lam * self.touches)

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "price": self.price,
            "index": self.index,
            "known_index": self.known_index,
            "lower": self.lower,
            "upper": self.upper,
            "touches": self.touches,
            "bounces": self.bounces,
            "penetrations": self.penetrations,
            "bounce_probability": self.bounce_probability,
            "fresh": self.fresh,
            "invalidated_index": self.invalidated_index,
            "meta": self.meta,
        }


def _gamma(foundation: Foundation, t: int, beta: float, tick_size: float | None,
           min_ticks: int) -> float:
    atr = foundation.atr_at(t) or 0.0
    floor = (tick_size or 0.0) * min_ticks
    return max(floor, beta * atr)


def close_levels(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    tick_size: float | None = None,
    stop_index: int | None = None,
) -> list[MsnrLevel]:
    """A-levels and V-levels on closes, online, each knowable at t + k."""
    params = params or load_params()
    msnr = params.msnr
    k = int(msnr.pivot_k)
    beta = float(msnr.zone_gamma_atr)
    min_ticks = int(msnr.zone_gamma_min_ticks)
    out: list[MsnrLevel] = []
    n = len(candles)
    for t in range(k, n - k):
        if stop_index is not None and t > stop_index:
            break
        known = t + k
        if stop_index is not None and known > stop_index:
            break
        closes = [c.close for c in candles]
        left = max(closes[t - j] for j in range(1, k + 1))
        right = max(closes[t + j] for j in range(1, k + 1))
        if closes[t] > left and closes[t] >= right:
            out.append(MsnrLevel("A", closes[t], t, known,
                                 _gamma(foundation, t, beta, tick_size, min_ticks)))
            continue
        left = min(closes[t - j] for j in range(1, k + 1))
        right = min(closes[t + j] for j in range(1, k + 1))
        if closes[t] < left and closes[t] <= right:
            out.append(MsnrLevel("V", closes[t], t, known,
                                 _gamma(foundation, t, beta, tick_size, min_ticks)))
    return out


def gap_levels(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    tick_size: float | None = None,
    stop_index: int | None = None,
) -> list[MsnrLevel]:
    """Gap levels: |O_t - C_{t-1}| >= theta x ATR_t. Level price is the gap midpoint."""
    params = params or load_params()
    msnr = params.msnr
    theta = float(msnr.gap_theta_atr)
    beta = float(msnr.zone_gamma_atr)
    min_ticks = int(msnr.zone_gamma_min_ticks)
    out: list[MsnrLevel] = []
    for t in range(1, len(candles)):
        if stop_index is not None and t > stop_index:
            break
        atr = foundation.atr_at(t)
        if atr is None or atr <= 0:
            continue
        gap = candles[t].open - candles[t - 1].close
        if abs(gap) >= theta * atr:
            price = (candles[t].open + candles[t - 1].close) / 2.0
            level = MsnrLevel("gap", price, t, t,
                              _gamma(foundation, t, beta, tick_size, min_ticks))
            level.meta = {"gap_size_atr": abs(gap) / atr,
                          "prior_close": candles[t - 1].close,
                          "open": candles[t].open}
            out.append(level)
    return out


def advance_levels(
    levels: Sequence[MsnrLevel],
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    stop_index: int | None = None,
) -> None:
    """Walk every level forward through the candles, in place.

    Touch: any candle range intersecting the zone. Episode resolution mirrors the
    support and resistance section: an episode opens on a touch and resolves on the
    first close outside the zone, bounce or penetration by which side; an episode that
    stays inside past the horizon expires. Break: a close beyond the far edge by
    0.1 x ATR invalidates the level, and a close back beyond the near edge by the same
    buffer re-arms nothing: the level stays invalidated, matching that section.
    """
    params = params or load_params()
    horizon = int(params.support_resistance.horizon_omega)
    break_buffer_atr = float(params.supply_demand.invalidation_buffer_atr)

    for index, candle in enumerate(candles):
        if stop_index is not None and index > stop_index:
            break
        atr = foundation.atr_at(index)
        for level in levels:
            if level.known_index > index or level.invalidated_index is not None:
                continue
            if index == level.known_index:
                continue
            still: list[tuple[int, int]] = []
            for start, deadline in level.pending:
                if candle.close > level.upper:
                    (level.bounce_episodes if level.kind != "A" else
                     level.penetration_episodes).append((start, index))
                elif candle.close < level.lower:
                    (level.penetration_episodes if level.kind != "A" else
                     level.bounce_episodes).append((start, index))
                elif index >= deadline:
                    level.expired_episodes.append((start, index))
                else:
                    still.append((start, deadline))
            level.pending = still
            touched = candle.low <= level.upper and candle.high >= level.lower
            if touched:
                level.touch_indices.append(index)
                level.pending.append((index, index + horizon))
            level.last_close = candle.close
            if atr is None or atr <= 0:
                continue
            buffer = break_buffer_atr * atr
            if level.kind == "A" and candle.close > level.upper + buffer:
                level.invalidated_index = index
            elif level.kind != "A" and candle.close < level.lower - buffer:
                level.invalidated_index = index


def detect_levels(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    tick_size: float | None = None,
    stop_index: int | None = None,
    include_gaps: bool = True,
) -> list[MsnrLevel]:
    """Close levels, gap levels, and their touch, bounce and break history."""
    params = params or load_params()
    levels = close_levels(candles, foundation, params, tick_size=tick_size, stop_index=stop_index)
    if include_gaps:
        levels += gap_levels(candles, foundation, params, tick_size=tick_size, stop_index=stop_index)
    levels.sort(key=lambda lv: (lv.known_index, lv.index, lv.kind))
    advance_levels(levels, candles, foundation, params, stop_index=stop_index)
    return levels


def detect_msnr_setup_v1(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    levels: Sequence[MsnrLevel] | None = None,
    smc_events: Sequence | None = None,
    mss_events: Sequence | None = None,
    tick_size: float | None = None,
    stop_index: int | None = None,
) -> list[ModuleSignal]:
    """Setup v1, long side only: a V-level touch with a bullish close, then CHoCH or MSS."""
    from engine.modules.smc import detect_mss, detect_structure_events

    params = params or load_params()
    msnr = params.msnr
    follow_up = int(msnr.follow_up_candles)
    stop_buffer = float(msnr.stop_buffer_atr)

    levels = list(levels) if levels is not None else detect_levels(
        candles, foundation, params, tick_size=tick_size, stop_index=stop_index)
    if smc_events is None:
        smc_events = detect_structure_events(candles, foundation, params, stop_index=stop_index)
    if mss_events is None:
        mss_events = detect_mss(candles, foundation, params, events=smc_events,
                                stop_index=stop_index)
    structure = [(ev.index, "CHoCH" if ev.kind == "CHoCH" else "BOS") for ev in smc_events
                 if ev.kind == "CHoCH" and ev.direction == "bull"]
    structure += [(ev.index, "MSS") for ev in mss_events if ev.direction == "bull"]
    structure.sort()

    signals: list[ModuleSignal] = []
    for level in levels:
        if level.kind != "V":
            continue
        for t in level.touch_indices:
            candle = candles[t]
            if not (candle.close > candle.open and candle.close > level.upper):
                continue
            following = [(idx, kind) for idx, kind in structure if t < idx <= t + follow_up]
            if not following:
                continue
            idx, kind = following[0]
            atr = foundation.atr_at(idx)
            if atr is None or atr <= 0:
                continue
            entry = candles[idx].close
            stop = level.lower - stop_buffer * atr
            candidates = [lv for lv in levels
                          if lv.kind == "A" and lv.lower > entry
                          and lv.invalidated_index is None]
            if not candidates:
                continue
            target_level = min(candidates, key=lambda lv: lv.lower)
            target = target_level.lower
            if stop >= entry or target <= entry:
                continue
            rr = (target - entry) / (entry - stop)
            signals.append(make_signal(
                module=MODULE, version=VERSION,
                symbol=candle.symbol, timeframe=candle.timeframe,
                direction=Direction.LONG, entry=entry, stop=stop, target=target,
                candle=candles[idx], known_index=idx, candles=list(candles),
                formation_index=level.index,
                reasons=[
                    f"V-level at {level.price:.5f} formed at candle {level.index} and was "
                    f"confirmed at {level.known_index} (k = {params.msnr.pivot_k} closes each side)",
                    f"candle {t} touched the zone [{level.lower:.5f}, {level.upper:.5f}] and "
                    f"closed bullish above it",
                    f"a bullish {kind} followed at candle {idx}, within "
                    f"{int(msnr.follow_up_candles)} candles, in that order",
                    f"target is the near edge of the nearest A-level zone above, "
                    f"{target:.5f}, not beyond it",
                ],
                invalidation=[
                    f"a close below the zone {level.lower:.5f} minus the ATR buffer "
                    f"voids the setup",
                    f"a close beyond {target:.5f} before entry voids the target",
                    "the level is invalidated for good once a close breaks it by more "
                    "than the ATR buffer",
                ],
                features={
                    "level_touches": float(level.touches),
                    "level_bounce_probability": level.bounce_probability,
                    "level_bounces": float(level.bounces),
                    "level_penetrations": float(level.penetrations),
                    "freshness_score": level.freshness_score(float(msnr.freshness_lambda)),
                    "zone_width_atr": (level.upper - level.lower) / atr,
                    "candles_touch_to_structure": float(idx - t),
                    "reward_risk": rr,
                },
                meta={"level": level.to_dict(), "target_level": target_level.to_dict(),
                      "follow_up": kind},
            ))
            break
    return signals