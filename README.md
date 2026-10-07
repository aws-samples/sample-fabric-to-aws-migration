# fabric-assess

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

Scaffolding in progress. See `docs/PORTING.md` for the module-by-module plan
ported from `aws-samples/sample-bigquery-to-aws-migration`, the verified Fabric
metadata surfaces, the T-SQL -> Redshift/Athena dialect map, and the open
VERIFY items blocking publish.

## Prerequisites (planned)

- Python 3.9+
- ODBC Driver 18 for SQL Server (the Fabric SQL analytics endpoint speaks TDS)
- A Microsoft Entra identity with read access to the Fabric Warehouse, plus
  Contributor on the workspace for query-insights workload data (higher
  confidence; schema-only scan works without it)

## Two ways to run (planned, mirroring the reference)

1. **Collect, then report** — run the lightweight collector where your Fabric
   credentials live; it writes a checksummed JSON bundle; your AWS team
   generates the report from it, fully offline.
2. **Full assessment** — scan and report in one step in the environment with
   Fabric access.

## License

MIT-0 (aws-samples convention).
