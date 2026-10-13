"""ICT: Inner Circle Trader, a teaching framework, not a published model.

ICT overlaps heavily with SMC, so the SMC detectors (swings, BOS, MSS, displacement, FVG,
order block, sweeps) are reused and only the pieces specific to ICT are added here:
the dealing range and equilibrium, optimal trade entry, session windows, the Judas swing,
power of three, and the Silver Bullet window.

Specification, ICT section:

    EQ = (H_r + L_r) / 2 for the frozen range of the latest confirmed swing high and low;
        discount when P < EQ, premium when P > EQ. "Look for longs in discount and shorts
        in premium" is a hypothesis to test, not a law.
    OTE: for a bullish leg from low L_a to high H_b,
        OTE = [ H_b - 0.79 (H_b - L_a),  H_b - 0.62 (H_b - L_a) ].
        The leg anchors must be chosen by an explicit rule.
    Session filter: S_t = 1[tau_t in [tau_a, tau_b)], with tau_t the candle time in a
        configured timezone. The spec says to verify the exact windows against the
        educator and to keep them as configuration; convert from America/New_York, which
        observes daylight saving, because Lagos does not.
    Judas swing (sell-side version, first w candles of the session, session open O_s):
        exists t <= s + w: L_t < O_s - eps  and  exists j > t, j in session: C_j > O_s.
        The bullish bias is known at the close of candle j, never earlier. The mirror is
        the buy-side version.
    Power of three: accumulation (a range with total width below 1 x ATR), then
        manipulation (a Judas swing out of that range), then distribution (a displacement
        away from it). Detect in that order or not at all.
    Silver Bullet: a configured window plus a fair value gap formed inside the window in
        the direction of a prior sweep. The commonly quoted 10:00-11:00 New York window
        is a parameter here.
    Entry model v1 (example, long): SellSideGrab -> MSS+ -> FVG+ and P < EQ and S_t = 1,
        with event order enforced. If entry waits for a retrace, define the touch rule, an
        expiry in candles, and the cancellation rule, for example a close below the swept
        low.

Readings that are ours (DECISIONS.md D-012): the candle's open time is the time of day
tested against a session window; the anchors for OTE are the latest confirmed swing low
before the MSS and the highest high between that low and the MSS candle; "a prior sweep"
in the Silver Bullet is a grab of the opposite side within a configured window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as clock_time
from typing import Sequence
from zoneinfo import ZoneInfo

from engine.config import load_params
from engine.foundation import Foundation
from engine.models import Candle, Direction, ModuleSignal
from engine.modules.common import make_signal

MODULE = "ict"
VERSION = "1.0.0"

LONG = "bull"
SHORT = "bear"


# ------------------------------------------------------------- sessions


def session_names(params=None) -> list[str]:
    params = params or load_params()
    return [name for name in params.ict.sessions.keys()]


def candle_local_time(candle: Candle, spec) -> clock_time:
    """The candle's open time converted to the session's configured timezone."""
    local = candle.open_time.astimezone(ZoneInfo(str(spec.tz)))
    return local.timetz().replace(tzinfo=None)


def in_session(candle: Candle, spec) -> bool:
    """S_t for one configured window. Windows that cross midnight are split."""
    start = clock_time.fromisoformat(str(spec.start))
    end = clock_time.fromisoformat(str(spec.end))
    t = candle_local_time(candle, spec)
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def in_any_session(candle: Candle, params=None, names: Sequence[str] | None = None) -> bool:
    params = params or load_params()
    for name in (names if names is not None else session_names(params)):
        if in_session(candle, params.ict.sessions[name]):
            return True
    return False


def session_blocks(
    candles: Sequence[Candle],
    params=None,
    *,
    session: str = "london",
    stop_index: int | None = None,
) -> list[tuple[int, int]]:
    """Maximal runs of candles inside the window, as (start_index, end_index) pairs."""
    params = params or load_params()
    spec = params.ict.sessions[session]
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for i, candle in enumerate(candles):
        if stop_index is not None and i > stop_index:
            break
        inside = in_session(candle, spec)
        if inside and start is None:
            start = i
        elif not inside and start is not None:
            blocks.append((start, i - 1))
            start = None
    if start is not None:
        blocks.append((start, min(len(candles) - 1, stop_index if stop_index is not None else len(candles) - 1)))
    return blocks


# ---------------------------------------------------- range, EQ and OTE


def dealing_range_eq(foundation: Foundation, index: int) -> dict | None:
    """The frozen range, EQ and the premium/discount read at `index`."""
    from engine.modules.liquidity import dealing_range
    rng = dealing_range(foundation, index)
    if rng is None:
        return None
    low, high = rng
    eq = (high + low) / 2.0
    close = foundation.candles[index].close
    zone = "discount" if close < eq else ("premium" if close > eq else "at_eq")
    return {"low": low, "high": high, "eq": eq, "close": close, "zone": zone}


def ote_zone(low_anchor: float, high_anchor: float, params=None) -> tuple[float, float]:
    """The OTE band of a bullish impulse leg, per the spec formula."""
    params = params or load_params()
    lo = float(params.ict.ote_low)
    hi = float(params.ict.ote_high)
    span = high_anchor - low_anchor
    return high_anchor - hi * span, high_anchor - lo * span


def ote_bracketing(foundation: Foundation, index: int) -> tuple[float, float] | None:
    """Anchors for OTE: latest confirmed swing low before `index`, and the highest high
    between that low and `index` (our explicit anchor rule, DECISIONS.md D-012)."""
    lows = [pv for pv in foundation.pivots if pv.kind == "low" and pv.known_index <= index - 1]
    if not lows:
        return None
    low = max(lows, key=lambda pv: (pv.index, pv.known_index))
    highs = [candles.high for candles in foundation.candles[low.index: index + 1]]
    if not highs:
        return None
    return low.price, max(highs)


# --------------------------------------------------------- Judas swing


@dataclass(frozen=True)
class JudasSwing:
    side: str                 # "sell_side": swept below the open; "buy_side": above
    session: str
    session_start: int
    session_open: float
    pierce_index: int
    reclaim_index: int
    depth_atr: float

    @property
    def known_index(self) -> int:
        return self.reclaim_index

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "session": self.session,
            "session_start": self.session_start,
            "session_open": self.session_open,
            "pierce_index": self.pierce_index,
            "reclaim_index": self.reclaim_index,
            "known_index": self.known_index,
            "depth_atr": self.depth_atr,
        }


def detect_judas_swing(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    session: str = "london",
    stop_index: int | None = None,
) -> list[JudasSwing]:
    """Early excursion beyond the session open that is then reversed, per the spec."""
    params = params or load_params()
    ict = params.ict
    w = int(ict.judas_window_candles)
    eps_atr = float(ict.judas_buffer_atr)
    out: list[JudasSwing] = []
    for start, end in session_blocks(candles, params, session=session, stop_index=stop_index):
        if start + 1 > end:
            continue
        open_ = candles[start].open
        atr = foundation.atr_at(start)
        if atr is None or atr <= 0:
            continue
        eps = eps_atr * atr
        made = {"sell_side": False, "buy_side": False}
        for t in range(start, min(start + w, end) + 1):
            candle = candles[t]
            if not made["sell_side"] and candle.low < open_ - eps:
                for j in range(t + 1, end + 1):
                    if candles[j].close > open_:
                        made["sell_side"] = True
                        out.append(JudasSwing("sell_side", session, start, open_, t, j,
                                              (open_ - candle.low) / atr))
                        break
            if not made["buy_side"] and candle.high > open_ + eps:
                for j in range(t + 1, end + 1):
                    if candles[j].close < open_:
                        made["buy_side"] = True
                        out.append(JudasSwing("buy_side", session, start, open_, t, j,
                                              (candle.high - open_) / atr))
                        break
            if made["sell_side"] and made["buy_side"]:
                break
    return out


# ------------------------------------------------------- power of three


@dataclass(frozen=True)
class PowerOfThree:
    session: str
    session_start: int
    accumulation_start: int
    accumulation_end: int
    accumulation_high: float
    accumulation_low: float
    manipulation_index: int
    distribution_index: int
    direction: str              # direction of the distribution leg
    distribution_body_atr: float

    @property
    def known_index(self) -> int:
        return self.distribution_index

    def to_dict(self) -> dict:
        return {
            "session": self.session,
            "session_start": self.session_start,
            "accumulation_start": self.accumulation_start,
            "accumulation_end": self.accumulation_end,
            "accumulation_high": self.accumulation_high,
            "accumulation_low": self.accumulation_low,
            "manipulation_index": self.manipulation_index,
            "distribution_index": self.distribution_index,
            "known_index": self.known_index,
            "direction": self.direction,
            "distribution_body_atr": self.distribution_body_atr,
        }


def detect_power_of_three(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    session: str = "london",
    stop_index: int | None = None,
) -> list[PowerOfThree]:
    """Accumulation, then manipulation, then distribution, in that order or not at all."""
    from engine.modules.smc import displacement

    params = params or load_params()
    ict = params.ict
    smc = params.smc
    n_acc = int(ict.po3_accumulation_candles)
    max_width = float(ict.po3_accumulation_max_width_atr)
    eps_atr = float(ict.judas_buffer_atr)
    alpha = float(smc.displacement_body_atr)
    q = float(smc.displacement_close_ratio)
    out: list[PowerOfThree] = []

    for start, end in session_blocks(candles, params, session=session, stop_index=stop_index):
        acc_end = start + n_acc - 1
        if acc_end >= end:
            continue
        atr = foundation.atr_at(acc_end)
        if atr is None or atr <= 0:
            continue
        acc = candles[start: acc_end + 1]
        high = max(c.high for c in acc)
        low = min(c.low for c in acc)
        if (high - low) >= max_width * atr:
            continue
        eps = eps_atr * atr
        manipulation = None
        direction = None
        for t in range(acc_end + 1, end + 1):
            if candles[t].low < low - eps:
                manipulation, direction = t, LONG
                break
            if candles[t].high > high + eps:
                manipulation, direction = t, SHORT
                break
        if manipulation is None:
            continue
        distribution = None
        for t in range(manipulation, end + 1):
            d = displacement(candles[t], foundation.atr_at(t) or 0.0, alpha, q)
            if d is None:
                continue
            if direction == LONG and d[0] == LONG and candles[t].close > low:
                distribution = (t, d[1])
                break
            if direction == SHORT and d[0] == SHORT and candles[t].close < high:
                distribution = (t, d[1])
                break
        if distribution is None:
            continue
        out.append(PowerOfThree(session, start, start, acc_end, high, low,
                                manipulation, distribution[0], direction, distribution[1]))
    return out


# ------------------------------------------------------- silver bullet


@dataclass(frozen=True)
class SilverBullet:
    direction: str
    fvg_index: int
    lower: float
    upper: float
    grab_index: int | None
    session: str

    @property
    def known_index(self) -> int:
        return self.fvg_index

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "fvg_index": self.fvg_index,
            "known_index": self.known_index,
            "lower": self.lower,
            "upper": self.upper,
            "grab_index": self.grab_index,
            "session": self.session,
        }


def detect_silver_bullet(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    fvgs: Sequence | None = None,
    grabs: Sequence | None = None,
    stop_index: int | None = None,
) -> list[SilverBullet]:
    """Fair value gaps formed inside the Silver Bullet window after an opposite-side sweep."""
    from engine.modules.smc import detect_fvgs

    params = params or load_params()
    ict = params.ict
    spec = ict.sessions["silver_bullet"]
    window = int(ict.sweep_window_candles)
    fvgs = list(fvgs) if fvgs is not None else detect_fvgs(candles, foundation, params,
                                                           stop_index=stop_index)
    grabs = list(grabs) if grabs is not None else []
    out: list[SilverBullet] = []
    for fvg in fvgs:
        if stop_index is not None and fvg.known_index > stop_index:
            break
        candle = candles[fvg.known_index]
        if not in_session(candle, spec):
            continue
        need = "sell_side" if fvg.direction == LONG else "buy_side"
        prior = [g for g in grabs if g.side.value == need
                 and fvg.known_index - window <= g.known_index <= fvg.known_index]
        if not prior:
            continue
        out.append(SilverBullet(fvg.direction, fvg.known_index, fvg.lower, fvg.upper,
                                max(g.known_index for g in prior), "silver_bullet"))
    return out


# ------------------------------------------------------ entry model v1


def detect_entry_model_v1(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    pools: Sequence | None = None,
    grabs: Sequence | None = None,
    mss_events: Sequence | None = None,
    fvgs: Sequence | None = None,
    stop_index: int | None = None,
    session_names_used: Sequence[str] | None = None,
) -> list[ModuleSignal]:
    """Long model: SellSideGrab -> MSS+ -> FVG+ and P < EQ and session, order enforced."""
    params = params or load_params()
    ict = params.ict
    window = int(ict.mss_window_candles)
    expiry = int(ict.entry_model_expiry_candles)
    cancel_mode = str(ict.cancellation)

    from engine.modules.liquidity import Side, nearest_pool
    from engine.modules.smc import detect_fvgs, detect_mss

    grabs = list(grabs) if grabs is not None else []
    pools = list(pools) if pools is not None else []
    mss_events = list(mss_events) if mss_events is not None else detect_mss(
        candles, foundation, params, grabs=grabs, stop_index=stop_index)
    fvgs = list(fvgs) if fvgs is not None else detect_fvgs(candles, foundation, params,
                                                           stop_index=stop_index)

    signals: list[ModuleSignal] = []
    for mss in mss_events:
        if mss.direction != LONG:
            continue
        t2 = mss.index
        if stop_index is not None and t2 > stop_index:
            break
        candidates = [g for g in grabs
                      if g.side is Side.SELL and g.pool_active
                      and t2 - window <= g.known_index < t2]
        if not candidates:
            continue
        # one setup per MSS, stop measured from the deepest sweep in the chain
        grab = min(candidates, key=lambda g: g.extreme)
        t0 = grab.known_index
        entry_index = None
        chosen = None
        for t in range(t2 + 1, min(t2 + expiry, len(candles) - 1) + 1):
            if stop_index is not None and t > stop_index:
                break
            atr = foundation.atr_at(t)
            if atr is None or atr <= 0:
                continue
            if cancel_mode == "close_beyond_swept_extreme":
                window_candles = candles[t2 + 1: t]
                if any(c.close < grab.extreme for c in window_candles):
                    break
            if not in_any_session(candles[t], params, session_names_used):
                continue
            rng = dealing_range_eq(foundation, t)
            if rng is None or not (candles[t].close < rng["eq"]):
                continue
            for fvg in fvgs:
                if fvg.direction != LONG or not (t2 <= fvg.known_index < t):
                    continue
                if candles[t].low <= fvg.upper:
                    entry_index, chosen = t, fvg
                    break
            if entry_index is not None:
                break
        if entry_index is None or chosen is None:
            continue
        atr = foundation.atr_at(entry_index)
        if atr is None or atr <= 0:
            continue
        entry = chosen.upper
        stop = grab.extreme - 0.1 * atr
        pool = nearest_pool(pools, foundation, entry_index, above=True)
        if pool is None or pool.level <= entry or stop >= entry:
            continue
        target = pool.level
        rng = dealing_range_eq(foundation, entry_index)
        rr = (target - entry) / (entry - stop)
        signals.append(make_signal(
            module=MODULE, version=VERSION,
            symbol=candles[entry_index].symbol, timeframe=candles[entry_index].timeframe,
            direction=Direction.LONG, entry=entry, stop=stop, target=target,
            candle=candles[entry_index], known_index=entry_index, candles=list(candles),
            formation_index=t0,
            reasons=[
                f"sell-side grab at candle {t0} below the active pool at {grab.pool_level:.5f}",
                f"bullish MSS at candle {t2}, within {window} candles of the grab, "
                f"event order enforced",
                f"bullish fair value gap [{chosen.lower:.5f}, {chosen.upper:.5f}] formed "
                f"at candle {chosen.known_index} in the MSS leg",
                f"entry candle {entry_index} trades into the gap while price is in "
                f"discount (P {candles[entry_index].close:.5f} < EQ "
                f"{rng['eq']:.5f}) and inside a configured session",
            ],
            invalidation=[
                f"a close below the swept low {grab.extreme:.5f} cancels the setup",
                f"the setup expires {expiry} candles after the MSS if the retrace does "
                f"not arrive",
                f"a close beyond the target {target:.5f} before entry voids the target",
            ],
            features={
                "grab_depth_atr": grab.depth_atr,
                "grab_rejection": grab.rejection_ratio,
                "pool_size": float(grab.pool_size),
                "candles_grab_to_mss": float(t2 - t0),
                "candles_mss_to_entry": float(entry_index - t2),
                "fvg_width_atr": chosen.width_atr,
                "eq_distance_atr": (rng["eq"] - candles[entry_index].close) / atr,
                "reward_risk": rr,
                "displacement_body_atr": mss.displacement_body_atr,
            },
            meta={"grab": grab.to_dict(), "mss": mss.to_dict(), "fvg": chosen.to_dict(),
                  "pool": pool.to_dict(), "range": rng},
        ))
    return signals
