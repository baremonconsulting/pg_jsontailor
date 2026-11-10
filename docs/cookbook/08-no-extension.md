# 8. Deploy without the extension

## Goal

Deploy a verified standalone bundle to a target without the extension.

## Prerequisites

Matching PostgreSQL majors, a disposable generator, a `NOSUPERUSER` target
owner and retained source schema/grant/rollback records.

## Steps

Use this procedure when the target database must not install the extension. The
generator and target must use the same PostgreSQL major. The target owner must
be an ordinary `NOSUPERUSER` role.

## 1. Prepare the generator

```sh
pg_dump --schema-only --no-owner --no-privileges --schema=shop \
  -d target > source-schema.sql
createdb generator
psql -X -v ON_ERROR_STOP=1 -d generator -c 'CREATE EXTENSION pg_jsontailor'
psql -X -v ON_ERROR_STOP=1 -d generator -f source-schema.sql
psql -X -v ON_ERROR_STOP=1 -d generator -c 'CREATE SCHEMA api'
```

Expected: every command exits zero. Repeat `--schema` for every mapped source
schema. Inspect the dump before loading it; it must contain definitions that
exactly match the target and no customer rows.

## 2. Export and verify

```sh
python3 tools/export_standalone.py \
  --psql "$(command -v psql)" --database generator \
  --view api.orders_dv --spec examples/orders_dv.spec.json \
  --output standalone-orders
python3 tools/verify_generated_sql_notice.py --bundle standalone-orders
sha256sum standalone-orders/generated.sql
```

Expected: verification succeeds and the recorded bundle contains
`generated.sql` and `GENERATED_SQL_NOTICE`. Review the SQL and
record its SHA-256, generator version, PostgreSQL major and intended owner.

## 3. Apply as the target owner

The bundle starts its own transaction. Do not paste only its body and do not
remove its version guard or search-path pin.

```sh
psql -X -v ON_ERROR_STOP=1 -d target \
  -c 'SET ROLE target_owner; CREATE SCHEMA api'
psql -X -v ON_ERROR_STOP=1 -d target \
  -c 'SET ROLE target_owner' \
  -f standalone-orders/generated.sql
```

Expected: both commands exit zero. The target owner owns `api` and every
generated object. A wrong PostgreSQL major fails before a
generated object is created; any later error rolls the whole bundle back.

## 4. Grant only reviewed capabilities

Run these as the target owner, substituting the real role and source objects:

```sql
GRANT USAGE ON SCHEMA api, shop TO application_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON api.orders_dv TO application_role;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON shop.orders, shop.order_items TO application_role;
GRANT SELECT ON shop.customers TO application_role;
GRANT USAGE, SELECT ON SEQUENCE
  shop.orders_order_id_seq, shop.order_items_item_id_seq TO application_role;
GRANT EXECUTE ON FUNCTION api.orders_dv__bounded_array(bigint)
  TO application_role;
GRANT EXECUTE ON FUNCTION api.orders_dv__delete_document(bigint,text)
  TO application_role;
```

Do not grant all functions in `api`. Adjust source privileges and RLS policies
to the operations the application actually performs.

## 5. Prove the deployment

```sql
SELECT api.orders_dv__check()->>'status'; -- expected: OK
SELECT data FROM api.orders_dv ORDER BY _id LIMIT 1;
```

Then execute one insert, one complete-document update with the current ETag,
one stale-ETag rejection and one delete through the actual application role.
Expected: successful writes change relational rows atomically; the stale write
returns `PT412` and changes nothing.

## 6. Move to a new product version

Stop application traffic. On a fresh same-major generator, install the new
product version, load a new schema-only dump, export/verify/review a new bundle
and retain the previous bundle and grants as rollback material. Put the exact
object drops from [recipe 9](09-remove-and-rollback.md) in
`reviewed-remove-orders.sql` and the reviewed step-4 grants in
`reviewed-grants.sql`, then run:

```sh
psql -X -v ON_ERROR_STOP=1 -d target \
  -c 'SET ROLE target_owner' -f reviewed-remove-orders.sql
psql -X -v ON_ERROR_STOP=1 -d target \
  -c 'SET ROLE target_owner' -f standalone-orders-vnext/generated.sql
psql -X -v ON_ERROR_STOP=1 -d target \
  -c 'SET ROLE target_owner' -f reviewed-grants.sql
```

Run owner health and application-role read/insert/replacement/stale/delete
smoke tests again. If any step fails, stop traffic, remove partial generated
objects with the same reviewed exact identities, reapply the retained previous
bundle, restore its recorded grants and repeat health/application smoke. Never
use `CASCADE` or continue with mixed-version generated objects.

The standalone target has no extension inventory, regeneration or reviewed-
grant administration helpers. Keep the reviewed bundle, hash, grants, health
result and rollback script in the deployment record. Generate and review a new
bundle after a PostgreSQL-major upgrade, product-version change or mapping
change. No provider is
qualified by this generic procedure.

## Expected result, common errors and reference

Require target health, app-role CRUD, removal/reapply and restored grants. A
major mismatch or missing target schema fails closed. See the
[standalone example](../../examples/standalone/README.md).

