# Porting `bq-assess` -> `fabric-assess`

Deep research + module-by-module porting plan for the companion tool to the
blog *"Assess a Microsoft Fabric warehouse for migration to an AWS lakehouse"*
(ticket Multicloud-41).

Status key: **VERIFIED** (checked against a cited source this session),
**VERIFY** (confirm against a live Fabric tenant / docs before publish),
**ASSUMPTION** (reasoned inference).

---

## 1. What the reference tool actually is

`aws-samples/sample-bigquery-to-aws-migration` (`bq-assess`) is a **read-only,
metadata-only** Python CLI. It never touches data rows and needs no AWS account.
It scans a source warehouse's metadata (+ optional query logs), scores every
table on **two independent axes**, and emits Iceberg DDL, load/sync DML,
query-rewrite guidance, an engine recommendation, and a directional cost
comparison as paired HTML+JSON reports.

**Verified architecture** (read from source, not just the README):

- **Package**: `src/bq_assess/`, installed via `pyproject.toml` (setuptools).
  Two console entry points: `bq-assess` (full pipeline) and `bq-collect`
  (collector only). Deps: `click`, `rich`, `jinja2`, `pyyaml`, `sqlglot`,
  plus the GCP SDKs.
- **Pipeline seam** (the key design decision, dated 2026-07-08 in the source):
  `collect(params) -> Bundle` does everything that touches the source cloud;
  `analyze_and_report(bundle, params)` is pure computation + report writing.
  `assess` composes both in-process; `report --bundle` runs only the analysis
  half, fully offline, on a checksummed bundle a customer produced. This is the
  "collect where creds live, report on the analyst side" story.
- **16 pipeline stages** in `analyze_and_report`: SQL-surface detection ->
  query attribution -> Iceberg conversion -> effort scoring -> relationship
  inference -> complexity scoring -> region/rate replay -> cost estimation ->
  engine recommendation -> rewrite guidance -> SQL translation -> placement ->
  storage placement -> cost assembly -> assessment assembly -> write
  deliverables.
- **Module layout** under `src/bq_assess/`:
  - `cli.py` / `collect_cli.py` — entry points + orchestration
  - `collector.py` / `scanner.py` — source-touching metadata reads
  - `models.py` — all normative dataclasses + enums (the shared contract)
  - `bundle/` — checksummed collect-then-report bundle (writer, loader)
  - `core/` — SQL surface analysis, relationships, pricing, query attribution
  - `scoring/` — the two-axis scorers (`effort.py`, `complexity.py`)
  - `engine/` — per-engine (Redshift / Athena) rewrite, placement, cost,
    recommendation, migration DML
  - `targets/iceberg/` — DDL conversion to Iceberg
  - `report/` — HTML (Jinja2 templates) + JSON writers

### The two axes (verbatim meaning from the source models + CONTEXT.md)

- **Migration Effort** — `AUTO | ASSISTED | MANUAL`. How hard it is to *move*
  a table into the Storage Target (Amazon S3 Tables / Iceberg). Driven by data
  volume tiers, lossy type casts, ongoing-sync need, non-clean partition/sort
  mappings. **Tables only.**
- **Query Complexity** — `PORTABLE | ADAPT | REWRITE`. How hard it is to keep
  the existing query workload *running* on the target engine. Driven by
  source-dialect-specific SQL constructs. **Every entity with a SQL surface.**

They are never combined into one number. That is the whole design thesis.

---

## 2. The reframing — why Fabric inverts the effort axis

The sample's Migration Effort axis is heavy **because BigQuery stores data in a
proprietary columnar format that must be exported**. Microsoft Fabric is the
opposite:

- **VERIFIED**: Fabric Warehouse stores relational data as **Delta Lake /
  Delta Parquet in OneLake** — an open format — with full T-SQL.
  (learn.microsoft.com/fabric/data-warehouse/architecture)
- **VERIFIED**: OneLake can expose those Delta tables **as Apache Iceberg
  automatically** via metadata virtualization, no data copy or movement.
  (blog.fabric.microsoft.com — "access your Delta Lake tables as Iceberg
  automatically"; learn.microsoft.com/fabric/onelake/onelake-iceberg-tables)

**Consequence for the code**: the Effort scorer's dominant signal flips. For
BigQuery it was "how proprietary / how large is the export". For Fabric most
tables trend toward **AUTO**, because the bytes are already open-format Parquet
sitting in OneLake. The real effort questions become:

1. **Read-in-place vs copy-to-S3-Tables** — query the OneLake Iceberg surface
   from AWS, or physically copy into Amazon S3 Tables. (VERIFY cross-cloud
   in-place query reliability/auth/egress — the blog's main technical risk.)
2. **Type mapping** — T-SQL / Delta types with no clean Iceberg equivalent.
3. **Partition / sort mapping** — same idea as the sample.

So **Query Complexity (T-SQL rewrite) becomes the main event**, not effort.
No existing content frames a Fabric->AWS move this way. That is the blog's
differentiation (prior-art check: VERIFIED no Fabric->AWS assessment tool or
blog exists as of 2026-09-30; closest is Synapse->Redshift via AWS SCT, a
different source, tool, and target).

---

## 3. Verified Fabric metadata surfaces (grounds the scanner)

All reachable **read-only** through the **SQL analytics endpoint** that every
Fabric Warehouse / Lakehouse auto-provisions. These replace BigQuery
`INFORMATION_SCHEMA` + the BQ SDK.

| Need | Fabric surface | Status |
|---|---|---|
| Tables / schemas / columns | `INFORMATION_SCHEMA.TABLES` / `.COLUMNS`, and `sys.tables` / `sys.columns` / `sys.types` (sys.* is the reliable one per MS docs) | VERIFIED exist; VERIFY exact column mapping to our models |
| View / proc / function bodies | `sys.sql_modules.definition` (+ `INFORMATION_SCHEMA.VIEWS`, `sys.objects` for type) | VERIFIED surface; VERIFY body completeness for procs |
| Workload history (the cost/complexity fuel) | `queryinsights.exec_requests_history` | **VERIFIED columns** (see below) |
| Frequent-query rollup | `queryinsights.frequently_run_queries` | VERIFIED exists |
| Partitioning / distribution | Fabric Warehouse has limited explicit partitioning; Delta layout + (preview) data clustering. | VERIFY what is introspectable |

**VERIFIED columns on `queryinsights.exec_requests_history`** (the analog to
BigQuery `INFORMATION_SCHEMA.JOBS`, which powers workload + cost in the sample):
`distributed_statement_id`, `command` (full query text), `query_hash` (group
shapes), `statement_type` (SELECT/INSERT/UPDATE/DELETE), `total_elapsed_time_ms`,
`allocated_cpu_time_ms`, `data_scanned_remote_storage_mb`, `row_count`,
`submit_time` / `start_time` / `end_time`, `status`, `label`.
(learn.microsoft.com/sql/relational-databases/system-views/queryinsights-exec-requests-history-transact-sql?view=fabric)

**Permission note (matters for positioning)**: query insights needs
**Contributor or higher** on a Premium-capacity workspace. The schema reads
(sys.* / INFORMATION_SCHEMA) are lighter. So the tool's confidence tiers map
cleanly: schema-only (low) -> + view/proc bodies (medium) -> + query insights
(high), exactly like the sample's LOW/MEDIUM/HIGH ladder.

**Connectivity**: the SQL analytics endpoint speaks the **TDS protocol** (SQL
Server wire protocol). Python reaches it with `pyodbc` (ODBC Driver 18) or
`pytds` + Azure AD / Entra token auth. This replaces `google-cloud-bigquery`.
VERIFY the exact auth flow (service principal vs interactive Entra) against
your Azure account.

---

## 4. Module-by-module port

Legend: **KEEP** (chassis — copy structure, minimal change) /
**ADAPT** (rework internals) / **REWRITE** (source-specific, new code).

| Reference module | Action | What changes |
|---|---|---|
| `pyproject.toml` | ADAPT | Rename to `fabric-assess`; entry points `fabric-assess` / `fabric-collect`; swap GCP SDKs for `pyodbc`/`pytds` + `azure-identity`; keep `click` `rich` `jinja2` `pyyaml` `sqlglot` |
| `models.py` | KEEP (mostly) | Enums + dataclasses are source-agnostic. Rename BQ-flavored fields (`bq_pricing_model` -> `fabric_capacity_model`; `view_query`/`mview_query` stay generic). Keep both axes, confidence levels, cost comparison shape |
| `cli.py` / `collect_cli.py` | KEEP shape | Same `assess` / `report --bundle` split, same stage orchestration. Swap `--gcp-project` -> `--fabric-workspace` / `--warehouse`; `--use-adc` -> Entra auth flags |
| `scanner.py` / `collector.py` | **REWRITE** | Connect to SQL analytics endpoint (TDS); read sys.* + INFORMATION_SCHEMA + queryinsights instead of BQ SDK / INFORMATION_SCHEMA.JOBS |
| `bundle/` | KEEP | Checksummed JSON bundle is cloud-agnostic. Only the provenance fields rename |
| `core/sql_surface.py` | **REWRITE rules** | Detect **T-SQL** constructs (OPENJSON, CROSS/OUTER APPLY, STRING_AGG, MERGE, TOP, PIVOT/UNPIVOT, stored procs, CLR) instead of BQ constructs (UNNEST, ARRAY_*, JS UDF, struct nav) |
| `core/relationships.py` | KEEP | JOIN-clause inference from view SQL is dialect-tolerant via sqlglot. VERIFY sqlglot's `tsql` dialect coverage |
| `core/pricing*` | **REWRITE** | Fabric **capacity (CU / F-SKU)** billing, not BQ on-demand/slots. AWS side unchanged |
| `scoring/effort.py` | **ADAPT (the inversion)** | Dominant signal flips to open-format-already-present -> mostly AUTO. New signals: in-place-vs-copy decision, type mapping, partition mapping |
| `scoring/complexity.py` | ADAPT | Re-tune weights for T-SQL construct classes; the machinery (categories, confidence) stays |
| `engine/` (redshift, athena) | KEEP | Target is **identical** to the sample — S3 Tables (Iceberg) + Redshift Serverless / Athena. Rewrite rules change source dialect to T-SQL |
| `targets/iceberg/converter.py` | ADAPT | Map **T-SQL / Delta types** -> Iceberg instead of BQ types -> Iceberg |
| `report/` | KEEP | Three-interface HTML+JSON (Landing / Effort / Complexity) is source-agnostic. Copy relabeled |

### T-SQL -> Redshift/Athena dialect map (expand + VERIFY each equivalent)

- `STRING_AGG(expr, sep)` -> `LISTAGG(expr, sep) WITHIN GROUP (...)`
- `OPENJSON` / `JSON_VALUE` / `JSON_QUERY` -> Redshift `SUPER` + JSON navigation / Athena JSON funcs
- `CROSS APPLY` / `OUTER APPLY` -> lateral unnest / join patterns
- `TOP n` -> `LIMIT n`
- `ISNULL` -> `NVL`/`COALESCE`; `GETDATE()` -> `CURRENT_DATE`/`SYSDATE`; `DATEADD`/`DATEDIFF` arg-order drift
- `MERGE` semantics differences (Iceberg `MERGE` vs T-SQL `MERGE`)
- `PIVOT` / `UNPIVOT` -> manual `CASE` aggregation / unnest
- T-SQL stored procedures -> Redshift stored procedures (PL/pgSQL-like) or external orchestration
- CLR / external procs -> no equivalent, manual rewrite (the REWRITE bucket)

sqlglot has a `tsql` source dialect and `redshift` / `athena`(`presto`/`trino`)
targets, so the sample's translate() path is reusable — VERIFY coverage of the
constructs above and treat misses as explicit "manual rewrite" flags, exactly
as the sample does for JavaScript UDFs.

---

## 5. Build order (MVP-first)

1. **Scaffold** — `pyproject.toml`, package skeleton, `models.py` (done:
   skeleton dirs created). KEEP models nearly verbatim.
2. **Collector + scanner** — the only source-touching code; the part your
   Azure account validates first. Target the VERIFIED metadata surfaces above.
   Produce a bundle.
3. **Scoring + report** — port the two scorers (effort inverted) and the
   HTML/JSON writers; run them on the bundle fully offline.
4. **Engine + cost** — T-SQL rewrite rules + Fabric capacity cost model.
5. **Validate** against a real Fabric tenant (your Azure creds), capture
   screenshots for the blog, resolve every VERIFY above.

## 6. Open VERIFY items blocking publish

1. Cross-cloud **in-place Iceberg query** from Athena/Redshift against OneLake
   (auth + egress + reliability) — if shaky, the narrative becomes copy-to-S3.
2. Exact **auth flow** to the SQL analytics endpoint (service principal vs
   interactive Entra; least-privilege role for metadata + query insights).
3. **Fabric capacity cost unit** (CU-seconds / F-SKU rate) terminology + a
   defensible rate before any number appears.
4. Each **T-SQL construct's** Redshift/Athena equivalent + sqlglot coverage.
5. Whether to assess **Fabric Warehouse only** or also **Lakehouse** (Lakehouse
   is closer to a no-op — already Delta files).
