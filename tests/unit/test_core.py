"""Unit tests: SQL surface detection + anonymization, scorers, Iceberg converter."""
from __future__ import annotations

from fabric_assess.core.sql_surface import SQLSurfaceAnalyzer
from fabric_assess.models import ConversionResult, EffortCategory, MigrationStrategy
from fabric_assess.scoring.complexity import ComplexityScorer
from fabric_assess.scoring.effort import EffortScorer
from fabric_assess.targets.iceberg.converter import IcebergConverter


class TestSQLSurface:
    def test_anonymize_strips_string_literals(self):
        a = SQLSurfaceAnalyzer()
        out = a.anonymize("WHERE name = 'topsecret'")
        assert "topsecret" not in out
        assert "'?'" in out

    def test_anonymize_strips_numeric_literals(self):
        a = SQLSurfaceAnalyzer()
        out = a.anonymize("WHERE amount > 123456")
        assert "123456" not in out

    def test_anonymize_preserves_identifiers_with_digits(self):
        a = SQLSurfaceAnalyzer()
        out = a.anonymize("SELECT col1, table2 FROM t")
        assert "col1" in out and "table2" in out

    def test_detect_tsql_constructs(self):
        a = SQLSurfaceAnalyzer()
        classes = {
            c.construct_class
            for c in a.detect("SELECT TOP 5 STRING_AGG(x, ',') FROM OPENJSON(@j) CROSS APPLY f(x)")
        }
        assert {"TOP", "STRING_AGG", "OPENJSON", "CROSS_APPLY"} <= classes

    def test_detect_merge_and_proc(self):
        a = SQLSurfaceAnalyzer()
        classes = {c.construct_class for c in a.detect("CREATE PROC p AS MERGE INTO t USING s ON t.id=s.id")}
        assert "MERGE" in classes and "PROC" in classes


class TestEffortScorer:
    def test_clean_table_is_auto(self, clean_table_entity):
        conv = ConversionResult(ddl="CREATE TABLE ...", lossy_casts=[], warnings=[], success=True)
        res = EffortScorer().score(clean_table_entity, conv)
        assert res.category == EffortCategory.AUTO

    def test_conversion_failure_is_manual(self, table_entity):
        conv = ConversionResult(ddl="", lossy_casts=[], warnings=["x"], success=False)
        res = EffortScorer().score(table_entity, conv)
        assert res.category == EffortCategory.MANUAL
        assert res.strategy == MigrationStrategy.COPY_TO_S3

    def test_large_clean_table_recommends_copy(self, table_entity):
        # 200 GB, no lossy casts -> copy to S3 Tables is the default strategy.
        conv = ConversionResult(ddl="CREATE TABLE ...", lossy_casts=[], warnings=[], success=True)
        # drop the lossy/sync columns for this strategy check
        table_entity.columns = []
        res = EffortScorer().score(table_entity, conv)
        assert res.strategy == MigrationStrategy.COPY_TO_S3


class TestComplexityScorer:
    def test_no_surface_is_portable_low_confidence(self, clean_table_entity):
        res = ComplexityScorer().score(clean_table_entity, [])
        from fabric_assess.models import ComplexityCategory, ConfidenceLevel
        assert res.category == ComplexityCategory.PORTABLE
        assert res.confidence == ConfidenceLevel.LOW

    def test_merge_proc_is_rewrite(self, view_entity):
        a = SQLSurfaceAnalyzer()
        cons = a.detect("MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET x=1")
        res = ComplexityScorer().score(view_entity, cons)
        from fabric_assess.models import ComplexityCategory
        assert res.category in (ComplexityCategory.ADAPT, ComplexityCategory.REWRITE)


class TestIcebergConverter:
    def test_lossy_casts_detected(self, table_entity):
        res = IcebergConverter().convert(table_entity)
        assert res.success
        lossy_cols = {c.column for c in res.lossy_casts}
        assert "amount" in lossy_cols  # money -> decimal is lossy

    def test_clean_table_no_lossy(self, clean_table_entity):
        res = IcebergConverter().convert(clean_table_entity)
        assert res.success
        assert res.lossy_casts == []

    def test_view_produces_no_ddl(self, view_entity):
        res = IcebergConverter().convert(view_entity)
        assert res.success and res.ddl == ""


class TestAthenaDdl:
    """The DDL must be valid Athena Iceberg DDL for Amazon S3 Tables."""

    def _entity(self, table_entity, columns, name="dbo.Orders"):
        table_entity.full_name = name
        table_entity.columns = columns
        return table_entity

    def test_athena_type_names_and_lowercase(self, table_entity):
        from fabric_assess.models import ColumnSchema

        e = self._entity(
            table_entity,
            [
                ColumnSchema("OrderID", "bigint", False),
                ColumnSchema("Amount", "decimal(18,2)", True),
                ColumnSchema("CreatedAt", "datetime2", True),
            ],
        )
        res = IcebergConverter().convert(e)
        assert res.success
        assert "orderid bigint" in res.ddl
        assert " long" not in res.ddl
        assert "amount decimal(18,2)" in res.ddl
        assert "dbo_orders" in res.ddl
        assert "NOT NULL" not in res.ddl  # Athena DDL has no NOT NULL
        assert "LOCATION" not in res.ddl
        assert any("NOT NULL" in w for w in res.warnings)
        assert any("normalized" in w for w in res.warnings)

    def test_time_and_uniqueidentifier_flagged(self, table_entity):
        from fabric_assess.models import ColumnSchema

        e = self._entity(
            table_entity,
            [ColumnSchema("t", "time", True), ColumnSchema("g", "uniqueidentifier", True)],
            name="dbo.t",
        )
        res = IcebergConverter().convert(e)
        assert {c.column for c in res.lossy_casts} == {"t", "g"}
        assert " time" not in res.ddl and "timestamptz" not in res.ddl

    def test_case_collision_fails(self, table_entity):
        from fabric_assess.models import ColumnSchema

        e = self._entity(
            table_entity,
            [ColumnSchema("Id", "int", True), ColumnSchema("ID", "int", True)],
        )
        assert not IcebergConverter().convert(e).success


class TestCostRates:
    def test_storage_rate_matches_public_pricing(self):
        from fabric_assess.engine.athena import cost

        assert cost.S3_TABLES_STORAGE_PER_GB_MONTH == 0.0265
        lines = cost.estimate_athena_monthly(None, 1000.0)
        assert lines[0].monthly_low == 26.5


class TestWorkloadCollection:
    def test_collect_workload_from_query_insights(self):
        from fabric_assess.core.scanner import FabricScanner

        class _Cur:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, *params):
                self.sql = sql
                self.params = params
                assert params and params[0] < 0  # negative day offset
                assert "GETUTCDATE()" in sql and "TOP (?)" not in sql

            def fetchmany(self, n):
                assert n == 1000
                return [("SELECT TOP 5 * FROM t WHERE x = 'secret'", 10, 5, 1.0,
                         "SELECT", "2026-01-01")]

            def fetchall(self):
                # 1 TB over a 10-day span
                return [(100, 10, 5000, 1024.0 * 1024.0)]

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur(self)

        s = FabricScanner("srv", "db")
        s._conn = _Conn()
        profile, queries = s.collect_workload(lookback_days=90)
        assert profile.lookback_days == 30  # capped at Query Insights retention
        assert profile.has_data and profile.total_queries == 100
        assert abs(profile.monthly_scanned_tb - 3.0) < 1e-9
        assert "secret" not in queries[0].query

    def test_collect_workload_unavailable_returns_none(self):
        from fabric_assess.core.scanner import FabricScanner

        class _Conn:
            def cursor(self):
                raise RuntimeError("Invalid object name 'queryinsights...'")

        s = FabricScanner("srv", "db")
        s._conn = _Conn()
        assert s.collect_workload() == (None, [])


def test_supplied_fabric_spend_is_not_reported_unknown():
    from fabric_assess.engine.athena import cost
    from fabric_assess.models import FabricCapacityModel

    c = cost.build_comparison(cost.estimate_athena_monthly(None, 100.0), fabric_monthly=500.0)
    assert c.fabric_capacity_model == FabricCapacityModel.CAPACITY
    assert not any("UNKNOWN" in n for n in c.scope_notes)
