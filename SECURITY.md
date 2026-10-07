# Security Policy

## Reporting a vulnerability

If you discover a potential security issue in this project, we ask that you
notify AWS/Amazon Security via our
[vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/)
or directly via email to aws-security@amazon.com.

Please do **not** create a public issue.

## Security posture of this tool

`fabric-assess` is a read-only, metadata-only assessment tool with no persistent
service and no stored credentials:

- It authenticates with **your** Microsoft Entra identity (service principal or
  interactive sign-in) and never writes a credential to disk.
- It reads **metadata only** — never table row data.
- Query text captured for workload analysis is **anonymized** (string and numeric
  literals stripped) before it is stored or written to any report.
- It runs no telemetry and sends nothing off the machine it runs on.
- The static analysis configuration (`bandit.yml`) and the property-based test
  suite include explicit checks that generated reports contain no credential
  material.
