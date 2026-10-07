"""Directional cost model: Amazon Athena + Amazon S3 Tables storage.

All figures are DIRECTIONAL estimates with explicit confidence, mirroring the
reference tool's posture. Athena bills per TB scanned; S3 Tables bills for storage
and maintenance. The Fabric side is a capacity (CU / F-SKU) model the user must
confirm — we never print a hard Fabric number without labelling it UNKNOWN unless
the caller supplies one.
"""
from __future__ import annotations

from fabric_assess.models import (
    ConfidenceLevel,
    CostComparison,
    CostLine,
    FabricCapacityModel,
    WorkloadProfile,
)

# Public, directional default rates (us-east-1). The report labels these as
# estimates; a live Price List lookup can override them in a later pass.
ATHENA_PER_TB_SCANNED = 5.00          # USD per TB scanned
S3_TABLES_STORAGE_PER_GB_MONTH = 0.025  # USD per GB-month (storage)
S3_TABLES_MAINTENANCE_PER_GB_MONTH = 0.03  # USD per GB-month (compaction/maintenance, directional)
_GB_PER_TB = 1024.0


def estimate_athena_monthly(
    workload: WorkloadProfile | None,
    total_stored_gb: float,
) -> list[CostLine]:
    """Build the AWS-side monthly cost lines (Athena query + S3 Tables storage)."""
    lines: list[CostLine] = []

    # Storage (known once we have table bytes).
    storage_monthly = total_stored_gb * (
        S3_TABLES_STORAGE_PER_GB_MONTH + S3_TABLES_MAINTENANCE_PER_GB_MONTH
    )
    lines.append(
        CostLine(
            label="S3 Tables storage + maintenance",
            monthly=round(storage_monthly, 2),
            monthly_low=round(total_stored_gb * S3_TABLES_STORAGE_PER_GB_MONTH, 2),
            monthly_high=round(storage_monthly, 2),
            confidence=ConfidenceLevel.HIGH if total_stored_gb else ConfidenceLevel.LOW,
            source_note="S3 Tables public storage rate (us-east-1), directional",
        )
    )

    # Query scan cost (needs workload history for confidence).
    if workload and workload.has_data and workload.monthly_scanned_tb > 0:
        scan_monthly = workload.monthly_scanned_tb * ATHENA_PER_TB_SCANNED
        lines.append(
            CostLine(
                label="Athena query scan",
                monthly=round(scan_monthly, 2),
                monthly_low=round(scan_monthly * 0.6, 2),  # partition pruning upside
                monthly_high=round(scan_monthly, 2),
                confidence=ConfidenceLevel.MEDIUM,
                source_note=(
                    f"{workload.monthly_scanned_tb:.1f} TB/mo scanned "
                    f"@ ${ATHENA_PER_TB_SCANNED}/TB; Iceberg pruning may reduce"
                ),
            )
        )
    else:
        lines.append(
            CostLine(
                label="Athena query scan",
                monthly=None,
                monthly_low=None,
                monthly_high=None,
                confidence=ConfidenceLevel.LOW,
                source_note="no workload history — enable query insights for a scan estimate",
            )
        )

    return lines


def build_comparison(
    aws_lines: list[CostLine],
    *,
    fabric_monthly: float = 0.0,
    fabric_capacity_model: FabricCapacityModel = FabricCapacityModel.UNKNOWN,
    aws_region: str = "us-east-1",
) -> CostComparison:
    """Assemble a directional Fabric-vs-AWS cost comparison from the AWS lines."""
    aws_low = sum(x.monthly_low or 0.0 for x in aws_lines)
    aws_high = sum(x.monthly_high or x.monthly or 0.0 for x in aws_lines)

    delta_low = fabric_monthly - aws_high
    delta_high = fabric_monthly - aws_low

    scope_notes = [
        "All AWS figures are directional estimates (public us-east-1 rates).",
        "Validate against the AWS Pricing Calculator before any decision.",
    ]
    if fabric_capacity_model == FabricCapacityModel.UNKNOWN:
        scope_notes.append(
            "Fabric capacity cost is UNKNOWN — supply your F-SKU monthly spend for a delta."
        )

    return CostComparison(
        fabric_capacity_model=fabric_capacity_model,
        fabric_monthly=fabric_monthly,
        fabric_breakdown=[],
        aws_lines=aws_lines,
        aws_monthly_low=round(aws_low, 2),
        aws_monthly_high=round(aws_high, 2),
        monthly_delta_low=round(delta_low, 2),
        monthly_delta_high=round(delta_high, 2),
        annual_savings_low=round(delta_low * 12, 2),
        annual_savings_high=round(delta_high * 12, 2),
        migration_onetime=0.0,
        breakeven_months_low=0.0,
        breakeven_months_high=0.0,
        compute_confidence=ConfidenceLevel.LOW
        if fabric_capacity_model == FabricCapacityModel.UNKNOWN
        else ConfidenceLevel.MEDIUM,
        aws_pricing_region=aws_region,
        scope_notes=scope_notes,
    )
