# Changelog

All notable changes to `pg_jsontailor` are documented in this file.

## 0.2.0 — unreleased

- Added catalogue-driven generation of writable JSON document views.
- Added root, optional object and optional array mappings.
- Added insert, full-document replacement and ETag-guarded delete paths.
- Added document ETags and optimistic concurrency checks.
- Added validation for specifications, catalogue objects and supported types.
- Added invoker-rights and row-level security coverage.
- Added PGXS regression and isolation tests.
- Added PostgREST and Oracle ORDS comparison scenarios.
- Reworked array reconciliation to use set-based operations.
- Added typed key reconciliation, nested input validation and source-relation binding checks.
- Added synthetic pilot and reproducible benchmark tooling.
- Added PostgreSQL 17, 18 and 19 compatibility and continuous-test coverage.
- Documented and tested native SQL MERGE for insert, complete replacement, ETag-checked delete and DO NOTHING actions.
- Added post-lock ETag validation for direct SQL and MERGE delete actions.
- Documented and tested inline complete-document construction from ID, ETag and changed values.

This release is intended for evaluation and controlled pilots on PostgreSQL 17, 18 and 19. PostgreSQL 19 coverage uses the current release candidate until the final release is available.
