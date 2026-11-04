# Changelog

All notable changes to `pg_jsontailor` are documented in this file.

## 0.2.0 — 2026-11-04

- Added catalogue-driven generation of writable JSON document views.
- Added root, optional object and optional array mappings.
- Added insert, full-document replacement and delete triggers.
- Added document ETags and optimistic concurrency checks.
- Added validation for specifications, catalogue objects and supported types.
- Added invoker-rights and row-level security coverage.
- Added PGXS regression and isolation tests.
- Added PostgREST and Oracle ORDS comparison scenarios.
- Reworked array reconciliation to use set-based operations.
- Added synthetic pilot and reproducible benchmark tooling.
- Added PostgreSQL 17, 18 and 19 compatibility and continuous-test coverage.

This release is intended for evaluation and controlled pilots on PostgreSQL 17, 18 and 19. PostgreSQL 19 coverage uses the current release candidate until the final release is available.
