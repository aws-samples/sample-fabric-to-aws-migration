"""Bundle writer — serializes a Bundle to a directory with a checksummed manifest.

The bundle is the collect-then-report seam: the collector writes it where Fabric
credentials live; the analyst generates the report from it, fully offline. The
``tables.json`` file carries the user's view/procedure SQL (already anonymized by
the scanner's surface pass is NOT assumed here — bodies are raw definitions), so
it is written one entity per line to stay reviewable before sharing.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

from fabric_assess.bundle.models import SCHEMA_VERSION, Bundle, sha256_file
from fabric_assess.models import EntityType


class BundleWriter:
    """Write a Bundle to a directory with a versioned, checksummed manifest."""

    def write(self, bundle: Bundle, out_dir: str) -> str:
        bundle_dir = os.path.join(out_dir, "bundle")
        os.makedirs(bundle_dir, exist_ok=True)

        files: dict[str, str] = {}
        files["tables.json"] = self._write_tables(bundle, bundle_dir)
        files["routines.json"] = self._write_routines(bundle, bundle_dir)
        files["failures.json"] = self._write_failures(bundle, bundle_dir)
        if bundle.queries is not None:
            files["queries.jsonl"] = self._write_queries(bundle, bundle_dir)

        self._write_manifest(bundle, files, bundle_dir)
        return bundle_dir

    def _write_tables(self, bundle: Bundle, bundle_dir: str) -> str:
        path = os.path.join(bundle_dir, "tables.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[\n")
            first = True
            for e in bundle.entities:
                if e.entity_type == EntityType.ROUTINE:
                    continue
                row = {
                    "full_name": e.full_name,
                    "schema_name": e.schema_name,
                    "entity_id": e.entity_id,
                    "entity_type": e.entity_type.value,
                    "population": e.population.value,
                    "num_rows": e.num_rows,
                    "num_bytes": e.num_bytes,
                    "columns": [self._col_to_dict(c) for c in e.columns],
                    "clustering_fields": e.clustering_fields,
                    "view_query": e.view_query,
                    "mview_query": e.mview_query,
                    "depends_on": e.depends_on,
                    "last_modified": e.last_modified.isoformat() if e.last_modified else None,
                }
                if not first:
                    f.write(",\n")
                json.dump(row, f, ensure_ascii=False, default=str)
                first = False
            f.write("\n]")
        return sha256_file(path)

    def _write_routines(self, bundle: Bundle, bundle_dir: str) -> str:
        routines = []
        for e in bundle.entities:
            if e.entity_type != EntityType.ROUTINE or e.routine is None:
                continue
            routines.append(
                {
                    "full_name": e.full_name,
                    "schema_name": e.schema_name,
                    "entity_id": e.entity_id,
                    "routine": asdict(e.routine),
                    "last_modified": e.last_modified.isoformat() if e.last_modified else None,
                }
            )
        path = os.path.join(bundle_dir, "routines.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(routines, f, ensure_ascii=False, indent=2, default=str)
        return sha256_file(path)

    def _write_failures(self, bundle: Bundle, bundle_dir: str) -> str:
        path = os.path.join(bundle_dir, "failures.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(x) for x in bundle.failures], f, ensure_ascii=False, indent=2)
        return sha256_file(path)

    def _write_queries(self, bundle: Bundle, bundle_dir: str) -> str:
        path = os.path.join(bundle_dir, "queries.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for q in bundle.queries or []:
                json.dump(asdict(q), f, ensure_ascii=False, default=str)
                f.write("\n")
        return sha256_file(path)

    def _write_manifest(self, bundle: Bundle, files: dict[str, str], bundle_dir: str) -> None:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "workspace": bundle.workspace,
            "warehouse": bundle.warehouse,
            "aws_region": bundle.aws_region,
            "collector_version": bundle.collector_version,
            "created_at": bundle.created_at or datetime.now(timezone.utc).isoformat(),
            "files": files,  # filename -> sha256
            "entity_count": sum(
                1 for e in bundle.entities if e.entity_type != EntityType.ROUTINE
            ),
        }
        path = os.path.join(bundle_dir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _col_to_dict(c) -> dict:
        return {
            "name": c.name,
            "field_type": c.field_type,
            "nullable": c.nullable,
            "fields": [BundleWriter._col_to_dict(x) for x in c.fields],
        }
