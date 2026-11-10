# Deployment record

Complete one copy for each pilot or production deployment.

| Item | Recorded value |
|---|---|
| Product commit and archive SHA-256 | |
| Pilot-gate approver, date and PG17/18 CI run URLs | |
| Per-customer standalone bundle SHA-256 | |
| Generator hash verified before each export | |
| Installed extension SQL SHA-256 verified before each export | |
| Generator environment and named owner | |
| pg_jsontailor version | |
| PostgreSQL version and provider | |
| Installation route | |
| Generator and target PostgreSQL majors | |
| View names and specification hashes | |
| Exact reviewed UTF-8 SQL file and SHA-256 | |
| Review approver, timestamp, generator/schema context | |
| Observed deployed catalogue manifest and SHA-256 | |
| Private diagnostic location/access/deletion deadline | |
| PG17/18/pinned-PG19 validation results or NOT_RUN reasons | |
| Actual customer-network raw-upstream probe | PASS/FAIL/NOT_RUN and origin |
| Application roles and capabilities | |
| Raw PostgREST exposure | Must be none |
| Boundary proxy and configuration | |
| `statement_timeout`, request limit and `db-max-rows` | |
| Ingress header/body deadline, request buffering and rate limit | |
| Backup owner and last restore rehearsal | |
| RPO and RTO | |
| Rollback owner and procedure | |
| Customer security contact and Baremon security contact | |
| Pilot contact, backup, private channel and vulnerability-reporting route | |
| Pre-send owner gate DONE/WAIVED decisions, dates and private evidence | |
| Customer acceptance owner | |

Attach successful `__check()`, smoke read/write evidence and the restore rehearsal. A synthetic-pilot PASS is technical evidence only, not customer or release approval.
