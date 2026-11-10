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
