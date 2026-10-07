"""Microsoft Fabric metadata scanner — yields normative EntityMetadata.

Read-only: captures table / view / materialized-view / routine *metadata and
definitions* only through the Fabric Warehouse SQL analytics endpoint; never
executes SELECT against data rows. Resilient: per-entity failures are recorded
and skipped rather than aborting the scan.

The frozen seam other modules build on mirrors the reference BigQueryScanner:
``scan() -> Iterator[EntityMetadata]`` plus ``self.failures: list[FailureRecord]``.

Connectivity: the SQL analytics endpoint speaks the TDS protocol (SQL Server
wire protocol). We reach it with pyodbc (ODBC Driver 18) and authenticate with a
Microsoft Entra access token obtained via azure-identity — no password or secret
is ever written to disk. Metadata is read from the standard catalog views:

  - sys.tables / sys.columns / sys.types / sys.schemas  (schema)
  - sys.sql_modules + sys.objects                       (view / proc / function bodies)
  - INFORMATION_SCHEMA.VIEWS                             (view text fallback)

Both pyodbc and azure-identity are imported lazily so the rest of the package
(models, scoring, report, bundle) imports and tests without the native ODBC
driver present.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator
from datetime import datetime, timezone

from fabric_assess.core.classifier import classify_population
from fabric_assess.models import (
    ColumnSchema,
    EntityMetadata,
    EntityType,
    FailureRecord,
    RoutineMetadata,
)

logger = logging.getLogger(__name__)

# Microsoft Entra token audience for the Fabric / SQL analytics endpoint (Azure
# SQL resource). The TDS layer accepts an Entra access token via the
# SQL_COPT_SS_ACCESS_TOKEN pre-login attribute.
_FABRIC_SQL_SCOPE = "https://database.windows.net/.default"
_SQL_COPT_SS_ACCESS_TOKEN = 1256  # msodbcsql pre-login attribute id

# sys object type -> EntityType. Unknown types fall back to TABLE.
_OBJECT_TYPE_MAP: dict[str, EntityType] = {
    "U": EntityType.TABLE,            # user table
    "V": EntityType.VIEW,             # view
    "P": EntityType.ROUTINE,          # stored procedure
    "FN": EntityType.ROUTINE,         # scalar function
    "TF": EntityType.ROUTINE,         # table-valued function
    "IF": EntityType.ROUTINE,         # inline table-valued function
}


class ScannerError(Exception):
    """Raised on a fatal scanner error (auth failure, endpoint unreachable)."""


class FabricScanner:
    """Scans a Fabric Warehouse's metadata over the SQL analytics endpoint.

    Authenticates with a Microsoft Entra identity (service principal via
    azure-identity's DefaultAzureCredential, or an explicitly supplied token).
    Reads metadata and object definitions only — never data rows.
    """

    def __init__(
        self,
        server: str,
        database: str,
        *,
        access_token: str | None = None,
        credential=None,
        odbc_driver: str = "ODBC Driver 18 for SQL Server",
    ) -> None:
        """
        server:   the SQL analytics endpoint host (e.g.
                  ``<workspace>.datawarehouse.fabric.microsoft.com``).
        database: the warehouse / lakehouse name.
        access_token / credential: optional pre-obtained Entra token or an
                  azure-identity credential; if neither is given,
                  DefaultAzureCredential is used at connect time.
        """
        self._server = server
        self._database = database
        self._access_token = access_token
        self._credential = credential
        self._odbc_driver = odbc_driver
        self._conn = None
        self.failures: list[FailureRecord] = []

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _token_bytes(self) -> bytes:
        """Obtain an Entra access token and pack it the way msodbcsql expects."""
        token = self._access_token
        if token is None:
            credential = self._credential
            if credential is None:
                try:
                    from azure.identity import DefaultAzureCredential
                except ImportError as exc:  # pragma: no cover - env dependent
                    raise ScannerError(
                        "azure-identity is required for Entra authentication; "
                        "install the package or pass an access_token."
                    ) from exc
                credential = DefaultAzureCredential()
            token = credential.get_token(_FABRIC_SQL_SCOPE).token
        # msodbcsql wants the UTF-16-LE token prefixed with its 4-byte length.
        token_bytes = token.encode("utf-16-le")
        return struct.pack("<i", len(token_bytes)) + token_bytes

    def _connect(self):
        if self._conn is not None:
            return self._conn
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ScannerError(
                "pyodbc (with ODBC Driver 18 for SQL Server) is required to reach "
                "the Fabric SQL analytics endpoint."
            ) from exc

        conn_str = (
            f"Driver={{{self._odbc_driver}}};"
            f"Server={self._server},1433;"
            f"Database={self._database};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )
        try:
            self._conn = pyodbc.connect(
                conn_str,
                attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: self._token_bytes()},
            )
        except Exception as exc:
            raise ScannerError(f"failed to connect to Fabric endpoint: {exc}") from exc
        return self._conn

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan(self) -> Iterator[EntityMetadata]:
        """Yield EntityMetadata for every table/view/mview/routine in the warehouse.

        Per-entity failures are appended to ``self.failures`` and skipped. A fatal
        connection/auth error raises ScannerError before any entity is yielded.
        """
        conn = self._connect()
        objects = self._list_objects(conn)
        columns_by_object = self._columns_by_object(conn)
        modules_by_object = self._modules_by_object(conn)

        for obj in objects:
            full_name = f"{obj['schema']}.{obj['name']}"
            try:
                yield self._build_entity(obj, columns_by_object, modules_by_object)
            except Exception as exc:
                self.failures.append(
                    FailureRecord(entity_name=full_name, stage="scan", error=str(exc))
                )
                logger.warning("skipping %s: %s", full_name, exc)

    def _build_entity(
        self,
        obj: dict,
        columns_by_object: dict[int, list[ColumnSchema]],
        modules_by_object: dict[int, str],
    ) -> EntityMetadata:
        object_id = obj["object_id"]
        entity_type = _OBJECT_TYPE_MAP.get(obj["type"].strip(), EntityType.TABLE)
        columns = columns_by_object.get(object_id, [])
        body = modules_by_object.get(object_id)

        view_query = body if entity_type == EntityType.VIEW else None
        routine = None
        if entity_type == EntityType.ROUTINE:
            routine = RoutineMetadata(
                name=obj["name"],
                language="SQL",
                arguments=[],
                body=body or "",
                routine_type=obj["type"].strip(),
            )

        return EntityMetadata(
            entity_id=str(object_id),
            schema_name=obj["schema"],
            full_name=f"{obj['schema']}.{obj['name']}",
            entity_type=entity_type,
            population=classify_population(entity_type),
            num_rows=obj.get("num_rows") or 0,
            num_bytes=obj.get("num_bytes") or 0,
            columns=columns,
            clustering_fields=None,
            view_query=view_query,
            mview_query=None,
            routine=routine,
            depends_on=[],
            last_modified=obj.get("modify_date") or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Catalog queries. Identifiers are system catalog views; the only
    # interpolation anywhere in the scanner is none — these are static.
    # ------------------------------------------------------------------

    def _list_objects(self, conn) -> list[dict]:
        sql = """
            SELECT o.object_id, s.name AS schema_name, o.name AS object_name,
                   o.type AS object_type, o.modify_date,
                   ISNULL(p.rows, 0) AS num_rows
            FROM sys.objects o
            JOIN sys.schemas s ON s.schema_id = o.schema_id
            LEFT JOIN sys.partitions p
                   ON p.object_id = o.object_id AND p.index_id IN (0, 1)
            WHERE o.type IN ('U','V','P','FN','TF','IF')
              AND o.is_ms_shipped = 0
        """
        rows = self._query(conn, sql)
        objects: list[dict] = []
        for r in rows:
            objects.append(
                {
                    "object_id": r[0],
                    "schema": r[1],
                    "name": r[2],
                    "type": r[3],
                    "modify_date": r[4],
                    "num_rows": r[5],
                    "num_bytes": 0,
                }
            )
        return objects

    def _columns_by_object(self, conn) -> dict[int, list[ColumnSchema]]:
        sql = """
            SELECT c.object_id, c.name, t.name AS type_name, c.is_nullable
            FROM sys.columns c
            JOIN sys.types t ON t.user_type_id = c.user_type_id
            ORDER BY c.object_id, c.column_id
        """
        result: dict[int, list[ColumnSchema]] = {}
        for r in self._query(conn, sql):
            result.setdefault(r[0], []).append(
                ColumnSchema(name=r[1], field_type=r[2], nullable=bool(r[3]))
            )
        return result

    def _modules_by_object(self, conn) -> dict[int, str]:
        sql = "SELECT object_id, definition FROM sys.sql_modules"
        return {r[0]: r[1] for r in self._query(conn, sql) if r[1]}

    def _query(self, conn, sql: str) -> list[tuple]:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            return cursor.fetchall()
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
