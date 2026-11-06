# Upgrading generated views

The 0.2.0 generated write functions remove per-document subtransactions from
ordinary bulk writes. Regenerate existing duality views after installing this
version; upgrading the extension alone does not replace SQL already copied
into generated objects.

Generated views contain copied SQL and PL/pgSQL. Upgrading the `pg_jsontailor` extension changes the generator, but does not rewrite objects that were generated earlier. Regenerate each mapping when release notes identify a generated write-path change or after a supported source-schema change.

Any view generated from an untagged 0.2.0 development snapshot, including an
earlier public `main` commit, must be regenerated from the eventual tagged
release before it is treated as a pilot deployment.

## Source-schema change matrix

| Change | Effect | Required action |
|---|---|---|
| Add or drop an unmapped column | Binding remains valid | No regeneration; run the health check |
| Rename, replace or move a source table | Stored specification no longer identifies the same relation | Update the specification in source control and explicitly recreate the mapping |
| Rename, reuse or permute a mapped column | Writes return `PT422` | Review the mapping and regenerate |
| Change a mapped column type | PostgreSQL normally blocks the change while binding views depend on it | Regenerate through a reviewed migration using the updated specification |
| Rename a bound primary-key constraint | Writes return `PT422` | Regenerate |
| Change or invalidate a required foreign key | Writes return `PT422`; unsupported actions are rejected | Restore a validated, enforced, non-deferrable supported FK and regenerate |
| Add classic `INHERITS` descendants | Writes return `PT422` | Remove the unsupported inheritance or redesign the mapping |
| Attach or detach declarative partitions | Supported when the mapped root contract remains valid | Run health and functional checks |
| Dump and restore | Generated bindings are restored by catalogue dependency | Run inventory, health, privilege and smoke checks |
| Logical replication | DDL and generated objects are not assumed to replicate | Install the extension and generate or regenerate mappings separately on the subscriber |

Never use regeneration to guess a new table or column name. Update the
source-controlled specification explicitly when names or mappings change.

Keep every JSON mapping in source control. Before regeneration, inventory objects that depend on the generated view and confirm that no application writer is active. Inspect all managed views and their catalogue health first:

```sql
SELECT * FROM jsontailor.duality_view_inventory();
SELECT api.orders_dv__check();
```

Run inventory as the owner of the managed views when health execution is
required. Inventory reports views owned by other roles as `SKIPPED` and does not
execute their health functions. Connect as each view owner, or invoke that
owner's explicitly qualified `__check()` function under a separately reviewed
operational procedure.

For a view generated with provenance metadata, use the transactional
regenerator. It preserves direct owner-issued view grants, generated-function
grants and generated-function comments, rejects dependent relations and unmanaged
triggers or rules, and never uses `CASCADE`. Direct owner-issued relation and
column grants, including grant options, are preserved. Delegated grant chains
block regeneration before any object is replaced and must be reapplied through
the reviewed deployment procedure. Application triggers and rules must be removed and
explicitly recreated as reviewed deployment objects:

```sql
BEGIN;
SELECT jsontailor.regenerate_duality_view('api.orders_dv');
-- Run deployment-specific read, privilege and RLS acceptance checks here.
COMMIT;
```

If a consumer depends on the view, PostgreSQL stops the operation instead of silently destroying that consumer. Failure rolls back all drops and creations. The function also accepts the legacy `pg_jsontailor duality view spec v1` comment written by earlier 0.2.0 development builds and replaces it with current provenance metadata.

For a view whose metadata comment was removed or replaced, recover the mapping from source control and use the explicit procedure below. Record grants before starting. Recreate one mapping in a transaction so that failure restores the previous objects. Drop only the binding views for roles present in that reviewed mapping; the example includes all three roles:

```sql
BEGIN;

DROP VIEW api.orders_dv;
DROP VIEW IF EXISTS api.orders_dv__root_binding;
DROP VIEW IF EXISTS api.orders_dv__object_binding;
DROP VIEW IF EXISTS api.orders_dv__array_binding;

DO $cleanup$
DECLARE
    item regprocedure;
BEGIN
    FOR item IN
        SELECT p.oid::regprocedure
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'api'
          AND p.proname = ANY (ARRAY[
              'orders_dv__insert',
              'orders_dv__update',
              'orders_dv__delete',
              'orders_dv__guard',
              'orders_dv__delete_document',
              'orders_dv__check'
          ])
    LOOP
        EXECUTE format('DROP FUNCTION %s', item);
    END LOOP;
END
$cleanup$;

SELECT jsontailor.create_duality_view(
    'api.orders_dv',
    :'orders_dv_spec'::jsonb
);

-- Reapply the deployment-specific grants recorded before regeneration.
GRANT SELECT, INSERT, UPDATE, DELETE ON api.orders_dv TO application_role;

-- Run deployment-specific catalogue, read, privilege and RLS acceptance checks here.
-- Exercise writes only against disposable test rows created inside this transaction.

COMMIT;
```

Pass `orders_dv_spec` as a `psql` variable or application parameter; do not interpolate untrusted text into the script. The runtime role also needs the documented source-table privileges and `EXECUTE` on the generated delete function. If dependent objects must be replaced, inventory their definitions, owners and grants first, then include their explicit drop and recreation in the same reviewed transaction. Do not substitute an unqualified `CASCADE` operation.

Before committing, verify generated objects, grants, a representative read and row-level security with the deployment role. Perform an ETag-checked replacement and delete only against disposable rows created within the transaction, enclosing the write checks in a savepoint and using `ROLLBACK TO SAVEPOINT` before the final commit. After committing, run separate non-mutating smoke tests against the deployed view. A relation role change, rename, replacement, primary-key constraint rename or mapped-column identity change is detected by generated write paths and returns `PT422` until regeneration. PostgreSQL dump and restore preserve the binding check because it is resolved from restored per-role binding-view dependencies, constraint names and catalogue structure rather than stored numeric OIDs. Binding views for roles present in the reviewed mapping are generated implementation objects and must be dropped and recreated with the main view as shown above. Objects at optional binding names for absent roles are outside that mapping and must not be removed.

Generated write statements acquire `ACCESS SHARE` locks on configured root,
object and array source relations in that fixed order before validating their
catalogue bindings. Source DDL requiring `ACCESS EXCLUSIVE`, including relation
renames and replacements, therefore waits for active writers. Schedule source
schema changes in a write-free maintenance window;
set a deployment-appropriate `lock_timeout` so an accidental overlap fails
instead of waiting indefinitely.

If validation fails before `COMMIT`, issue `ROLLBACK`. Regeneration replaces database objects and therefore requires a planned write window; it is not an online schema migration mechanism.
