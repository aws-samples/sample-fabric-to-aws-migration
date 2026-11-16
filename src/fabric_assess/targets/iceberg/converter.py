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

# Amazon S3 Tables names allow only lowercase letters, digits and underscores,
# and a name with capitals is not visible to AWS Glue, Lake Formation or Athena.
# Fabric identifiers are normalized to that set; the report warns when a name
# changes so queries can be updated. This only shapes report text.
_UNSAFE_CHARS_RE = re.compile(r"[^a-z0-9_]")


def _quote_ident(name: str, *, table: bool = False) -> str:
    """Normalize an identifier to the Amazon S3 Tables naming rules.

    Table names must also begin with a letter or number.
    """
    norm = _UNSAFE_CHARS_RE.sub("_", name.lower())
    if table and (not norm or norm[0] == "_"):
        norm = "t" + norm
    return norm or "col"


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

        not_null: list[str] = []
        for col in entity.columns:
            iceberg_type, cast = _map_type(col)
            if cast is not None:
                lossy.append(cast)
            if not col.nullable:
                not_null.append(_quote_ident(col.name))
            # Athena CREATE TABLE has no NOT NULL constraint; nullability is
            # reported as a warning instead.
            lines.append(f"    {_quote_ident(col.name)} {iceberg_type}")
        if not_null:
            warnings.append(
                "Athena DDL cannot declare NOT NULL; enforce in the load for: "
                + ", ".join(not_null)
            )

        renamed = [
            f"{c.name} -> {_quote_ident(c.name)}"
            for c in entity.columns
            if _quote_ident(c.name) != c.name
        ]
        table_src = entity.full_name.replace(".", "_")
        if _quote_ident(table_src, table=True) != table_src:
            renamed.insert(0, f"{entity.full_name} -> {_quote_ident(table_src, table=True)}")
        if renamed:
            warnings.append(
                "names normalized for Amazon S3 Tables (lowercase letters, digits, "
                "underscores); update queries that use the old names: "
                + ", ".join(renamed)
            )
        normalized = [_quote_ident(c.name) for c in entity.columns]
        if len(set(normalized)) != len(normalized):
            return ConversionResult(
                ddl="",
                lossy_casts=lossy,
                warnings=warnings
                + ["column names collide after normalization; rename before the copy"],
                success=False,
            )

        # Amazon S3 Tables manage their own storage inside a table bucket, so
        # Athena rejects a LOCATION clause. Run the DDL with the query context
        # catalog set to s3tablescatalog/<table-bucket> and the namespace as the
        # database, per the Athena S3 Tables documentation.
        table_name = _quote_ident(table_src, table=True)
        ddl = (
            "-- Run in Athena with Catalog = s3tablescatalog/<your-table-bucket>\n"
            f"CREATE TABLE <your_namespace>.{table_name} (\n"
            + ",\n".join(lines)
            + "\n)\nTBLPROPERTIES ('table_type' = 'ICEBERG');"
        )

        return ConversionResult(
            ddl=ddl, lossy_casts=lossy, warnings=warnings, success=True
        )
