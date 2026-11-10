# 4. Create and verify

## Goal

Create a reviewed mapping and verify catalogue health/inventory.

## Prerequisites

Source schema, required index and a safely bound `spec` variable.

## Steps

```sql
SELECT jsontailor.create_duality_view('api.orders_dv', :'spec'::jsonb);
SELECT api.orders_dv__check();
SELECT view_name,generator_version,health_status
FROM jsontailor.duality_view_inventory()
WHERE view_name='api.orders_dv';
```

Expected: a healthy check and one `api.orders_dv` inventory row with matching
generator version and `OK` health. Creation can succeed without the child
index; the check then raises PT422. Add the index and repeat. Binding drift is
a stop condition requiring reviewed regeneration.

## Expected result, common errors and reference

Require `OK` health and one current inventory row. Treat `PT422` as a stop
condition. See [schema change](11-schema-change.md).

