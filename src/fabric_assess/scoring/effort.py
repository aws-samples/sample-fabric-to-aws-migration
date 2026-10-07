"""Migration Effort scorer — Tables only.

The Fabric inversion: because OneLake already stores data as open Delta/Parquet
(auto-virtualizable as Iceberg), moving a table is not a proprietary export. Most
tables trend toward AUTO. The dominant residual factors are lossy type casts,
ongoing-sync need, and the in-place-vs-copy strategy decision; raw data volume
contributes far less than it does for a proprietary-format source.
"""
from __future__ import annotations

from fabric_assess.core.units import bytes_to_gb, fmt_size
from fabric_assess.models import (
    ConfidenceLevel,
    ConversionResult,
    EffortCategory,
    EffortResult,
    EntityMetadata,
    MigrationStrategy,
)

_ONE_GB = 1024**3
# Volume thresholds are higher than a proprietary-export source: open-format
# bytes already in OneLake do not need re-encoding, only staging to copy.
_LARGE_THRESHOLD = 100 * _ONE_GB
_HUGE_THRESHOLD = 1024 * _ONE_GB  # 1 TB

_SYNC_SIGNALS = {"updated_at", "modified_at", "ingestion_time", "last_modified", "_rowstamp"}


def _category_for(points: int) -> EffortCategory:
    """Single points->category ladder shared by scoring and re-scoring."""
    if points == 0:
        return EffortCategory.AUTO
    if points <= 2:
        return EffortCategory.ASSISTED
    return EffortCategory.MANUAL


class EffortScorer:
    """Score migration effort for TABLE-population entities."""

    def score(self, entity: EntityMetadata, conversion: ConversionResult) -> EffortResult:
        if not conversion.success:
            return EffortResult(
                category=EffortCategory.MANUAL,
                score=99,
                flags=["conversion_failed"],
                reasoning="Iceberg DDL conversion failed — manual migration design required.",
                confidence=ConfidenceLevel.HIGH,
                strategy=MigrationStrategy.COPY_TO_S3,
            )

        points = 0
        flags: list[str] = []
        reasons: list[str] = []

        size_bytes = entity.num_bytes or 0

        # Data-volume tier. For an open-format source this is a secondary signal:
        # it only drives the in-place-vs-copy strategy and staging effort, not a
        # re-encode. Hence higher thresholds and lower weight than a BQ source.
        if size_bytes >= _HUGE_THRESHOLD:
            points += 1
            flags.append("data_volume_huge")
            reasons.append(
                f"huge volume ({fmt_size(bytes_to_gb(size_bytes))}) — "
                f"favor in-place query or partitioned copy (+1)"
            )
        elif size_bytes >= _LARGE_THRESHOLD:
            flags.append("data_volume_large")
            reasons.append(
                f"large volume ({fmt_size(bytes_to_gb(size_bytes))}) — "
                f"open-format staged copy, low effort"
            )

        # Lossy casts — T-SQL/Delta types with no clean Iceberg equivalent.
        n_lossy = len(conversion.lossy_casts)
        if n_lossy > 0:
            points += n_lossy
            flags.append("lossy_casts")
            reasons.append(f"{n_lossy} lossy cast(s) — manual type review (+{n_lossy})")

        # Ongoing-sync need — a recurring MERGE/incremental load.
        if self._has_sync_signal(entity):
            points += 1
            flags.append("ongoing_sync")
            reasons.append("ongoing-sync signal detected — recurring incremental load (+1)")

        category = _category_for(points)
        strategy = self._strategy_for(size_bytes, n_lossy)

        confidence = ConfidenceLevel.LOW if entity.num_bytes is None else ConfidenceLevel.HIGH

        return EffortResult(
            category=category,
            score=points,
            flags=flags,
            reasoning="; ".join(reasons)
            if reasons
            else "Open-format source already Iceberg-compatible — fully automatable.",
            confidence=confidence,
            strategy=strategy,
        )

    def _has_sync_signal(self, entity: EntityMetadata) -> bool:
        for col in entity.columns:
            if col.name and col.name.lower() in _SYNC_SIGNALS:
                return True
        return False

    def _strategy_for(self, size_bytes: int, n_lossy: int) -> MigrationStrategy:
        """Recommend in-place query vs physical copy.

        A clean, large table is the strongest candidate to query in place from
        the OneLake Iceberg surface (no copy, no egress to re-encode). A table
        needing lossy type remediation must be copied so the cast is materialized.
        """
        if n_lossy > 0:
            return MigrationStrategy.COPY_TO_S3
        if size_bytes >= _LARGE_THRESHOLD:
            return MigrationStrategy.IN_PLACE
        return MigrationStrategy.COPY_TO_S3
