"""CRT: Candle Range Theory, one named variant of a practitioner model.

CRT has no published definition. The spec fixes the common range, sweep and reclaim
reading and says to name the variant; this is that reading, and nothing here claims the
range gets revisited. Every threshold is a named parameter.

Specification, CRT section:

    Reference range: one completed candle R on the reference timeframe (H4 or D1).
        H_R, L_R, M_R = (H_R + L_R) / 2, W_R = H_R - L_R
    Bearish CRT: a later candle t on the same timeframe sweeps above the range and
        closes back inside it:
            H_t > H_R + eps  and  L_R <= C_t <= H_R
        Bias is short. TP_1 = L_R, TP_0 = M_R, SL = H_t + 0.1 x ATR_t.
    Bullish CRT mirrors it: L_t < L_R - eps and L_R <= C_t <= H_R, TP_1 = H_R,
        SL = L_t - 0.1 x ATR_t.
    The signal is knowable at the close of candle t.
    Filters: skip ranges with W_R < 0.5 x ATR.
    Parameters to fix: the reference timeframe and which candle counts as R; whether the
    sweep and reclaim must be the same candle or may take up to n = 1 candles; wick or
    close sweep with eps = 0.05 x ATR; the expiry in candles.

This implementation uses the wick sweep with the close back inside (the formula above),
allows the reclaim either on the same candle or on the next candle (`sweep_reclaim_candles`
= 1), and knows a setup at the close of the reclaiming candle. Entering on that close is
variant `close_entry`; waiting for a lower-timeframe MSS is a separate variant that the
spec asks to test separately and that this module does not mix in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from engine.config import load_params
from engine.foundation import Foundation
from engine.models import Candle, Direction, ModuleSignal, Timeframe
from engine.modules.common import make_signal

MODULE = "crt"
VERSION = "1.0.0"

BULL = "bull"
BEAR = "bear"


@dataclass(frozen=True)
class CrtSetup:
    direction: str
    reference_index: int
    reference_high: float
    reference_low: float
    reference_mid: float
    width_atr: float
    pierce_index: int
    known_index: int
    variant: str                 # one_candle | reclaim_within_n
    sweep_extreme: float
    entry: float
    stop: float
    target_1: float
    target_0: float

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.target_1 - self.entry) / risk if risk > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "reference_index": self.reference_index,
            "reference_high": self.reference_high,
            "reference_low": self.reference_low,
            "reference_mid": self.reference_mid,
            "width_atr": self.width_atr,
            "pierce_index": self.pierce_index,
            "known_index": self.known_index,
            "variant": self.variant,
            "sweep_extreme": self.sweep_extreme,
            "entry": self.entry,
            "stop": self.stop,
            "target_1": self.target_1,
            "target_0": self.target_0,
        }


def interval_end(candle: Candle) -> datetime:
    """The moment a candle stops changing: its open time plus one timeframe."""
    return candle.open_time + timedelta(seconds=candle.timeframe.seconds)


def reference_before(reference_candles: Sequence[Candle], moment) -> Candle | None:
    """The latest reference candle that had fully closed before `moment`."""
    closed = [c for c in reference_candles if interval_end(c) <= moment]
    return max(closed, key=lambda c: c.open_time) if closed else None


def detect_crt(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    reference_candles: Sequence[Candle] | None = None,
    stop_index: int | None = None,
) -> list[CrtSetup]:
    """Range sweeps and reclaims on the trading timeframe, against completed references.

    With no `reference_candles`, the trading candles themselves are the reference set
    (relevant when the caller feeds H4 candles and wants the previous H4 candle as R).
    """
    params = params or load_params()
    crt = params.crt
    eps_atr = float(crt.buffer_atr)
    stop_buffer = float(crt.stop_buffer_atr)
    min_width_atr = float(crt.min_range_width_atr)
    reclaim_n = int(crt.sweep_reclaim_candles)
    references = list(reference_candles) if reference_candles is not None else list(candles)

    out: list[CrtSetup] = []
    for t, candle in enumerate(candles):
        if stop_index is not None and t > stop_index:
            break
        atr = foundation.atr_at(t)
        if atr is None or atr <= 0:
            continue
        ref = reference_before(references, candle.open_time)
        if ref is None or ref.timeframe.value != str(crt.reference_timeframe):
            if reference_candles is not None:
                continue
        if ref is None:
            continue
        width = ref.high - ref.low
        if width < min_width_atr * atr:
            continue
        mid = (ref.high + ref.low) / 2.0
        eps = eps_atr * atr
        # same candle: pierce and close back inside
        if candle.high > ref.high + eps and ref.low <= candle.close <= ref.high:
            out.append(CrtSetup(
                BEAR, _index_of(references, ref), ref.high, ref.low, mid,
                width / atr, t, t, "one_candle", candle.high, candle.close,
                candle.high + stop_buffer * atr, ref.low, mid))
            continue
        if candle.low < ref.low - eps and ref.low <= candle.close <= ref.high:
            out.append(CrtSetup(
                BULL, _index_of(references, ref), ref.high, ref.low, mid,
                width / atr, t, t, "one_candle", candle.low, candle.close,
                candle.low - stop_buffer * atr, ref.high, mid))
            continue
        # loose variant: the reclaim may come up to n candles later
        if reclaim_n >= 1:
            if candle.high > ref.high + eps and candle.close > ref.high:
                for j in range(t + 1, min(t + reclaim_n, len(candles) - 1) + 1):
                    if stop_index is not None and j > stop_index:
                        break
                    c2 = candles[j]
                    if ref.low <= c2.close <= ref.high:
                        atr_j = foundation.atr_at(j) or atr
                        out.append(CrtSetup(
                            BEAR, _index_of(references, ref), ref.high, ref.low, mid,
                            width / atr_j, t, j, "reclaim_within_n", candle.high, c2.close,
                            candle.high + stop_buffer * atr_j, ref.low, mid))
                        break
            elif candle.low < ref.low - eps and candle.close < ref.low:
                for j in range(t + 1, min(t + reclaim_n, len(candles) - 1) + 1):
                    if stop_index is not None and j > stop_index:
                        break
                    c2 = candles[j]
                    if ref.low <= c2.close <= ref.high:
                        atr_j = foundation.atr_at(j) or atr
                        out.append(CrtSetup(
                            BULL, _index_of(references, ref), ref.high, ref.low, mid,
                            width / atr_j, t, j, "reclaim_within_n", candle.low, c2.close,
                            candle.low - stop_buffer * atr_j, ref.high, mid))
                        break
    return out


def _index_of(references: Sequence[Candle], candle: Candle) -> int:
    for i, c in enumerate(references):
        if c is candle:
            return i
    return -1


def detect_crt_signals(
    candles: Sequence[Candle],
    foundation: Foundation,
    params=None,
    *,
    reference_candles: Sequence[Candle] | None = None,
    expiry_candles: int | None = None,
    stop_index: int | None = None,
) -> list[ModuleSignal]:
    """Turn each setup into the shared record, entering at the close of the reclaim."""
    params = params or load_params()
    expiry = int(expiry_candles if expiry_candles is not None else params.crt.expiry_candles)
    signals: list[ModuleSignal] = []
    for setup in detect_crt(candles, foundation, params,
                            reference_candles=reference_candles, stop_index=stop_index):
        candle = candles[setup.known_index]
        direction = Direction.SHORT if setup.direction == BEAR else Direction.LONG
        signals.append(make_signal(
            module=MODULE, version=VERSION,
            symbol=candle.symbol, timeframe=candle.timeframe,
            direction=direction, entry=setup.entry, stop=setup.stop, target=setup.target_1,
            candle=candle, known_index=setup.known_index, candles=list(candles),
            formation_index=setup.reference_index if setup.reference_index >= 0 else None,
            reasons=[
                f"reference candle {setup.reference_index} closed with range "
                f"[{setup.reference_low:.5f}, {setup.reference_high:.5f}], "
                f"{setup.width_atr:.2f} x ATR wide",
                f"candle {setup.pierce_index} swept beyond "
                f"{'the high' if setup.direction == BEAR else 'the low'} and the "
                f"{setup.variant.replace('_', ' ')} reclaimed the range at candle "
                f"{setup.known_index}",
                f"first target is the opposite side of the range ({setup.target_1:.5f}), "
                f"midpoint {setup.target_0:.5f} is the intermediate objective",
            ],
            invalidation=[
                f"the setup is cancelled if neither target nor stop is reached within "
                f"{expiry} candles",
                f"a close back beyond the swept extreme {setup.sweep_extreme:.5f} voids "
                f"the reading",
            ],
            features={
                "width_atr": setup.width_atr,
                "reward_risk": setup.reward_risk,
                "sweep_depth_atr": (setup.sweep_extreme - setup.reference_high)
                if setup.direction == BEAR else (setup.reference_low - setup.sweep_extreme),
                "midpoint_target_r": abs(setup.target_0 - setup.entry)
                / max(abs(setup.entry - setup.stop), 1e-12),
            },
            meta={"setup": setup.to_dict(), "entry_variant": "close_entry",
                  "expiry_candles": expiry},
        ))
    return signals