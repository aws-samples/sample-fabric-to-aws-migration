# Privacy Policy

This document describes how the `fabric-assess` CLI handles data. The tool is
maintained by Amazon Web Services and distributed under the MIT-0 license.

## What this tool is

`fabric-assess` is a read-only command-line tool. It scans Microsoft Fabric
Warehouse **metadata** (schemas, column types, view/procedure definitions, and —
optionally — query-insights statistics from the SQL analytics endpoint) and
writes assessment reports (HTML + JSON) to your local filesystem.

## Data the tool collects

**The tool itself collects no data.** It runs no telemetry, analytics, crash
reporting, or usage tracking. It sends nothing to the tool maintainers or to AWS.

## Data the tool reads and where it goes

- The CLI reads Fabric metadata using **your** Microsoft Entra credentials
  (a service principal or interactive sign-in you supply). It never reads table
  row data.
- All output (reports, optional collection bundles) is written **locally** to
  paths you choose. Nothing is uploaded anywhere by the tool.
- Optional workload analysis reads anonymized query text from
  `queryinsights.exec_requests_history` (string and numeric literals stripped).
  You can disable query-text collection entirely with `--exclude-query-text`.
- The `fabric-collect` mode produces a plain-JSON, checksummed bundle designed to
  be **reviewed by you before sharing**. Whether and with whom you share a bundle
  is entirely your decision.

## Microsoft and AWS API calls

- Fabric metadata reads are governed by your agreement with Microsoft.
- Optional live pricing lookups query the public AWS Price List API; these
  requests contain no workspace data — only region/SKU filters.

## Credentials

The tool never requests secrets or credentials beyond the Microsoft Entra
authentication you already have configured. It never writes a credential to disk.
Its property-based test suite includes explicit checks that generated reports
contain no credential material.

## Changes

Changes to this policy are tracked in the repository's git history.
