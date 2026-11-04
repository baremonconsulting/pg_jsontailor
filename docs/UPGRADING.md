# Upgrading generated views

Generated views contain copied SQL and PL/pgSQL. Upgrading the `pg_jsontailor` extension changes the generator, but does not rewrite objects that were generated earlier. Regenerate each mapping when release notes identify a generated write-path change or after a supported source-schema change.

Keep every JSON mapping in source control. Before regeneration, record view and function grants and confirm that no application writer is active. Recreate one mapping in a transaction so that failure restores the previous objects:

```sql
BEGIN;

DROP VIEW api.orders_dv CASCADE;

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

COMMIT;
```

Pass `orders_dv_spec` as a `psql` variable or application parameter; do not interpolate untrusted text into the script. The runtime role also needs the documented source-table privileges and `EXECUTE` on the generated delete function.

After committing, read a representative document, perform an ETag-checked replacement in a disposable transaction, call the delete function against disposable data, and verify row-level security with the deployment role. A relation rename or replacement is detected by generated write paths and returns `PT422` until regeneration. PostgreSQL dump and restore preserve the dependency-based binding check because it is resolved from the restored view dependencies rather than stored numeric OIDs.

If validation fails before `COMMIT`, issue `ROLLBACK`. Regeneration replaces database objects and therefore requires a planned write window; it is not an online schema migration mechanism.
