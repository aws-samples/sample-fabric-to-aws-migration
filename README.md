# Assess a Microsoft Fabric warehouse for migration to an AWS lakehouse

Assess migrating a **Microsoft Fabric** warehouse to an **AWS lakehouse** — data
in **Amazon S3 Tables (Apache Iceberg)**, queried by **Amazon Redshift
Serverless** or **Amazon Athena**. The `fabric-assess` CLI reads Fabric
metadata (read-only, never table data) through the warehouse's SQL analytics
endpoint, scores every table on two independent axes — **Migration Effort**
(moving the data) and **Query Complexity** (keeping the T-SQL running) — and
generates Iceberg DDL, load guidance, an engine recommendation, and a
directional Fabric-vs-AWS cost comparison as HTML + JSON reports.

It **assesses; it does not execute the migration** — and it needs no AWS
account to run.

> **Alpha / companion sample** for the AWS Multicloud blog post *"Assess a
> Microsoft Fabric warehouse for migration to an AWS lakehouse"*. Cost figures
> are directional estimates, labelled by confidence. Review assessments with
> your AWS specialist team before they inform a decision.

## The Fabric angle

Unlike a BigQuery source, a Fabric Warehouse already stores its data as **open
Delta/Parquet in OneLake**, which OneLake can expose **as Apache Iceberg
automatically**. So the data-movement problem is small — most tables score
**AUTO** on Migration Effort — and the real work is the **T-SQL rewrite** to
the AWS engine. This tool makes that split explicit and scored before any
migration commitment.

## Status

Alpha MVP — a working end-to-end assessment pipeline:

- **Scanner** reads Fabric metadata over the SQL analytics endpoint (TDS /
  Entra token auth; no secret written to disk).
- **Two-axis scoring** — Migration Effort (inverted toward AUTO for the
  open-format source) and Query Complexity (T-SQL constructs).
- **Iceberg converter** maps T-SQL/Delta types and flags lossy casts.
- **Athena engine** — rewrite guidance + best-effort `sqlglot` translation and a
  directional cost model.
- **Reports** — paired HTML + JSON, plus a checksummed collect-then-report bundle.

Redshift as a second query engine and a live AWS Price List lookup are planned
follow-ups. See `docs/PORTING.md` for the module map and open VERIFY items.

## Install

```bash
pip install -e .          # add .[dev] for the test + lint tooling
```

The scanner needs the ODBC Driver 18 for SQL Server and `pyodbc` /
`azure-identity` at run time; the offline `report --bundle` path does not.

## Usage

```bash
# Full assessment against a live Fabric Warehouse
fabric-assess assess \
  --server <workspace>.datawarehouse.fabric.microsoft.com \
  --warehouse SalesWH --out reports --format both

# Collect where your Fabric credentials live, report anywhere (offline)
fabric-collect --server <...> --warehouse SalesWH --out collected
fabric-assess report --bundle collected/bundle --out reports
```

## Develop

```bash
pytest                       # unit + integration + property tests
ruff check src tests         # lint
bandit -c bandit.yml -r src  # security scan
```

## License

MIT-0 (aws-samples convention).
