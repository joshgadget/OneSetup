"""Time-ordered splits and walk-forward windows.

The spec's protocol: never shuffle candles, fit on train, choose variants on
validation, touch the test period once, then walk forward by refitting on a rolling
window and trading the next window. Nothing here randomises anything.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    name: str
    start: int
    end: int          # exclusive

    @property
    def size(self) -> int:
        return self.end - self.start

    def contains(self, index: int) -> bool:
        return self.start <= index < self.end

    def to_dict(self) -> dict:
        return {"name": self.name, "start": self.start, "end": self.end, "size": self.size}


def time_split(
    n: int,
    *,
    train_fraction: float = 0.50,
    validation_fraction: float = 0.20,
    test_fraction: float = 0.30,
) -> dict[str, Split]:
    """Train, then validation, then test, in that time order and nothing shuffled."""
    if n < 3:
        raise ValueError("need at least three candles to split")
    total = train_fraction + validation_fraction + test_fraction
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"fractions must sum to 1, got {total}")
    a = int(round(n * train_fraction))
    b = int(round(n * (train_fraction + validation_fraction)))
    a = max(1, min(a, n - 2))
    b = max(a + 1, min(b, n - 1))
    return {
        "train": Split("train", 0, a),
        "validation": Split("validation", a, b),
        "test": Split("test", b, n),
    }


def walk_forward(n: int, *, windows: int = 4, train_fraction: float = 0.5) -> list[dict[str, Split]]:
    """Rolling refit: each fold fits on the window before it and trades the next window."""
    if windows < 1:
        raise ValueError("windows must be at least 1")
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be between 0 and 1")
    fold = n // windows
    if fold < 3:
        raise ValueError("series too short for this many windows")
    out: list[dict[str, Split]] = []
    for w in range(windows):
        start = w * fold
        end = n if w == windows - 1 else (w + 1) * fold
        size = end - start
        train_size = int(size * train_fraction)
        if train_size < 2 or size - train_size < 1:
            raise ValueError("fold too short to split")
        out.append({
            "train": Split(f"train_{w}", start, start + train_size),
            "test": Split(f"test_{w}", start + train_size, end),
        })
    return out
