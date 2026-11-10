# 9. Remove and roll back

## Goal

Remove one generated mapping without touching source data, then restore it.

## Prerequisites

Stopped application traffic, exact retained object identities, verified bundle
and recorded grants.

## Steps

Inventory exact identities first. For the shipped full `bigint` mapping, run
the following as the object owner after stopping application traffic:

```sql
BEGIN;
DROP VIEW api.orders_dv;
DROP FUNCTION api.orders_dv__insert();
DROP FUNCTION api.orders_dv__update();
DROP FUNCTION api.orders_dv__delete();
DROP FUNCTION api.orders_dv__guard();
DROP FUNCTION api.orders_dv__check();
DROP FUNCTION api.orders_dv__delete_document(bigint,text);
DROP FUNCTION api.orders_dv__bounded_array(bigint);
DROP VIEW api.orders_dv__array_binding;
DROP VIEW api.orders_dv__object_binding;
DROP VIEW api.orders_dv__root_binding;
COMMIT;
```

Expected: all statements succeed and the `shop` source tables remain. A
root-only/object/array mapping omits binding views and helpers it did not
generate; derive the exact list from the retained bundle. Do not replace this
list with a name wildcard or `CASCADE`. Roll back by applying the retained
verified bundle, restoring recorded grants and repeating health and smoke
tests.

## Expected result, common errors and reference

All generated objects disappear, source tables remain, and verified reapply
restores health and app access. Never use wildcard or `CASCADE`. See
[upgrading](../UPGRADING.md).

