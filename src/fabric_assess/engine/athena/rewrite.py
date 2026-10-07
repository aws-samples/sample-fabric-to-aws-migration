"""Athena rewrite guidance: T-SQL -> Trino/Presto (Athena) SQL.

Two layers:

1. Best-effort automatic translation via sqlglot (``tsql`` -> ``athena``). sqlglot
   is imported lazily so the package imports without it; when absent, translation
   degrades to "manual" with a clear warning rather than failing.
2. Per-construct human guidance keyed on the construct classes the SQL-surface
   analyzer detects, for the constructs sqlglot cannot fully carry across.

The emitted SQL is report output — it is never executed by this tool.
"""
from __future__ import annotations

from fabric_assess.models import TranslationResult

# Human guidance per detected T-SQL construct class (Athena / Trino dialect).
REWRITE_GUIDANCE: dict[str, str] = {
    "OPENJSON": (
        "OPENJSON / JSON_VALUE / JSON_QUERY: use Athena JSON functions "
        "(json_extract, json_extract_scalar) or UNNEST over a parsed array."
    ),
    "CROSS_APPLY": (
        "CROSS/OUTER APPLY: rewrite as CROSS JOIN UNNEST(...) for table-valued "
        "expressions, or a LEFT JOIN LATERAL pattern."
    ),
    "STRING_AGG": (
        "STRING_AGG(expr, sep): use array_join(array_agg(expr), sep); add an "
        "ORDER BY inside array_agg to reproduce WITHIN GROUP ordering."
    ),
    "MERGE": (
        "MERGE: Athena Iceberg supports MERGE INTO, but T-SQL OUTPUT and "
        "multi-WHEN semantics differ — review each WHEN branch."
    ),
    "PIVOT": (
        "PIVOT/UNPIVOT: Athena has no PIVOT; rewrite as conditional aggregation "
        "(CASE inside aggregates) or UNNEST for UNPIVOT."
    ),
    "TOP": (
        "SELECT TOP n: use LIMIT n. TOP ... WITH TIES / PERCENT needs a window "
        "function (RANK/row_number) rewrite."
    ),
    "PROC": (
        "Stored procedures: Athena has no procedural layer. Move logic to the "
        "orchestration tier (Step Functions / Glue) or materialized CTAS steps."
    ),
    "CLR": (
        "CLR / EXTERNAL NAME routines: no engine equivalent — reimplement as a "
        "UDF (Lambda-backed) or application code."
    ),
    "FUNCTION_DRIFT": (
        "Function drift: map ISNULL->COALESCE, GETDATE()->current_timestamp, "
        "DATEADD/DATEDIFF->date_add/date_diff (argument order differs), "
        "LEN->length, CHARINDEX->strpos, IIF->IF."
    ),
}


def guidance_for(construct_classes: list[str]) -> list[str]:
    """Return human rewrite guidance for each detected construct class."""
    out: list[str] = []
    for cls in construct_classes:
        text = REWRITE_GUIDANCE.get(cls)
        if text and text not in out:
            out.append(text)
    return out


def translate(sql: str) -> TranslationResult:
    """Best-effort T-SQL -> Athena translation via sqlglot.

    Degrades gracefully: if sqlglot is missing or cannot parse the statement,
    returns a LOW-confidence result flagged for manual rewrite rather than raising.
    """
    if not sql or not sql.strip():
        return TranslationResult(
            translated_sql="", confidence="LOW",
            warnings=["empty SQL"], target_engine="athena",
        )
    try:
        import sqlglot
    except ImportError:
        return TranslationResult(
            translated_sql="",
            confidence="LOW",
            warnings=["sqlglot not installed — manual rewrite required"],
            target_engine="athena",
        )
    try:
        out = sqlglot.transpile(sql, read="tsql", write="athena")
        translated = ";\n".join(out)
        return TranslationResult(
            translated_sql=translated,
            confidence="MEDIUM",
            warnings=["machine translation — review before use"],
            target_engine="athena",
        )
    except Exception as exc:
        return TranslationResult(
            translated_sql="",
            confidence="LOW",
            warnings=[f"sqlglot could not translate — manual rewrite required: {exc}"],
            target_engine="athena",
        )
