"""fabric-collect CLI — the collector half of the collect-then-report seam.

Runs where Fabric credentials live. Scans the warehouse metadata and writes a
checksummed JSON bundle you can review and hand to whoever generates the report
(``fabric-assess report --bundle``). It touches Fabric but never AWS, and writes
no credential to disk.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import click

from fabric_assess import __version__
from fabric_assess.bundle.models import Bundle
from fabric_assess.bundle.writer import BundleWriter


@click.command(help="Collect a Fabric Warehouse metadata bundle for offline assessment.")
@click.version_option(__version__, prog_name="fabric-collect")
@click.option("--server", required=True, help="Fabric SQL analytics endpoint host.")
@click.option("--warehouse", required=True, help="Warehouse / lakehouse (database) name.")
@click.option("--out", "out_dir", default="collected", show_default=True, help="Output directory.")
@click.option("--aws-region", default="us-east-1", show_default=True,
              help="Intended AWS target region (recorded in the bundle).")
def main(server, warehouse, out_dir, aws_region) -> None:
    from fabric_assess.core.scanner import FabricScanner, ScannerError

    scanner = FabricScanner(server=server, database=warehouse)
    try:
        entities = list(scanner.scan())
    except ScannerError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    bundle = Bundle(
        workspace=server,
        warehouse=warehouse,
        aws_region=aws_region,
        entities=entities,
        failures=scanner.failures,
        collector_version=__version__,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    scanner.close()

    bundle_dir = BundleWriter().write(bundle, out_dir)
    click.echo(f"wrote bundle to {bundle_dir}")
    click.echo("Review the bundle (it contains your view/procedure SQL) before sharing.")


if __name__ == "__main__":  # pragma: no cover
    main()
