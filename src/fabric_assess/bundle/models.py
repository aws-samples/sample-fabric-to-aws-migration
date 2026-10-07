"""Bundle data model — the typed collect-then-report hand-off artifact."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from fabric_assess.models import EntityMetadata, FailureRecord, WorkloadProfile

SCHEMA_VERSION = 1
COMPATIBLE_SCHEMA_VERSIONS = frozenset({1})


def sha256_file(path: str | Path) -> str:
    """Checksum a file — the bundle's integrity contract, shared by writer + loader."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class QueryRecord:
    """One anonymized query + per-request stats (queries.jsonl row)."""
    query: str
    total_elapsed_time_ms: int = 0
    allocated_cpu_time_ms: int = 0
    data_scanned_remote_storage_mb: float | None = None
    statement_type: str | None = None
    submit_time: str | None = None


@dataclass
class Bundle:
    """The complete hand-off artifact between collector and report generator."""
    workspace: str
    warehouse: str
    aws_region: str
    entities: list[EntityMetadata]
    failures: list[FailureRecord] = field(default_factory=list)
    workload: WorkloadProfile | None = None
    queries: list[QueryRecord] | None = None
    collector_version: str = ""
    created_at: str = ""
