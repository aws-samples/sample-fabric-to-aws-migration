"""SQL Surface assembly + T-SQL construct detection + anonymization.

The SQL Surface is every piece of source SQL the Query Complexity axis scores:
view SQL, materialized-view SQL, and function/stored-procedure bodies
(auto-captured from ``sys.sql_modules``), plus ad-hoc query text when workload
history (``queryinsights.exec_requests_history``) is provided.

``detect()`` scans a SQL string for T-SQL-specific constructs; ``anonymize()``
strips string and numeric literals before anything is stored or reported;
``assemble()`` builds the per-entity surface keyed by full_name.

The construct-class strings below are the frozen contract the Query Complexity
scorer (``scoring/complexity.py``) keys on — do not change them on one side only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from fabric_assess.models import DetectedConstruct, EntityMetadata

# ---- Canonical construct classes (the frozen seam with scoring/complexity) ----

OPENJSON = "OPENJSON"
CROSS_APPLY = "CROSS_APPLY"
STRING_AGG = "STRING_AGG"
MERGE = "MERGE"
PIVOT = "PIVOT"
TOP = "TOP"
PROC = "PROC"
CLR = "CLR"
FUNCTION_DRIFT = "FUNCTION_DRIFT"

CONSTRUCT_CLASSES = (
    OPENJSON,
    CROSS_APPLY,
    STRING_AGG,
    MERGE,
    PIVOT,
    TOP,
    PROC,
    CLR,
    FUNCTION_DRIFT,
)


# ---------------------------------------------------------------------------
# Anonymization — string and numeric literals stripped before storage.
# ---------------------------------------------------------------------------

# Single-quoted string literals (T-SQL escapes a quote by doubling it).
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

# Numeric literals following an operator/keyword/punctuation, so digits inside
# identifiers (e.g. col1, table2) are preserved.
_NUMERIC_LITERAL_RE = re.compile(r"(?<=[\s=><!(,+\-*/])\d+\.?\d*(?=[\s,);]|$)")


# ---------------------------------------------------------------------------
# Construct detection patterns (T-SQL dialect).
# ---------------------------------------------------------------------------

# OPENJSON / JSON_VALUE / JSON_QUERY — JSON shredding, no 1:1 engine equivalent.
_OPENJSON_RE = re.compile(r"\b(OPENJSON|JSON_VALUE|JSON_QUERY|JSON_MODIFY)\s*\(", re.IGNORECASE)

# CROSS APPLY / OUTER APPLY — correlated lateral, rewritten as lateral/join.
_CROSS_APPLY_RE = re.compile(r"\b(CROSS|OUTER)\s+APPLY\b", re.IGNORECASE)

# STRING_AGG(expr, sep) — maps to LISTAGG with a WITHIN GROUP clause.
_STRING_AGG_RE = re.compile(r"\bSTRING_AGG\s*\(", re.IGNORECASE)

# MERGE — T-SQL MERGE semantics differ from Iceberg/engine MERGE.
_MERGE_RE = re.compile(r"\bMERGE\s+(?:INTO\s+)?\w", re.IGNORECASE)

# PIVOT / UNPIVOT — rewritten as CASE aggregation / unnest.
_PIVOT_RE = re.compile(r"\b(PIVOT|UNPIVOT)\b", re.IGNORECASE)

# TOP n — maps to LIMIT n; TOP ... WITH TIES / percent needs manual review.
_TOP_RE = re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s*\(?\s*\d+", re.IGNORECASE)

# JavaScript / external-language markers are not valid T-SQL; CLR assemblies are.
_CLR_RE = re.compile(r"\b(EXTERNAL\s+NAME|CREATE\s+ASSEMBLY)\b", re.IGNORECASE)

# Function-name / semantic drift: T-SQL functions whose Redshift/Athena
# equivalent differs in name or argument order. Conservative, high-signal set.
_FUNCTION_DRIFT_NAMES = (
    "ISNULL",
    "GETDATE",
    "GETUTCDATE",
    "DATEADD",
    "DATEDIFF",
    "DATEPART",
    "CONVERT",
    "CHARINDEX",
    "LEN",
    "IIF",
    "FORMAT",
)
_FUNCTION_DRIFT_RE = re.compile(
    r"\b(" + "|".join(_FUNCTION_DRIFT_NAMES) + r")\s*\(", re.IGNORECASE
)

# Stored-procedure body marker (used when scanning routine bodies / ad-hoc text).
_PROC_RE = re.compile(r"\bCREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\b", re.IGNORECASE)


_DETECTORS: list[tuple[str, re.Pattern[str], str]] = [
    (CLR, _CLR_RE, "CLR / external-name routine — no Query Engine equivalent; manual rewrite."),
    (OPENJSON, _OPENJSON_RE, "OPENJSON / JSON_* shredding — rewrite to engine JSON/SUPER navigation."),
    (CROSS_APPLY, _CROSS_APPLY_RE, "CROSS/OUTER APPLY — rewrite as a lateral/join pattern for the Query Engine."),
    (STRING_AGG, _STRING_AGG_RE, "STRING_AGG — maps to LISTAGG WITHIN GROUP; verify ordering."),
    (MERGE, _MERGE_RE, "MERGE — T-SQL MERGE semantics differ from the engine; review carefully."),
    (PIVOT, _PIVOT_RE, "PIVOT/UNPIVOT — rewrite as CASE aggregation or unnest."),
    (TOP, _TOP_RE, "SELECT TOP n — maps to LIMIT n; WITH TIES / PERCENT needs manual review."),
    (PROC, _PROC_RE, "Stored procedure — port to engine stored procedure or external orchestration."),
    (FUNCTION_DRIFT, _FUNCTION_DRIFT_RE, "T-SQL function with name/argument drift — adapt to the Query Engine dialect."),
]


class SQLSurfaceAnalyzer:
    """Assembles the SQL Surface and detects T-SQL-specific constructs."""

    def anonymize(self, sql: str) -> str:
        """Replace string and numeric literals with ``?`` placeholders.

        String literals become ``'?'``; numeric literals after operators/keywords
        become ``?``. Identifiers containing digits (``col1``, ``table2``) are
        preserved. After this, no original literal value remains.
        """
        if not sql:
            return sql
        result = _STRING_LITERAL_RE.sub("'?'", sql)
        result = _NUMERIC_LITERAL_RE.sub("?", result)
        return result

    def detect(self, sql: str) -> list[DetectedConstruct]:
        """Detect T-SQL-specific constructs in a single SQL string.

        Returns one :class:`DetectedConstruct` per construct *class* present
        (deduped by class), each carrying an anonymized snippet and a
        human-readable description. Order follows the detector order above.
        """
        if not sql:
            return []

        constructs: list[DetectedConstruct] = []
        for construct_class, pattern, description in _DETECTORS:
            match = pattern.search(sql)
            if match is None:
                continue
            constructs.append(
                DetectedConstruct(
                    construct_class=construct_class,
                    snippet=self._snippet(sql, match),
                    description=description,
                )
            )
        return constructs

    def assemble(
        self,
        entities: Iterable[EntityMetadata],
        query_log_text: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Build the SQL Surface: a map of entity full_name -> list of SQL strings.

        Auto-captured surface (always available): views -> ``view_query``,
        materialized views -> ``mview_query``, routines -> ``routine.body``.
        Log-only surface (only when ``query_log_text`` is provided): each ad-hoc
        query body, keyed under ``"__ad_hoc__"``.

        All SQL is anonymized before being placed in the surface.
        """
        surface: dict[str, list[str]] = {}

        for entity in entities:
            sqls: list[str] = []
            if entity.view_query:
                sqls.append(entity.view_query)
            if entity.mview_query:
                sqls.append(entity.mview_query)
            if entity.routine is not None and entity.routine.body:
                sqls.append(entity.routine.body)
            if sqls:
                surface[entity.full_name] = [self.anonymize(s) for s in sqls]

        if query_log_text:
            ad_hoc = [self.anonymize(q) for q in query_log_text if q]
            if ad_hoc:
                surface["__ad_hoc__"] = ad_hoc

        return surface

    def detect_for_entities(
        self,
        entities: Iterable[EntityMetadata],
        query_log_text: list[str] | None = None,
    ) -> dict[str, list[DetectedConstruct]]:
        """Attribute detected constructs to each entity.

        Returns full_name -> constructs, including the ``"__ad_hoc__"`` bucket for
        log-sourced SQL. Entities with no detected construct are omitted.
        """
        result: dict[str, list[DetectedConstruct]] = {}
        n_classes = len(CONSTRUCT_CLASSES)
        for full_name, sqls in self.assemble(entities, query_log_text).items():
            found: list[DetectedConstruct] = []
            seen: set[str] = set()
            for sql in sqls:
                for c in self.detect(sql):
                    if c.construct_class not in seen:
                        seen.add(c.construct_class)
                        found.append(c)
                if len(seen) == n_classes:
                    break
            if found:
                result[full_name] = found
        return result

    def _snippet(self, sql: str, match: re.Match[str], width: int = 40) -> str:
        """Return an anonymized window around *match* for human context."""
        start = max(0, match.start() - width // 2)
        end = min(len(sql), match.end() + width // 2)
        window = sql[start:end].replace("\n", " ").strip()
        return self.anonymize(window)
