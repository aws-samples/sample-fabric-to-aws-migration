"""fabric-assess CLI — the full assessment entry point.

Two subcommands mirror the reference tool's seam:

  assess   Scan a live Fabric Warehouse and write the report in one step.
  report   Generate the report offline from a previously collected bundle
           (``--bundle <dir>``), with no Fabric access.

Both ultimately call the same offline Analyzer, so a live scan and a bundle
produce identical reports.
"""
from __future__ import annotations

import sys

import click

from fabric_assess import __version__
from fabric_assess.core.analyzer import Analyzer
from fabric_assess.report.html_writer import HtmlWriter
from fabric_assess.report.json_writer import JsonWriter


def _write_reports(assessment, out_dir: str, fmt: str) -> list[str]:
    written: list[str] = []
    if fmt in ("json", "both"):
        written.append(JsonWriter().write(assessment, out_dir))
    if fmt in ("html", "both"):
        written.append(HtmlWriter().write(assessment, out_dir))
    return written


@click.group(help="Assess migrating a Microsoft Fabric warehouse to an AWS lakehouse.")
@click.version_option(__version__, prog_name="fabric-assess")
def main() -> None:  # pragma: no cover - thin dispatch
    pass


@main.command("assess", help="Scan a live Fabric Warehouse and generate a report.")
@click.option("--server", required=True, help="Fabric SQL analytics endpoint host.")
@click.option("--warehouse", required=True, help="Warehouse / lakehouse (database) name.")
@click.option("--out", "out_dir", default="reports", show_default=True, help="Output directory.")
@click.option("--format", "fmt", type=click.Choice(["json", "html", "both"]), default="both",
              show_default=True)
@click.option("--fabric-monthly", type=float, default=0.0,
              help="Your Fabric F-SKU monthly spend (USD) for a cost delta; omit if unknown.")
@click.option("--aws-region", default="us-east-1", show_default=True)
@click.option("--username", default=None, help="SQL-auth username (testing against a local "
              "SQL Server; omit for a real Fabric warehouse, which uses Entra).")
@click.option("--password", default=None, help="SQL-auth password (testing only).")
@click.option("--port", default=1433, show_default=True, type=int)
@click.option("--trust-server-certificate", is_flag=True, default=False,
              help="Trust a self-signed server certificate (local SQL Server testing only).")
def assess_cmd(server, warehouse, out_dir, fmt, fabric_monthly, aws_region,
               username, password, port, trust_server_certificate) -> None:
    # Imported here so `report --bundle` works without the native ODBC driver.
    from fabric_assess.core.scanner import FabricScanner, ScannerError

    scanner = FabricScanner(
        server=server,
        database=warehouse,
        username=username,
        password=password,
        port=port,
        trust_server_certificate=trust_server_certificate,
    )
    try:
        entities = list(scanner.scan())
    except ScannerError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    assessment = Analyzer().analyze(
        entities,
        workspace=f"{server}/{warehouse}",
        failures=scanner.failures,
        fabric_monthly=fabric_monthly,
        aws_region=aws_region,
    )
    scanner.close()

    for path in _write_reports(assessment, out_dir, fmt):
        click.echo(f"wrote {path}")


@main.command("report", help="Generate a report offline from a collected bundle.")
@click.option("--bundle", "bundle_dir", required=True, help="Path to a bundle directory.")
@click.option("--out", "out_dir", default="reports", show_default=True, help="Output directory.")
@click.option("--format", "fmt", type=click.Choice(["json", "html", "both"]), default="both",
              show_default=True)
@click.option("--fabric-monthly", type=float, default=0.0,
              help="Your Fabric F-SKU monthly spend (USD) for a cost delta; omit if unknown.")
def report_cmd(bundle_dir, out_dir, fmt, fabric_monthly) -> None:
    from fabric_assess.bundle.loader import BundleError, BundleLoader

    try:
        bundle = BundleLoader().load(bundle_dir)
    except BundleError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    assessment = Analyzer().analyze(
        bundle.entities,
        workspace=f"{bundle.workspace}/{bundle.warehouse}",
        failures=bundle.failures,
        workload=bundle.workload,
        fabric_monthly=fabric_monthly,
        aws_region=bundle.aws_region,
    )
    for path in _write_reports(assessment, out_dir, fmt):
        click.echo(f"wrote {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
