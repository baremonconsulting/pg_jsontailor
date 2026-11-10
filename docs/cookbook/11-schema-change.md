# 11. Schema change and regeneration

## Goal

Rehearse a supported name-preserving migration atomically.

## Prerequisites

Disposable database after recipes 01–05, owner session, no active writers,
reviewed current spec/SQL, deployed manifest, grants and tested backup/restore.
Use `psql -X -v ON_ERROR_STOP=1`. This example rolls back; it is not approval
to apply production DDL.

## Steps

Save the pre-change manifest using recipe 04. Review the regenerated SQL with
recipe 03 before the write-free window. The existing specification remains
correct when the primary-key constraint alone is renamed:

```sql
BEGIN;
SET LOCAL lock_timeout='1s';
ALTER TABLE shop.orders RENAME CONSTRAINT orders_pkey TO orders_pkey_rehearsal;
SELECT jsontailor.regenerate_duality_view('api.orders_dv');
SELECT api.orders_dv__check()->>'status' AS health;
SELECT data->>'status' FROM api.orders_dv WHERE _id=42;
ROLLBACK;
SELECT api.orders_dv__check()->>'status' AS restored_health;
SELECT conname='orders_pkey' AS original_constraint
FROM pg_constraint WHERE conrelid='shop.orders'::regclass AND contype='p';
```

For a real deployment, test actual application-role CRUD on disposable rows
inside a savepoint, roll that data back, capture post-change catalogue evidence
and COMMIT only after approval. Reapply only reviewed capabilities if recreation
is required. Mapped-name/specification changes use the explicit recreation
procedure in UPGRADING, not this stored-spec regenerator.

## Expected result

Both health values are OK, the representative read succeeds and
original_constraint is true. Source data and grants remain unchanged.

## Stop, common errors and recovery

Dependency/ACL rejection, lock timeout, PT422 or unexpected role behaviour:
ROLLBACK and stop. A committed relational migration is not undone by reapplying
an old generated bundle; use its separately tested inverse migration or restore.

## Evidence and reference

Retain before/after manifests, schema/spec hashes, app-role checks and the
backup/restore rehearsal. See [upgrading](../UPGRADING.md).

