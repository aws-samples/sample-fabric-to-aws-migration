"""Shared data models for fabric-assess — normative dataclasses and enums.

Ported from aws-samples/sample-bigquery-to-aws-migration (bq_assess.models),
re-flavored for a Microsoft Fabric Warehouse source. The two-axis assessment
contract is unchanged:

- Migration Effort (AUTO / ASSISTED / MANUAL) — moving a table into the Storage
  Target (Amazon S3 Tables / Apache Iceberg). Tables only.
- Query Complexity (PORTABLE / ADAPT / REWRITE) — keeping the existing T-SQL
  running on the Query Engine (Amazon Redshift Serverless or Amazon Athena).

Key inversion vs the BigQuery reference: Fabric already stores data as open
Delta/Parquet in OneLake (auto-virtualizable as Iceberg), so most tables trend
toward AUTO on the Effort axis and the real work is the T-SQL rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ---- Enums -----------------------------------------------------------------

class EntityType(Enum):
    TABLE = "TABLE"
    EXTERNAL = "EXTERNAL"          # treated as Table (moves)
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    ROUTINE = "ROUTINE"            # T-SQL function / stored procedure


class EntityPopulation(Enum):
    TABLE = "TABLE"                # scored on both axes
    REBUILT = "REBUILT"           # view/mv/proc/function — Query Complexity only, Effort = 0


class EffortCategory(Enum):
    AUTO = "AUTO"
    ASSISTED = "ASSISTED"
    MANUAL = "MANUAL"


class ComplexityCategory(Enum):
    PORTABLE = "PORTABLE"
    ADAPT = "ADAPT"
    REWRITE = "REWRITE"


class ConfidenceLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConfidenceSource(Enum):
    QUERY_INSIGHTS = "query_insights"   # queryinsights.exec_requests_history
    VIEW_DEFINITION = "view_definition"  # sys.sql_modules
    NAMING_HEURISTIC = "naming_heuristic"
    SCHEMA_ONLY = "schema_only"
    MANUAL_INPUT = "manual_input"
    SAFE_DEFAULT = "safe_default"


class TargetEngine(str, Enum):
    ATHENA = "athena"
    REDSHIFT = "redshift"


class StorageTarget(str, Enum):
    ICEBERG = "iceberg"   # S3 Tables (open, multi-engine) — default
    RMS = "rms"           # Redshift Managed Storage (native, engine-local)


class FabricCapacityModel(Enum):
    """How the Fabric source is billed. VERIFY unit terminology before publish."""
    CAPACITY = "CAPACITY"   # F-SKU capacity (CU-based), the normal Fabric model
    UNKNOWN = "UNKNOWN"


class MigrationStrategy(str, Enum):
    """The Fabric-specific effort decision the inverted axis surfaces.

    Because OneLake data is already open Delta/Iceberg, moving it is not a
    proprietary export; it is a choice between querying in place and copying.
    """
    IN_PLACE = "in_place"     # query OneLake Iceberg surface from AWS (VERIFY cross-cloud)
    COPY_TO_S3 = "copy_to_s3"  # physically copy into Amazon S3 Tables


# ---- Scanned metadata ------------------------------------------------------

@dataclass
class ColumnSchema:
    name: str
    field_type: str   # T-SQL / Delta type name
    nullable: bool
    fields: list[ColumnSchema] = field(default_factory=list)  # nested (rare in Fabric WH)


@dataclass
class RoutineMetadata:
    """T-SQL function or stored procedure, from sys.sql_modules + sys.objects."""
    name: str
    language: str        # SQL | CLR | ...
    arguments: list[str]
    body: str            # sys.sql_modules.definition
    routine_type: str    # SCALAR_FUNCTION | TABLE_FUNCTION | PROCEDURE | ...


@dataclass
class EntityMetadata:
    entity_id: str
    schema_name: str                 # Fabric WH schema (e.g. dbo)
    full_name: str                   # "schema.entity" — shared cross-file key
    entity_type: EntityType
    population: EntityPopulation
    num_rows: int                    # 0 for views/procs/functions
    num_bytes: int                   # OneLake Delta footprint (approx)
    columns: list[ColumnSchema]
    clustering_fields: list[str] | None   # Fabric data clustering (preview)
    view_query: str | None           # views (sys.sql_modules)
    mview_query: str | None          # materialized views
    routine: RoutineMetadata | None  # functions / procs
    depends_on: list[str]            # FQNs this entity references
    last_modified: datetime
    physical_bytes: int | None = None


# ---- Conversion / scoring results -----------------------------------------

@dataclass
class LossyCast:
    """A T-SQL/Delta type with no clean Iceberg equivalent — human-reviewed."""
    column: str
    source_type: str
    iceberg_type: str
    loss_description: str


@dataclass
class ConversionResult:
    ddl: str                         # Iceberg CREATE TABLE; "" for non-Tables
    lossy_casts: list[LossyCast]
    warnings: list[str]
    success: bool


@dataclass
class EffortResult:
    category: EffortCategory
    score: int
    flags: list[str]
    reasoning: str
    confidence: ConfidenceLevel
    # Fabric-specific: the in-place-vs-copy recommendation for this table.
    strategy: MigrationStrategy = MigrationStrategy.COPY_TO_S3


@dataclass
class DetectedConstruct:
    construct_class: str   # OPENJSON | CROSS_APPLY | STRING_AGG | MERGE | TOP | PIVOT | PROC | CLR | ...
    snippet: str           # anonymized
    description: str


@dataclass
class ComplexityResult:
    category: ComplexityCategory
    score: int
    constructs: list[DetectedConstruct]
    flags: list[str]
    reasoning: str
    confidence: ConfidenceLevel
    confidence_source: ConfidenceSource


@dataclass
class TranslationResult:
    """Best-effort T-SQL -> target-engine SQL translation (via sqlglot)."""
    translated_sql: str
    confidence: str        # "HIGH" | "MEDIUM" | "LOW"
    warnings: list[str]
    target_engine: str = "redshift"   # "redshift" | "athena"


# ---- Cost ------------------------------------------------------------------

@dataclass
class CostLine:
    label: str
    monthly: float | None
    monthly_low: float | None
    monthly_high: float | None
    confidence: ConfidenceLevel
    source_note: str


@dataclass
class WorkloadProfile:
    """Workload metrics from queryinsights.exec_requests_history, used for AWS
    engine sizing + justification. VERIFY the CPU-time -> RPU bridge."""
    has_data: bool = False
    total_stored_gb: float = 0.0
    total_queries: int = 0
    days_sampled: int = 0
    lookback_days: int = 30
    queries_per_day: float = 0.0
    avg_concurrent_queries: float = 0.0
    peak_concurrent_queries: float = 0.0
    monthly_scanned_tb: float = 0.0
    total_cpu_time_ms: int = 0        # sum(allocated_cpu_time_ms)
    active_hour_fraction: float = 0.0


@dataclass
class CostComparison:
    fabric_capacity_model: FabricCapacityModel
    fabric_monthly: float
    fabric_breakdown: list[CostLine]
    aws_lines: list[CostLine]
    aws_monthly_low: float
    aws_monthly_high: float
    monthly_delta_low: float
    monthly_delta_high: float
    annual_savings_low: float
    annual_savings_high: float
    migration_onetime: float
    breakeven_months_low: float
    breakeven_months_high: float
    compute_confidence: ConfidenceLevel
    fabric_pricing_region: str = "unknown"
    aws_pricing_region: str = "us-east-1"
    scope_notes: list[str] = field(default_factory=list)
    pricing_notes: list[str] = field(default_factory=list)


# ---- Report ----------------------------------------------------------------

@dataclass
class FailureRecord:
    entity_name: str
    stage: str   # scan | convert | detect | score
    error: str


@dataclass
class EntityReport:
    full_name: str
    entity_type: EntityType
    population: EntityPopulation
    rows: int
    size_gb: float
    depends_on: list[str]
    effort: EffortResult | None
    conversion: ConversionResult | None = None
    complexity: ComplexityResult | None = None
    rewrite_guidance: list[str] = field(default_factory=list)
    translated_sql: TranslationResult | None = None
    physical_bytes: int | None = None


@dataclass
class AssessmentSummary:
    total_entities: int
    total_tables: int
    total_size_gb: float
    effort_counts: dict[str, int]       # {"AUTO": n, "ASSISTED": n, "MANUAL": n}
    complexity_counts: dict[str, int]   # {"PORTABLE": n, "ADAPT": n, "REWRITE": n}
    sql_surface_confidence: ConfidenceLevel
    total_logical_size_gb: float = 0.0
    workload_constructs: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    assessment_id: str   # "assess-{date}-{hash}"
    generated_at: datetime
    workspace: str       # Fabric workspace / warehouse identifier
    summary: AssessmentSummary
    cost: CostComparison
    entities: list[EntityReport]
    failures: list[FailureRecord]
