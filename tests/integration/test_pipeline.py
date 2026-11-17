"""Integration tests: bundle round-trip, analyzer pipeline, report generation."""
from __future__ import annotations

import json

from fabric_assess.bundle.loader import BundleError, BundleLoader
from fabric_assess.bundle.models import Bundle, QueryRecord
from fabric_assess.bundle.writer import BundleWriter
from fabric_assess.core.analyzer import Analyzer
from fabric_assess.report.html_writer import HtmlWriter
from fabric_assess.report.json_writer import JsonWriter


def _bundle(entities) -> Bundle:
    return Bundle(
        workspace="ws",
        warehouse="wh",
        aws_region="us-east-1",
        entities=entities,
        queries=[QueryRecord(query="SELECT ?", allocated_cpu_time_ms=10)],
        collector_version="0.1.0",
    )


class TestBundleRoundTrip:
    def test_round_trip(self, tmp_path, table_entity, view_entity):
        b = _bundle([table_entity, view_entity])
        bundle_dir = BundleWriter().write(b, str(tmp_path))
        loaded = BundleLoader().load(bundle_dir)
        assert len(loaded.entities) == 2
        assert loaded.queries and loaded.queries[0].allocated_cpu_time_ms == 10

    def test_checksum_rejects_tamper(self, tmp_path, table_entity):
        b = _bundle([table_entity])
        bundle_dir = BundleWriter().write(b, str(tmp_path))
        with open(f"{bundle_dir}/tables.json", "a", encoding="utf-8") as f:
            f.write(" ")
        try:
            BundleLoader().load(bundle_dir)
            raise AssertionError("expected checksum rejection")
        except BundleError:
            pass


class TestAnalyzerPipeline:
    def test_analyze_produces_summary(self, table_entity, clean_table_entity, view_entity):
        a = Analyzer().analyze(
            [table_entity, clean_table_entity, view_entity], workspace="ws/wh"
        )
        assert a.summary.total_tables == 2
        assert a.summary.total_entities == 3
        # one clean table AUTO, one lossy/sync table ASSISTED
        assert a.summary.effort_counts["AUTO"] >= 1
        assert a.summary.complexity_counts["ADAPT"] + a.summary.complexity_counts["REWRITE"] >= 1

    def test_reports_written(self, tmp_path, table_entity, view_entity):
        a = Analyzer().analyze([table_entity, view_entity], workspace="ws/wh")
        json_path = JsonWriter().write(a, str(tmp_path))
        html_path = HtmlWriter().write(a, str(tmp_path))
        with open(json_path, encoding="utf-8") as f:
            data = json.loads(f.read())
        assert data["summary"]["total_tables"] == 1
        with open(html_path, encoding="utf-8") as f:
            assert "Migration Effort" in f.read()


class TestWorkloadPipeline:
    def test_workload_round_trips_and_feeds_analysis(self, tmp_path, table_entity, view_entity):
        from fabric_assess.models import WorkloadProfile

        b = _bundle([table_entity, view_entity])
        b.workload = WorkloadProfile(has_data=True, total_queries=5, days_sampled=1,
                                     monthly_scanned_tb=2.0)
        b.queries = [QueryRecord(query="SELECT a FROM t CROSS APPLY OPENJSON(j)")]
        loaded = BundleLoader().load(BundleWriter().write(b, str(tmp_path)))
        assert loaded.workload and loaded.workload.monthly_scanned_tb == 2.0

        a = Analyzer().analyze(
            loaded.entities, workspace="ws/wh", workload=loaded.workload,
            query_log_text=[q.query for q in loaded.queries],
        )
        assert "OPENJSON" in a.summary.workload_constructs
        scan = next(x for x in a.cost.aws_lines if x.label == "Athena query scan")
        assert scan.monthly == 10.0


class TestTableComplexityAttribution:
    def test_table_scored_from_referencing_view(self, table_entity, view_entity):
        a = Analyzer().analyze([table_entity, view_entity], workspace="ws/wh")
        table = next(r for r in a.entities if r.full_name == "dbo.orders")
        assert table.effort is not None and table.complexity is not None
        assert "CROSS_APPLY" in table.complexity.flags
        assert "name this table" in table.complexity.reasoning

    def test_unreferenced_table_has_no_complexity(self, clean_table_entity):
        a = Analyzer().analyze([clean_table_entity], workspace="ws/wh")
        assert a.entities[0].complexity is None


class TestReportsDoNotLeakLiterals:
    def test_translated_sql_has_no_literals(self, tmp_path, view_entity):
        view_entity.view_query = (
            "SELECT TOP 5 id FROM dbo.orders "
            "WHERE api_key = 'sk-SECRET-123' AND region_id = 98765"
        )
        a = Analyzer().analyze([view_entity], workspace="ws/wh")
        json_path = JsonWriter().write(a, str(tmp_path))
        html_path = HtmlWriter().write(a, str(tmp_path))
        for path in (json_path, html_path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            assert "sk-SECRET-123" not in text
            assert "98765" not in text
