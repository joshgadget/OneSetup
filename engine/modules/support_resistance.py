"""Support and resistance, the one module with a published formal definition.

Chung and Bellotti (2021), as transcribed in the specification:

    Support:    x_t in [a, b]  =>  P(x_{t+d} > b) > P(x_{t+d} < a),  d = 1..omega
    Resistance: the mirror image.
    Discovery:  Support = min X_t +/- gamma,  Resistance = max X_t +/- gamma
    Strength:   E[p] = (n+1)/(N+2),  Var[p] = (n+1)(N-n+1)/((N+3)(N+2)^2)

What counts as a bounce. The paper's probability is conditional on price being inside
the interval, sampled at each candle, with a horizon of omega candles: given x_t is in
[a, b], does price leave through the near edge (bounce) or the far edge (penetration)
first? So every candle whose close is inside the interval opens an episode, and the
episode resolves at the first close beyond an edge, or expires if omega candles pass
with price still inside. Episodes still inside are excluded from p(b), exactly as the
specification's conditional probability excludes them.

Two other readings were measured and rejected, and both are reported in DECISIONS.md
D-008 because they matter:
  * counting crossings of the near edge, halved, as the specification's formula
    literally reads (S_b = floor(1/2 * crossings)) gives about 0.92 on a driftless
    random walk, because a level anchored on a swing is crossed at its near edge far
    more often than at its far edge.
  * counting only first entries from the near side gives about 0.59, because price
    enters near that edge and so tends to leave through it.
The per-candle-inside estimator below is the one that reproduces the paper's reported
~0.5 on random data, which is the acceptance test in the specification.

Live adaptations, all parameterised: gamma from a trailing window (m = 200) or
0.25 x ATR; several levels from every confirmed swing, with levels within gamma of each
other merged into a NEW interval (the originals are frozen as `merged`, so a later merge
can never rewrite published evidence); age weighting w = exp(-lambda * candles) as a
separate ranked estimate; break confirmation on a close beyond edge +/- 0.1 x ATR.

Signal rule v1 (a convention, not part of the paper): long when price has entered a
support interval, a later candle closes above b, and the level's strength exceeds 0.5;
stop at a - 0.25 x ATR; first target the lower bound of the nearest resistance interval,
never beyond it. Short mirrors it. With no opposing interval there is no target, and the
module returns nothing rather than inventing one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from engine.foundation import Foundation
from engine.models import Candle, Direction
from engine.modules.common import make_signal
from engine.scoring.stats import beta_posterior_mean, beta_posterior_variance

MODULE = "support_resistance"
VERSION = "1.2.0"


class LevelKind(str, Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


@dataclass
class Level:
    """A support or resistance interval [lower, upper] with its evidence.

    Near edge: the boundary a bounce leaves through (support upper, resistance lower).
    Far edge:  the boundary a penetration leaves through.
    """

    kind: LevelKind
    lower: float
    upper: float
    gamma: float
    formed_index: int
    known_index: int
    swing_indices: list[int] = field(default_factory=list)
    swing_prices: list[float] = field(default_factory=list)
    # episodes: (start_index, deadline_index) for every candle found inside the interval
    pending: list[tuple[int, int]] = field(default_factory=list)
    bounces_count: int = 0
    penetrations_count: int = 0
    expired_count: int = 0
    inside_count: int = 0
    touch_indices: list[int] = field(default_factory=list)
    trigger_indices: list[int] = field(default_factory=list)
    last_touch_index: int | None = None
    last_close: float | None = None
    state: str = "active"                  # active | broken | merged
    broken_index: int | None = None
    absorbed_into: int | None = None
    position: str = "outside"              # above | inside | below

    # ---------------------------------------------------------------- geometry

    @property
    def centre(self) -> float:
        return (self.lower + self.upper) / 2.0

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def near_edge(self) -> float:
        return self.upper if self.kind is LevelKind.SUPPORT else self.lower

    @property
    def far_edge(self) -> float:
        return self.lower if self.kind is LevelKind.SUPPORT else self.upper

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    # ---------------------------------------------------------------- evidence

    @property
    def bounces(self) -> int:
        """Episodes that left through the near edge within the horizon."""
        return self.bounces_count

    @property
    def penetrations(self) -> int:
        """Episodes that left through the far edge within the horizon."""
        return self.penetrations_count

    @property
    def resolved(self) -> int:
        return self.bounces_count + self.penetrations_count

    @property
    def strength(self) -> float:
        """Bayesian bounce probability, uniform prior. No evidence gives exactly 0.5."""
        return beta_posterior_mean(self.bounces, self.penetrations)

    @property
    def strength_variance(self) -> float:
        return beta_posterior_variance(self.bounces, self.penetrations)

    def strength_from(self, bounces: int, penetrations: int) -> float:
        return beta_posterior_mean(bounces, penetrations)

    def decayed_strength(self, index: int, lam: float) -> float:
        """Age-weighted evidence, w = exp(-lambda * candles since the last touch)."""
        if self.last_touch_index is None:
            return 0.5
        weight = math.exp(-lam * max(0, index - self.last_touch_index))
        n = self.bounces * weight
        k = self.penetrations * weight
        return (n + 1.0) / (n + k + 2.0)

    def fuse(self, other: "Level", index: int) -> "Level":
        """A new merged interval. Both originals are frozen by the caller."""
        return Level(
            kind=self.kind,
            lower=min(self.lower, other.lower),
            upper=max(self.upper, other.upper),
            gamma=max(self.gamma, other.gamma),
            formed_index=min(self.formed_index, other.formed_index),
            known_index=index,
            swing_indices=sorted(set(self.swing_indices + other.swing_indices)),
            swing_prices=self.swing_prices + other.swing_prices,
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "lower": self.lower,
            "upper": self.upper,
            "gamma": self.gamma,
            "formed_index": self.formed_index,
            "known_index": self.known_index,
            "bounces": self.bounces,
            "penetrations": self.penetrations,
            "expired_episodes": self.expired_count,
            "inside_episodes": self.inside_count,
            "strength": self.strength,
            "strength_variance": self.strength_variance,
            "state": self.state,
            "broken_index": self.broken_index,
            "swings": len(self.swing_indices),
            "last_touch_index": self.last_touch_index,
        }


# --------------------------------------------------------------------- gamma

def gamma_series(
    candles: Sequence[Candle],
    foundation: Foundation,
    *,
    mode: str = "trailing_mean_abs_diff",
    window: int = 200,
    atr_multiple: float = 0.25,
) -> list[float | None]:
    """Level half-width at each index: trailing mean |dx| over `window`, or k x ATR."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if mode == "atr_multiple":
        for t in range(n):
            atr = foundation.atr_at(t)
            out[t] = atr * atr_multiple if atr is not None else None
        return out
    if mode != "trailing_mean_abs_diff":
        raise ValueError(f"unknown gamma mode {mode!r}")
    rolling = 0.0
    for t in range(n):
        if t >= 1:
            rolling += abs(candles[t].close - candles[t - 1].close)
        if t >= window + 1:
            rolling -= abs(candles[t - window].close - candles[t - window - 1].close)
        count = min(t, window)
        out[t] = (rolling / count) if count > 0 else None
    return out


# ------------------------------------------------------------------ the scan

def _advance(
    level: Level,
    candle: Candle,
    index: int,
    atr: float | None,
    break_buffer_atr: float,
    horizon: int,
) -> bool:
    """Move a level forward one candle.

    Resolves open episodes, keeps the position state machine the signal rule needs, and
    applies break confirmation. Returns True when this candle is a signal trigger
    (price had entered the interval and now closes back out through the near edge).
    """
    if level.state != "active":
        return False
    close = candle.close

    # 1. resolve episodes opened on earlier candles
    still_open: list[tuple[int, int]] = []
    for start, deadline in level.pending:
        if close > level.upper:
            if level.kind is LevelKind.SUPPORT:
                level.bounces_count += 1
            else:
                level.penetrations_count += 1
        elif close < level.lower:
            if level.kind is LevelKind.SUPPORT:
                level.penetrations_count += 1
            else:
                level.bounces_count += 1
        elif index >= deadline:
            level.expired_count += 1          # still inside after omega candles
        else:
            still_open.append((start, deadline))
    level.pending = still_open

    if level.last_close is None:
        level.last_close = close
        level.position = "above" if close > level.upper else ("below" if close < level.lower else "inside")
        if level.position == "inside":
            level.last_touch_index = index
            level.touch_indices.append(index)
            level.inside_count += 1
            level.pending.append((index, index + horizon))
        return False
    level.last_close = close

    trigger = False
    if level.kind is LevelKind.SUPPORT:
        if level.position == "above":
            if close <= level.upper:
                level.position = "inside"
                level.last_touch_index = index
                level.touch_indices.append(index)
        elif level.position == "inside":
            if close > level.upper:
                trigger = True
                level.position = "above"
            elif close < level.lower:
                level.position = "below"
        else:  # below
            if close > level.upper:
                level.position = "above"
            elif close >= level.lower:
                level.position = "inside"
                level.last_touch_index = index
                level.touch_indices.append(index)
    else:
        if level.position == "below":
            if close >= level.lower:
                level.position = "inside"
                level.last_touch_index = index
                level.touch_indices.append(index)
        elif level.position == "inside":
            if close < level.lower:
                trigger = True
                level.position = "below"
            elif close > level.upper:
                level.position = "above"
        else:  # above
            if close < level.lower:
                level.position = "below"
            elif close <= level.upper:
                level.position = "inside"
                level.last_touch_index = index
                level.touch_indices.append(index)

    # 2. open a new episode when this candle closed inside the interval
    if level.contains(close):
        level.inside_count += 1
        level.pending.append((index, index + horizon))

    # 3. break confirmation: a close, never a wick
    if atr is not None and atr > 0:
        buffer = break_buffer_atr * atr
        if level.kind is LevelKind.SUPPORT and close < level.lower - buffer:
            level.state, level.broken_index = "broken", index
        elif level.kind is LevelKind.RESISTANCE and close > level.upper + buffer:
            level.state, level.broken_index = "broken", index
    return trigger


def discover_levels(
    candles: Sequence[Candle],
    foundation: Foundation,
    params,
    *,
    collect_signals: bool = True,
    stop_index: int | None = None,
) -> tuple[list[Level], list]:
    """Build the level set online and count evidence as the series unfolds.

    `stop_index` runs the scan but stops the clock there. It exists so a test can prove
    that a longer series produces exactly the same state at candle t as a truncated
    series does: the scan must not touch a candle after t.
    """
    sr = params.support_resistance
    break_buffer = float(sr.break_buffer_atr)
    strength_floor = float(sr.strength_floor)
    stop_buffer = float(sr.stop_buffer_atr)
    horizon = int(sr.horizon_omega)
    decay_lambda = float(getattr(sr, "decay_lambda", 0.02))

    gammas = gamma_series(
        candles, foundation,
        mode=str(sr.gamma_mode),
        window=int(sr.gamma_window_m),
        atr_multiple=float(sr.gamma_atr_multiple),
    )

    levels: list[Level] = []
    signals: list = []
    pivots_by_known: dict[int, list] = {}
    for pv in foundation.pivots:
        pivots_by_known.setdefault(pv.known_index, []).append(pv)

    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        gamma = gammas[t]
        atr = foundation.atr_at(t)

        # 1. New confirmed pivots become candidate levels, unless price is inside an
        #    active level of the same kind: discovery pauses then, so the level stays
        #    fixed while it is being tested.
        if gamma is not None and gamma > 0:
            for pv in pivots_by_known.get(t, []):
                kind = LevelKind.RESISTANCE if pv.kind == "high" else LevelKind.SUPPORT
                if any(lv.kind is kind and lv.state == "active" and lv.contains(candle.close) for lv in levels):
                    continue
                candidate = Level(
                    kind=kind, lower=pv.price - gamma, upper=pv.price + gamma, gamma=gamma,
                    formed_index=pv.index, known_index=pv.known_index,
                    swing_indices=[pv.index], swing_prices=[pv.price],
                )
                partners = [
                    lv for lv in levels
                    if lv.kind is kind and lv.state == "active"
                    and abs(lv.centre - candidate.centre) <= gamma
                ]
                if not partners:
                    levels.append(candidate)
                    continue
                merged = candidate
                for partner in partners:
                    partner.state = "merged"
                    merged = merged.fuse(partner, index=t)
                merged.known_index = t
                levels.append(merged)

        # 2. Advance every live level and act on triggers.
        for lv in levels:
            if lv.state != "active":
                continue
            if lv.known_index > t:
                continue
            if lv.known_index == t and lv.last_close is None:
                _advance(lv, candle, t, atr, break_buffer, horizon)
                continue
            prior_bounces, prior_penetrations = lv.bounces, lv.penetrations
            trigger = _advance(lv, candle, t, atr, break_buffer, horizon)
            if not trigger or not collect_signals or atr is None:
                continue
            strength = lv.strength_from(prior_bounces, prior_penetrations)
            signal = _signal_from_trigger(
                lv, levels, candles, foundation, t, strength, strength_floor,
                stop_buffer, prior_bounces, prior_penetrations, decay_lambda,
            )
            lv.trigger_indices.append(t)
            if signal is not None:
                signals.append(signal)
    return levels, signals


def _signal_from_trigger(
    level: Level,
    levels: Sequence[Level],
    candles: Sequence[Candle],
    foundation: Foundation,
    index: int,
    strength: float,
    strength_floor: float,
    stop_buffer: float,
    prior_bounces: int,
    prior_penetrations: int,
    decay_lambda: float,
):
    """Signal rule v1. Returns None when the rule's conditions are not met."""
    if strength <= strength_floor:
        return None
    atr = foundation.atr_at(index)
    if atr is None or atr <= 0:
        return None
    candle = candles[index]
    entry = candle.close
    if level.kind is LevelKind.SUPPORT:
        stop = level.lower - stop_buffer * atr
        candidates = [
            lv for lv in levels
            if lv.kind is LevelKind.RESISTANCE and lv.state == "active" and lv.lower > entry
        ]
        if not candidates:
            return None
        target_level = min(candidates, key=lambda lv: lv.lower)
        target = target_level.lower
        direction = Direction.LONG
    else:
        stop = level.upper + stop_buffer * atr
        candidates = [
            lv for lv in levels
            if lv.kind is LevelKind.SUPPORT and lv.state == "active" and lv.upper < entry
        ]
        if not candidates:
            return None
        target_level = max(candidates, key=lambda lv: lv.upper)
        target = target_level.upper
        direction = Direction.SHORT
    if abs(entry - stop) <= 0 or abs(target - entry) <= 0:
        return None
    if (direction is Direction.LONG and target <= entry) or (direction is Direction.SHORT and target >= entry):
        return None
    rr = abs(target - entry) / abs(entry - stop)
    return make_signal(
        module=MODULE, version=VERSION,
        symbol=candle.symbol, timeframe=candle.timeframe,
        direction=direction, entry=entry, stop=stop, target=target,
        candle=candle, known_index=index, candles=list(candles),
        formation_index=level.known_index,
        reasons=[
            f"price re-entered the {level.kind.value} interval "
            f"[{level.lower:.5f}, {level.upper:.5f}] built from {len(level.swing_prices)} confirmed swing(s)",
            f"the close back out through the near edge at candle {index} is the trigger",
            f"Bayesian strength {strength:.3f} clears the {strength_floor:.2f} floor "
            f"({prior_bounces} bounces, {prior_penetrations} penetrations resolved before this candle)",
            "target is the lower bound of the nearest opposing interval, never beyond it",
        ],
        invalidation=[
            f"a close below {level.lower:.5f} breaks the interval",
            f"a close beyond {target:.5f} before entry invalidates the target",
            "the level's evidence is invalidated once a close breaks it by more than the ATR buffer",
        ],
        score=None,
        features={
            "level_strength": strength,
            "level_variance": level.strength_variance,
            "level_width_atr": level.width / atr,
            "swing_count": float(len(level.swing_prices)),
            "reward_risk": rr,
            "distance_target_atr": abs(target - entry) / atr,
            "decayed_strength": level.decayed_strength(index, decay_lambda),
        },
        meta={"level": level.to_dict(), "target_level": target_level.to_dict()},
    )