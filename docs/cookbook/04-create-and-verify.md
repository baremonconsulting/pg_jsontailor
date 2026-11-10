# 4. Create and verify

## Goal

Create a reviewed mapping and retain observed catalogue evidence.

## Prerequisites

Complete recipe 03; retain approval and keep schema, generator and specification
unchanged. Bind `spec` from the reviewed file. Run as deployment owner with
stop-on-error.

## Steps

`create_duality_view()` executes immediately, returns void and displays no DDL.
It generates SQL afresh; it does not read or validate the review file.

```sql
BEGIN;
SELECT jsontailor.create_duality_view('api.orders_dv', :'spec'::jsonb);
SELECT api.orders_dv__check();
SELECT view_name,generator_version,health_status
FROM jsontailor.duality_view_inventory()
WHERE view_name='api.orders_dv';
COMMIT;
```

Capture actual deployed state as owner, separately from desired-state SQL:

```sh
umask 077
psql -XqAt -v ON_ERROR_STOP=1 -d generator \
  -f examples/orders_deployed_manifest.sql > deployed.manifest.json
sha256sum deployed.manifest.json > deployed.manifest.sha256
```

The manifest applies to the complete shipped bigint-key orders mapping.
Other mappings require reviewed exact identities, optional roles and signatures.

## Expected result

An `OK` check and inventory row; four views, seven functions and six triggers
for this mapping, definitions/security state, owners, ACLs, comments and metadata.

## Stop, common errors and recovery

Missing objects, unexpected grants/owners or PT422 are stop conditions.
Before COMMIT use ROLLBACK; after COMMIT stop traffic and follow
[removal/rollback](09-remove-and-rollback.md). Never use CASCADE to repair an
unexplained dependency. A missing child index can allow creation but fail health.

## Evidence and reference

Retain manifest, hash, reviewed SQL and approval privately. Catalogue
reconstruction is not original SQL bytes. Regenerating the stored specification
describes desired state, not proof of absence of deployed drift.

