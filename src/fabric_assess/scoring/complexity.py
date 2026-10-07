"""Query Complexity scorer — two-axis model.

Scores every entity with a SQL surface on detected T-SQL-specific constructs.
Categories: PORTABLE / ADAPT / REWRITE. Confidence ladder: LOW (no surface) ->
MEDIUM (auto-captured view/proc defs) -> HIGH (workload history).
"""
from __future__ import annotations

from fabric_assess.models import (
    ComplexityCategory,
    ComplexityResult,
    ConfidenceLevel,
    ConfidenceSource,
    DetectedConstruct,
    EntityMetadata,
)

# Per-class weights. Constructs with no clean target-engine equivalent weigh most.
_WEIGHTS: dict[str, int] = {
    "CLR": 4,
    "PROC": 3,
    "OPENJSON": 2,
    "CROSS_APPLY": 2,
    "MERGE": 2,
    "PIVOT": 2,
    "STRING_AGG": 1,
    "TOP": 1,
    "FUNCTION_DRIFT": 1,
}


class ComplexityScorer:
    """Score query complexity for entities with a SQL surface."""

    @staticmethod
    def build_dep_counts(relationships) -> dict[str, int]:
        """Pre-compute dependent counts from relationships (O(R) once)."""
        counts: dict[str, int] = {}
        if relationships is None:
            return counts
        for r in getattr(relationships, "relationships", []):
            tgt = getattr(r, "target_table", None)
            if tgt:
                counts[tgt] = counts.get(tgt, 0) + 1
        return counts

    def score(
        self,
        entity: EntityMetadata,
        constructs: list[DetectedConstruct],
        relationships=None,
        has_logs: bool = False,
        dep_counts: dict[str, int] | None = None,
        has_attributed_queries: bool = False,
    ) -> ComplexityResult:
        """Score query complexity based on detected constructs."""
        points = 0
        flags: list[str] = []
        reasons: list[str] = []

        seen_classes: set[str] = set()
        for c in constructs:
            weight = _WEIGHTS.get(c.construct_class, 1)
            if c.construct_class not in seen_classes:
                points += weight
                reasons.append(f"{c.construct_class} (+{weight})")
                seen_classes.add(c.construct_class)
                flags.append(c.construct_class)

        # Hub entity bonus — O(1) lookup via pre-computed dict.
        if dep_counts is not None:
            dep_count = dep_counts.get(entity.full_name, 0)
            if dep_count >= 3:
                points += 1
                flags.append("hub_entity")
                reasons.append(f"hub entity ({dep_count} dependents) (+1)")

        if points == 0:
            category = ComplexityCategory.PORTABLE
        elif points <= 3:
            category = ComplexityCategory.ADAPT
        else:
            category = ComplexityCategory.REWRITE

        has_surface = bool(
            entity.view_query
            or entity.mview_query
            or (entity.routine and entity.routine.body)
        )
        if has_logs and has_surface:
            confidence = ConfidenceLevel.HIGH
            confidence_source = ConfidenceSource.QUERY_INSIGHTS
        elif has_attributed_queries:
            confidence = ConfidenceLevel.MEDIUM
            confidence_source = ConfidenceSource.QUERY_INSIGHTS
        elif has_surface:
            confidence = ConfidenceLevel.MEDIUM
            confidence_source = ConfidenceSource.VIEW_DEFINITION
        else:
            confidence = ConfidenceLevel.LOW
            confidence_source = ConfidenceSource.SCHEMA_ONLY

        reasoning = (
            "; ".join(reasons)
            if reasons
            else (
                "no SQL surface visible — cannot assess query complexity"
                if not has_surface
                else "no T-SQL-specific constructs detected"
            )
        )

        return ComplexityResult(
            category=category,
            score=points,
            constructs=constructs,
            flags=flags,
            reasoning=reasoning,
            confidence=confidence,
            confidence_source=confidence_source,
        )
