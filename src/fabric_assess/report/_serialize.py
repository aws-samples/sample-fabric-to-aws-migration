"""Serialize an Assessment to a plain JSON-ready dict.

Centralizes the dataclass/enum -> dict rules so the JSON writer and the HTML
writer render from the same structure. No credential or raw-literal material
reaches here: SQL surfaces were anonymized at scan time, and this layer only
reshapes already-safe fields.
"""
from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any

from fabric_assess.models import Assessment


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def assessment_to_dict(assessment: Assessment) -> dict[str, Any]:
    """Convert an Assessment (nested dataclasses + enums) to a JSON-ready dict."""
    return _plain(asdict(assessment))
