"""Analyzer — the pure-computation pipeline that turns scanned metadata into an
Assessment. Mirrors the reference tool's ``analyze_and_report`` seam: everything
here is offline and deterministic, so it runs identically against a live scan or a
loaded bundle.

Stages: SQL-surface detection -> Iceberg conversion -> effort scoring (tables)
-> complexity scoring (SQL-surface entities) -> rewrite guidance -> cost estimate
-> summary assembly.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fabric_assess.core.sql_surface import SQLSurfaceAnalyzer
from fabric_assess.core.units import bytes_to_gb
from fabric_assess.engine.athena import cost as athena_cost
from fabric_assess.engine.athena import rewrite as athena_rewrite
from fabric_assess.models import (
    Assessment,
    AssessmentSummary,
    ComplexityCategory,
    ConfidenceLevel,
    EffortCategory,
    EntityMetadata,
    EntityPopulation,
    EntityReport,
    FailureRecord,
    WorkloadProfile,
)
from fabric_assess.scoring.complexity import ComplexityScorer
from fabric_assess.scoring.effort import EffortScorer
from fabric_assess.targets.iceberg.converter import IcebergConverter


class Analyzer:
    """Compose the assessment pipeline over a list of entities."""

    def __init__(self) -> None:
        self._surface = SQLSurfaceAnalyzer()
        self._converter = IcebergConverter()
        self._effort = EffortScorer()
        self._complexity = ComplexityScorer()

    def analyze(
        self,
        entities: list[EntityMetadata],
        *,
        workspace: str = "",
        failures: list[FailureRecord] | None = None,
        workload: WorkloadProfile | None = None,
        fabric_monthly: float = 0.0,
        aws_region: str = "us-east-1",
    ) -> Assessment:
        failures = list(failures or [])
        constructs_by_entity = self._surface.detect_for_entities(entities)
        has_logs = bool(workload and workload.has_data)

        reports: list[EntityReport] = []
        effort_counts = {c.value: 0 for c in EffortCategory}
        complexity_counts = {c.value: 0 for c in ComplexityCategory}
        total_bytes = 0
        surface_confidences: list[ConfidenceLevel] = []

        for entity in entities:
            try:
                report = self._analyze_entity(
                    entity, constructs_by_entity.get(entity.full_name, []), has_logs
                )
            except Exception as exc:
                failures.append(
                    FailureRecord(entity_name=entity.full_name, stage="score", error=str(exc))
                )
                continue

            reports.append(report)
            if report.effort is not None:
                effort_counts[report.effort.category.value] += 1
            if report.complexity is not None:
                complexity_counts[report.complexity.category.value] += 1
                surface_confidences.append(report.complexity.confidence)
            total_bytes += entity.num_bytes or 0

        total_gb = bytes_to_gb(total_bytes)
        aws_lines = athena_cost.estimate_athena_monthly(workload, total_gb)
        comparison = athena_cost.build_comparison(
            aws_lines, fabric_monthly=fabric_monthly, aws_region=aws_region
        )

        summary = AssessmentSummary(
            total_entities=len(reports),
            total_tables=sum(
                1 for r in reports if r.population == EntityPopulation.TABLE
            ),
            total_size_gb=round(total_gb, 2),
            effort_counts=effort_counts,
            complexity_counts=complexity_counts,
            sql_surface_confidence=self._aggregate_confidence(surface_confidences),
            total_logical_size_gb=round(total_gb, 2),
        )

        return Assessment(
            assessment_id=self._assessment_id(workspace),
            generated_at=datetime.now(timezone.utc),
            workspace=workspace,
            summary=summary,
            cost=comparison,
            entities=reports,
            failures=failures,
        )

    def _analyze_entity(self, entity, constructs, has_logs) -> EntityReport:
        conversion = None
        effort = None
        if entity.population == EntityPopulation.TABLE:
            conversion = self._converter.convert(entity)
            effort = self._effort.score(entity, conversion)

        complexity = None
        rewrite_guidance: list[str] = []
        translated = None
        has_surface = bool(
            entity.view_query or entity.mview_query or (entity.routine and entity.routine.body)
        )
        if constructs or has_surface:
            complexity = self._complexity.score(entity, constructs, has_logs=has_logs)
            classes = [c.construct_class for c in constructs]
            rewrite_guidance = athena_rewrite.guidance_for(classes)
            # Translate the primary SQL surface if present.
            primary_sql = (
                entity.view_query
                or entity.mview_query
                or (entity.routine.body if entity.routine else None)
            )
            if primary_sql:
                translated = athena_rewrite.translate(primary_sql)

        return EntityReport(
            full_name=entity.full_name,
            entity_type=entity.entity_type,
            population=entity.population,
            rows=entity.num_rows,
            size_gb=round(bytes_to_gb(entity.num_bytes), 4),
            depends_on=entity.depends_on,
            effort=effort,
            conversion=conversion,
            complexity=complexity,
            rewrite_guidance=rewrite_guidance,
            translated_sql=translated,
        )

    @staticmethod
    def _aggregate_confidence(levels: list[ConfidenceLevel]) -> ConfidenceLevel:
        if not levels:
            return ConfidenceLevel.LOW
        if any(x == ConfidenceLevel.HIGH for x in levels):
            return ConfidenceLevel.HIGH
        if any(x == ConfidenceLevel.MEDIUM for x in levels):
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    @staticmethod
    def _assessment_id(workspace: str) -> str:
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:8]
        return f"assess-{date}-{digest}"
