"""Shared pytest fixtures."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fabric_assess.models import (
    ColumnSchema,
    EntityMetadata,
    EntityPopulation,
    EntityType,
)


@pytest.fixture
def table_entity() -> EntityMetadata:
    return EntityMetadata(
        entity_id="1",
        schema_name="dbo",
        full_name="dbo.orders",
        entity_type=EntityType.TABLE,
        population=EntityPopulation.TABLE,
        num_rows=5_000_000,
        num_bytes=200 * 1024**3,
        columns=[
            ColumnSchema("id", "bigint", False),
            ColumnSchema("amount", "money", True),
            ColumnSchema("updated_at", "datetime2", True),
        ],
        clustering_fields=None,
        view_query=None,
        mview_query=None,
        routine=None,
        depends_on=[],
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def clean_table_entity() -> EntityMetadata:
    return EntityMetadata(
        entity_id="2",
        schema_name="dbo",
        full_name="dbo.customers",
        entity_type=EntityType.TABLE,
        population=EntityPopulation.TABLE,
        num_rows=100,
        num_bytes=1024,
        columns=[
            ColumnSchema("id", "int", False),
            ColumnSchema("name", "nvarchar(100)", True),
        ],
        clustering_fields=None,
        view_query=None,
        mview_query=None,
        routine=None,
        depends_on=[],
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def view_entity() -> EntityMetadata:
    return EntityMetadata(
        entity_id="3",
        schema_name="dbo",
        full_name="dbo.v_sales",
        entity_type=EntityType.VIEW,
        population=EntityPopulation.REBUILT,
        num_rows=0,
        num_bytes=0,
        columns=[],
        clustering_fields=None,
        view_query="SELECT TOP 10 STRING_AGG(id, ',') FROM dbo.orders CROSS APPLY f(id)",
        mview_query=None,
        routine=None,
        depends_on=["dbo.orders"],
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
