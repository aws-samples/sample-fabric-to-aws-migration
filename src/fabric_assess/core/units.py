"""Byte/size unit helpers shared across scoring, cost, and report modules."""
from __future__ import annotations

_ONE_GB = 1024**3


def bytes_to_gb(num_bytes: int | None) -> float:
    """Convert a byte count to gibibytes. ``None`` is treated as 0."""
    if not num_bytes:
        return 0.0
    return num_bytes / _ONE_GB


def fmt_size(gb: float) -> str:
    """Format a GB figure for human-readable report/log output."""
    if gb >= 1024:
        return f"{gb / 1024:.1f} TB"
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{gb * 1024:.0f} MB"
