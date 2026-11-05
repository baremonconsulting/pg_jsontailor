# Upgrading generated views

Generated views contain copied SQL and PL/pgSQL. Upgrading the `pg_jsontailor` extension changes the generator, but does not rewrite objects that were generated earlier. Regenerate each mapping when release notes identify a generated write-path change or after a supported source-schema change.

Keep every JSON mapping in source control. Before regeneration, record view and function grants, inventory objects that depend on the generated view and confirm that no application writer is active. The default procedure deliberately uses `DROP VIEW` without `CASCADE`: if a consumer depends on the view, PostgreSQL stops the operation instead of silently destroying that consumer. Recreate one mapping in a transaction so that failure restores the previous objects:

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
              'orders_dv__delete_document'
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

Before committing, verify generated objects, grants, a representative read and row-level security with the deployment role. Perform an ETag-checked replacement and delete only against disposable rows created within the transaction, enclosing the write checks in a savepoint and using `ROLLBACK TO SAVEPOINT` before the final commit. After committing, run separate non-mutating smoke tests against the deployed view. A relation role change, rename, replacement, primary-key constraint rename or mapped-column identity change is detected by generated write paths and returns `PT422` until regeneration. PostgreSQL dump and restore preserve the binding check because it is resolved from restored per-role binding-view dependencies, constraint names and catalogue structure rather than stored numeric OIDs. The binding views are generated implementation objects and must be dropped and recreated with the main view as shown above.

If validation fails before `COMMIT`, issue `ROLLBACK`. Regeneration replaces database objects and therefore requires a planned write window; it is not an online schema migration mechanism.
