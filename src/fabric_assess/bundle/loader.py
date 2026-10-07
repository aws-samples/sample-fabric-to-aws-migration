"""Bundle loader — reads a bundle directory back into a Bundle, verifying checksums.

Checksum verification is the integrity contract: a bundle whose files do not match
the manifest's recorded sha256 is rejected, so an analyst never reports on a
tampered or truncated hand-off.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from fabric_assess.bundle.models import (
    COMPATIBLE_SCHEMA_VERSIONS,
    Bundle,
    QueryRecord,
    sha256_file,
)
from fabric_assess.models import (
    ColumnSchema,
    EntityMetadata,
    EntityPopulation,
    EntityType,
    FailureRecord,
    RoutineMetadata,
)


class BundleError(Exception):
    """Raised when a bundle is incompatible, incomplete, or fails checksum verification."""


class BundleLoader:
    """Load and verify a bundle written by BundleWriter."""

    def load(self, bundle_dir: str) -> Bundle:
        manifest = self._read_json(os.path.join(bundle_dir, "manifest.json"))
        version = manifest.get("schema_version")
        if version not in COMPATIBLE_SCHEMA_VERSIONS:
            raise BundleError(f"unsupported bundle schema_version: {version}")

        self._verify_checksums(bundle_dir, manifest.get("files", {}))

        entities: list[EntityMetadata] = []
        entities.extend(self._load_tables(bundle_dir))
        entities.extend(self._load_routines(bundle_dir))

        failures = [
            FailureRecord(**x) for x in self._read_json(os.path.join(bundle_dir, "failures.json"))
        ]
        queries = self._load_queries(bundle_dir, manifest.get("files", {}))

        return Bundle(
            workspace=manifest.get("workspace", ""),
            warehouse=manifest.get("warehouse", ""),
            aws_region=manifest.get("aws_region", "us-east-1"),
            entities=entities,
            failures=failures,
            queries=queries,
            collector_version=manifest.get("collector_version", ""),
            created_at=manifest.get("created_at", ""),
        )

    def _verify_checksums(self, bundle_dir: str, files: dict[str, str]) -> None:
        for filename, expected in files.items():
            path = os.path.join(bundle_dir, filename)
            if not os.path.exists(path):
                raise BundleError(f"bundle missing file listed in manifest: {filename}")
            actual = sha256_file(path)
            if actual != expected:
                raise BundleError(
                    f"checksum mismatch for {filename}: manifest {expected}, got {actual}"
                )

    def _load_tables(self, bundle_dir: str) -> list[EntityMetadata]:
        rows = self._read_json(os.path.join(bundle_dir, "tables.json"))
        out: list[EntityMetadata] = []
        for r in rows:
            out.append(
                EntityMetadata(
                    entity_id=r["entity_id"],
                    schema_name=r["schema_name"],
                    full_name=r["full_name"],
                    entity_type=EntityType(r["entity_type"]),
                    population=EntityPopulation(r["population"]),
                    num_rows=r["num_rows"],
                    num_bytes=r["num_bytes"],
                    columns=[self._col_from_dict(c) for c in r["columns"]],
                    clustering_fields=r.get("clustering_fields"),
                    view_query=r.get("view_query"),
                    mview_query=r.get("mview_query"),
                    routine=None,
                    depends_on=r.get("depends_on", []),
                    last_modified=self._parse_dt(r.get("last_modified")),
                )
            )
        return out

    def _load_routines(self, bundle_dir: str) -> list[EntityMetadata]:
        rows = self._read_json(os.path.join(bundle_dir, "routines.json"))
        out: list[EntityMetadata] = []
        for r in rows:
            routine = RoutineMetadata(**r["routine"])
            out.append(
                EntityMetadata(
                    entity_id=r["entity_id"],
                    schema_name=r["schema_name"],
                    full_name=r["full_name"],
                    entity_type=EntityType.ROUTINE,
                    population=EntityPopulation.REBUILT,
                    num_rows=0,
                    num_bytes=0,
                    columns=[],
                    clustering_fields=None,
                    view_query=None,
                    mview_query=None,
                    routine=routine,
                    depends_on=[],
                    last_modified=self._parse_dt(r.get("last_modified")),
                )
            )
        return out

    def _load_queries(self, bundle_dir: str, files: dict[str, str]) -> list[QueryRecord] | None:
        if "queries.jsonl" not in files:
            return None
        path = os.path.join(bundle_dir, "queries.jsonl")
        out: list[QueryRecord] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(QueryRecord(**json.loads(line)))
        return out

    @staticmethod
    def _read_json(path: str):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _parse_dt(value):
        if not value:
            return datetime(1970, 1, 1)
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime(1970, 1, 1)

    @classmethod
    def _col_from_dict(cls, c) -> ColumnSchema:
        return ColumnSchema(
            name=c["name"],
            field_type=c["field_type"],
            nullable=c["nullable"],
            fields=[cls._col_from_dict(x) for x in c.get("fields", [])],
        )
