"""Convert a Fabric table's schema to Apache Iceberg CREATE TABLE DDL.

Tables only (REBUILT entities carry no data). The converter maps each column's
T-SQL/Delta type to an Iceberg type, records any lossy cast for human review, and
emits Athena-dialect Iceberg DDL. The emitted SQL is report output — it is never
executed by this tool.
"""
from __future__ import annotations

import re

from fabric_assess.models import (
    ColumnSchema,
    ConversionResult,
    EntityMetadata,
    EntityType,
    LossyCast,
)
from fabric_assess.targets.iceberg.constants import (
    _DECIMAL_TYPES,
    CLEAN_TYPE_MAP,
    LOSSY_TYPE_MAP,
)

# Iceberg/Athena identifier safety: letters, digits, underscore. Anything else is
# quoted. Fabric identifiers are the user's own; this only shapes report text.
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    if _SAFE_IDENT_RE.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _base_type(field_type: str) -> str:
    """Strip any length/precision suffix: ``varchar(50)`` -> ``varchar``."""
    return field_type.split("(", 1)[0].strip().lower()


def _map_type(column: ColumnSchema) -> tuple[str, LossyCast | None]:
    """Map one column's type to Iceberg, returning any lossy cast."""
    raw = column.field_type.strip()
    base = _base_type(raw)

    if base in _DECIMAL_TYPES:
        # Preserve declared precision/scale when present, else a safe default.
        inside = raw[raw.find("(") + 1 : raw.find(")")] if "(" in raw else "38,18"
        iceberg = f"decimal({inside})" if inside else "decimal(38,18)"
        return iceberg, None

    if base in CLEAN_TYPE_MAP:
        return CLEAN_TYPE_MAP[base], None

    if base in LOSSY_TYPE_MAP:
        iceberg, reason = LOSSY_TYPE_MAP[base]
        return iceberg, LossyCast(
            column=column.name,
            source_type=raw,
            iceberg_type=iceberg,
            loss_description=reason,
        )

    # Unknown type — default to string and flag it for review.
    return "string", LossyCast(
        column=column.name,
        source_type=raw,
        iceberg_type="string",
        loss_description=f"unrecognized type '{raw}' defaulted to string; verify mapping",
    )


class IcebergConverter:
    """Produce Iceberg CREATE TABLE DDL from a Fabric table's schema."""

    def convert(self, entity: EntityMetadata) -> ConversionResult:
        if entity.entity_type not in (EntityType.TABLE, EntityType.EXTERNAL):
            # Non-table entities are rebuilt, not moved — no DDL.
            return ConversionResult(ddl="", lossy_casts=[], warnings=[], success=True)

        if not entity.columns:
            return ConversionResult(
                ddl="",
                lossy_casts=[],
                warnings=["no columns captured — cannot generate DDL"],
                success=False,
            )

        lines: list[str] = []
        lossy: list[LossyCast] = []
        warnings: list[str] = []

        for col in entity.columns:
            iceberg_type, cast = _map_type(col)
            if cast is not None:
                lossy.append(cast)
            null_sql = "" if col.nullable else " NOT NULL"
            lines.append(f"    {_quote_ident(col.name)} {iceberg_type}{null_sql}")

        table_ident = _quote_ident(entity.full_name.replace(".", "_"))
        ddl = (
            f"CREATE TABLE {table_ident} (\n"
            + ",\n".join(lines)
            + "\n)\nLOCATION 's3://<your-bucket>/"
            + entity.full_name.replace(".", "/")
            + "/'\nTBLPROPERTIES ('table_type' = 'ICEBERG');"
        )

        return ConversionResult(
            ddl=ddl, lossy_casts=lossy, warnings=warnings, success=True
        )
