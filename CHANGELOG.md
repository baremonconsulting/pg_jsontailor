# Changelog

All notable changes to `pg_jsontailor` are documented in this file.

## 0.2.0 — unreleased

`0.2.0` remains unreleased until an immutable `v0.2.0` tag and checksummed
archive are published. Earlier repository snapshots are development builds,
regardless of commit-message wording.

- Added catalogue-driven generation of writable JSON document views.
- Added root, optional object and optional array mappings.
- Added insert, full-document replacement and ETag-guarded delete paths.
- Added document ETags and optimistic concurrency checks.
- Added validation for specifications, catalogue objects and supported types.
- Added invoker-rights and row-level security coverage.
- Added PGXS regression and isolation tests.
- Added PostgREST integration scenarios.
- Reworked array reconciliation to use set-based operations.
- Added typed key reconciliation, nested input validation and source-relation binding checks.
- Added synthetic pilot and reproducible benchmark tooling.
- Added PostgreSQL 17, 18 and 19 compatibility and continuous-test coverage.
- Documented and tested native SQL MERGE for insert, complete replacement, ETag-checked delete and DO NOTHING actions.
- Added post-lock ETag validation for direct SQL and MERGE delete actions.
- Documented and tested inline complete-document construction from ID, ETag and changed values.
- Bound generated write paths to the physical source attributes selected by the view.
- Made binding guards reject mapped column-name permutations without depending on rendered view definitions or `quote_all_identifiers`.
- Hardened benchmark output handling, provenance capture, argument validation, optional database cleanup and independent artifact verification.
- Bound source roles to their primary-key identity and foreign-key topology, closing same-layout role swaps before DML.
- Replaced the obsolete merge-patch installation check, archived retired merge-patch tooling and hardened the portable synthetic pilot.
- Rejected empty or malformed benchmark metadata, fractional sampling values and noncanonical warm-up flags during artifact verification.
- Added dump/restore-safe per-role binding views to reject fully symmetric source-role swaps.
- Made installation verification independent of identifier rendering and required semantic view, trigger and update read-back.
- Required operational pilot reads to return the expected document identity and ETag, and verified that binding views remain inaccessible through PostgREST.
- Corrected NOCHECK reference drift, GENERATED ALWAYS root touch and typed child-key duplicate handling.
- Made generated relation-binding views intrinsically non-updatable even under broad or default write grants.
- Added replacement-time insertion of globally unused explicit child business keys while preserving ownership, feature-flag, identity and atomicity checks.
- Rejected existing orphan child identities with a null-safe ownership check during complete replacement.
- Preserved SQLSTATE and constraint diagnostics for secondary child uniqueness conflicts.
- Added strict JSON type and lossless numeric conversion semantics.
- Added deterministic UTC-normalized `timestamptz` mappings.
- Added generator provenance, inventory, non-mutating health checks and transactional regeneration.
- Moved catalogue guards to statement scope and made mapped-array validation set-wise.
- Added verified current benchmark evidence, custom and plain dump/restore CI, and a release-candidate consistency gate.
- Prevented lifecycle inventory from executing health callbacks owned by another role with the inventory caller's privileges.
- Preserved column ACLs during regeneration and rejected unmanaged view triggers or rules instead of silently dropping them.
- Made UTC `timestamptz` JSON and ETag encoding injective and reversible across BC and AD eras.

This release is intended for evaluation and controlled pilots on PostgreSQL 17, 18 and 19. PostgreSQL 19 coverage uses the current release candidate until the final release is available.
