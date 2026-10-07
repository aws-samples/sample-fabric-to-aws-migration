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

    def test_large_clean_table_recommends_in_place(self, table_entity):
        # 200 GB, no lossy casts -> in-place query is the recommended strategy.
        conv = ConversionResult(ddl="CREATE TABLE ...", lossy_casts=[], warnings=[], success=True)
        # drop the lossy/sync columns for this strategy check
        table_entity.columns = []
        res = EffortScorer().score(table_entity, conv)
        assert res.strategy == MigrationStrategy.IN_PLACE


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
