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
