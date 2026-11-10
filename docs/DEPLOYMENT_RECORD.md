# Deployment record

Complete one copy for each pilot or production deployment.

| Item | Recorded value |
|---|---|
| Product commit and archive SHA-256 | |
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
| Backup owner and last restore rehearsal | |
| RPO and RTO | |
| Rollback owner and procedure | |
| Security contact | |
| Customer acceptance owner | |

Attach successful `__check()`, smoke read/write evidence and the restore rehearsal. A synthetic-pilot PASS is technical evidence only, not customer or release approval.
