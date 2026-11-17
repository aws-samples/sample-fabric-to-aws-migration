"""Analyzer — the pure-computation pipeline that turns scanned metadata into an
Assessment. Everything here is offline and deterministic, so it runs identically
against a live scan or a loaded bundle.

Stages: SQL-surface detection -> Iceberg conversion -> effort scoring (tables)
-> complexity scoring (SQL-surface entities) -> rewrite guidance -> cost estimate
-> summary assembly.
"""
from __future__ import annotations

import hashlib
import re
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
        query_log_text: list[str] | None = None,
        fabric_monthly: float = 0.0,
        storage_gb: float = 0.0,
        aws_region: str = "us-east-1",
    ) -> Assessment:
        failures = list(failures or [])
        constructs_by_entity = self._surface.detect_for_entities(
            entities, query_log_text
        )
        has_logs = bool(workload and workload.has_data)
        ad_hoc = constructs_by_entity.get("__ad_hoc__", [])
        # SQL that reads tables: view and routine bodies plus query history.
        # Used to give each table a Query Complexity score from the SQL that
        # references it (name match, a heuristic the report labels).
        referencing_sql = [
            sql
            for e in entities
            for sql in (e.view_query, e.mview_query, e.routine.body if e.routine else None)
            if sql
        ] + [q for q in (query_log_text or []) if q]

        reports: list[EntityReport] = []
        effort_counts = {c.value: 0 for c in EffortCategory}
        complexity_counts = {c.value: 0 for c in ComplexityCategory}
        total_bytes = 0
        surface_confidences: list[ConfidenceLevel] = []

        for entity in entities:
            try:
                constructs = constructs_by_entity.get(entity.full_name, [])
                attributed = 0
                if entity.population == EntityPopulation.TABLE:
                    constructs, attributed = self._attribute(entity, referencing_sql)
                report = self._analyze_entity(entity, constructs, has_logs, attributed)
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
        # The Fabric SQL endpoint does not report table storage size, so the
        # caller can supply the OneLake footprint for the storage estimate.
        cost_gb = total_gb or storage_gb
        aws_lines = athena_cost.estimate_athena_monthly(workload, cost_gb)
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
            workload_constructs=[c.construct_class for c in ad_hoc],
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

    def _attribute(self, entity: EntityMetadata, sqls: list[str]):
        """Constructs found in SQL that references *entity* by name."""
        name = entity.full_name.split(".", 1)[-1]
        pattern = re.compile(
            r"(?<![\w\]])\[?" + re.escape(name) + r"\]?(?![\w\[])", re.IGNORECASE
        )
        found: list = []
        seen: set[str] = set()
        hits = 0
        for sql in sqls:
            if not pattern.search(sql):
                continue
            hits += 1
            for c in self._surface.detect(sql):
                if c.construct_class not in seen:
                    seen.add(c.construct_class)
                    found.append(c)
        return found, hits

    def _analyze_entity(self, entity, constructs, has_logs, attributed=0) -> EntityReport:
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
        if constructs or has_surface or attributed:
            complexity = self._complexity.score(
                entity, constructs, has_logs=has_logs,
                has_attributed_queries=bool(attributed),
            )
            if attributed:
                detail = (
                    complexity.reasoning if constructs
                    else "no T-SQL-specific constructs detected"
                )
                complexity.reasoning = (
                    f"from {attributed} view, routine or query text(s) that name "
                    f"this table: {detail}"
                )
            classes = [c.construct_class for c in constructs]
            rewrite_guidance = athena_rewrite.guidance_for(classes)
            # Translate the entity's own SQL surface if present.
            primary_sql = (
                entity.view_query
                or entity.mview_query
                or (entity.routine.body if entity.routine else None)
            )
            if primary_sql:
                # Anonymize first so literals (keys, passwords, ids) never reach the report.
                translated = athena_rewrite.translate(
                    self._surface.anonymize(primary_sql)
                )

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
