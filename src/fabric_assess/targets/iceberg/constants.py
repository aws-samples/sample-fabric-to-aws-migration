"""T-SQL / Delta type -> Apache Iceberg type mapping.

Clean mappings convert without review. Lossy mappings (precision loss, no exact
equivalent, or a semantic change) are surfaced as LossyCast for human review and
feed the Migration Effort score.
"""
from __future__ import annotations

# Clean, information-preserving maps: T-SQL type (lowercased) -> Iceberg type.
CLEAN_TYPE_MAP: dict[str, str] = {
    "bit": "boolean",
    "tinyint": "int",
    "smallint": "int",
    "int": "int",
    "bigint": "long",
    "real": "float",
    "float": "double",
    "date": "date",
    "time": "time",
    "datetime2": "timestamp",
    "datetimeoffset": "timestamptz",
    "char": "string",
    "varchar": "string",
    "nchar": "string",
    "nvarchar": "string",
    "uniqueidentifier": "string",
    "varbinary": "binary",
    "binary": "binary",
}

# Lossy maps: T-SQL type -> (iceberg type, reason). The cast still succeeds, but
# a human must confirm the loss is acceptable.
LOSSY_TYPE_MAP: dict[str, tuple[str, str]] = {
    "money": ("decimal(19,4)", "money maps to decimal(19,4); verify scale is sufficient"),
    "smallmoney": ("decimal(10,4)", "smallmoney maps to decimal(10,4); verify scale"),
    "datetime": ("timestamp", "legacy datetime has ~3.33ms resolution; timestamp is microsecond"),
    "smalldatetime": ("timestamp", "smalldatetime has 1-minute resolution; verify precision need"),
    "text": ("string", "deprecated text LOB -> string; verify no >2GB values"),
    "ntext": ("string", "deprecated ntext LOB -> string; verify no >2GB values"),
    "image": ("binary", "deprecated image LOB -> binary; verify no >2GB values"),
    "xml": ("string", "xml stored as string; engine XML functions are not portable"),
    "sql_variant": ("string", "sql_variant has no typed equivalent; stored as string"),
    "hierarchyid": ("string", "hierarchyid has no equivalent; stored as string"),
    "geography": ("string", "spatial type has no Iceberg equivalent; stored as WKT string"),
    "geometry": ("string", "spatial type has no Iceberg equivalent; stored as WKT string"),
}

# decimal/numeric carry their own precision; handled specially in the converter.
_DECIMAL_TYPES = frozenset({"decimal", "numeric"})
