"""T-SQL / Delta type -> Apache Iceberg type mapping, written as Athena DDL types.

The converter emits Amazon Athena ``CREATE TABLE`` DDL for Amazon S3 Tables, so
the right-hand side uses the type names Athena DDL accepts for Iceberg tables
(``bigint`` rather than the Iceberg name ``long``). Athena Iceberg DDL has no
``tinyint``, ``smallint``, ``char``, ``time`` or ``timestamptz`` columns, so those
map to the nearest supported type.

Fabric Warehouse persists only a subset of T-SQL types (bit, smallint, int,
bigint, decimal/numeric, float, real, date, time, datetime2, char, varchar,
varbinary, uniqueidentifier). The legacy SQL Server types in LOSSY_TYPE_MAP
cannot appear in a Fabric Warehouse table; they stay mapped so a scan of a
SQL Server test database (see README) still produces a usable report.

Clean mappings convert without review. Lossy mappings (precision loss, no exact
equivalent, or a semantic change) are surfaced as LossyCast for human review and
feed the Migration Effort score.
"""
from __future__ import annotations

# Clean, information-preserving maps: T-SQL type (lowercased) -> Athena DDL type.
CLEAN_TYPE_MAP: dict[str, str] = {
    "bit": "boolean",
    "tinyint": "int",
    "smallint": "int",
    "int": "int",
    "bigint": "bigint",
    "real": "float",
    "float": "double",
    "date": "date",
    "datetime2": "timestamp",
    "char": "string",
    "varchar": "string",
    "nchar": "string",
    "nvarchar": "string",
    "varbinary": "binary",
    "binary": "binary",
}

# Lossy maps: T-SQL type -> (Athena DDL type, reason). The cast still succeeds,
# but a human must confirm the change is acceptable.
LOSSY_TYPE_MAP: dict[str, tuple[str, str]] = {
    # Types a Fabric Warehouse table can hold.
    "time": (
        "string",
        ("Athena Iceberg DDL has no time type; stored as string (or convert to "
        "microseconds since midnight as bigint)"),
    ),
    "uniqueidentifier": (
        "string",
        ("Fabric stores uniqueidentifier as binary in Delta Parquet; convert to "
        "its string form during the copy"),
    ),
    # Legacy SQL Server types (not persistable in Fabric Warehouse tables).
    "datetimeoffset": (
        "timestamp",
        ("Athena Iceberg DDL supports only timestamp without time zone; "
        "normalize values to UTC during the copy"),
    ),
    "money": ("decimal(19,4)", "money maps to decimal(19,4); confirm the scale is sufficient"),
    "smallmoney": ("decimal(10,4)", "smallmoney maps to decimal(10,4); confirm the scale"),
    "datetime": ("timestamp", "legacy datetime has ~3.33 ms resolution; timestamp is microsecond"),
    "smalldatetime": ("timestamp", "smalldatetime has 1-minute resolution; confirm precision need"),
    "text": ("string", "deprecated text LOB -> string; confirm no very large values"),
    "ntext": ("string", "deprecated ntext LOB -> string; confirm no very large values"),
    "image": ("binary", "deprecated image LOB -> binary; confirm no very large values"),
    "xml": ("string", "xml stored as string; engine XML functions are not portable"),
    "sql_variant": ("string", "sql_variant has no typed equivalent; stored as string"),
    "hierarchyid": ("string", "hierarchyid has no equivalent; stored as string"),
    "geography": ("string", "spatial type has no Iceberg equivalent; store as WKT string"),
    "geometry": ("string", "spatial type has no Iceberg equivalent; store as WKT string"),
}

# decimal/numeric carry their own precision; handled specially in the converter.
_DECIMAL_TYPES = frozenset({"decimal", "numeric"})
