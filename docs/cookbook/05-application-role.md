# 5. Application role

## Goal

Grant only the capabilities required by the application.

## Prerequisites

An application role, reviewed RLS policies and an existing mapping.

## Steps

```sql
GRANT USAGE ON SCHEMA api,shop TO app_role;
GRANT SELECT,INSERT,UPDATE,DELETE ON api.orders_dv TO app_role;
GRANT SELECT,INSERT,UPDATE,DELETE ON shop.orders,shop.order_items TO app_role;
GRANT SELECT ON shop.customers TO app_role;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA shop TO app_role;
SELECT jsontailor.grant_array_reader('api.orders_dv','app_role');
SELECT jsontailor.grant_delete_rpc('api.orders_dv','app_role');
```

Expected: `GRANT` and two successful helper calls. Without the array capability
the first array read fails 42501. If an ETag-covered object is locked, grant
`UPDATE` on at least one referenced-table column in addition to `SELECT`.

Standalone targets lack grant helpers; the owner instead grants the exact
signatures:

```sql
GRANT EXECUTE ON FUNCTION api.orders_dv__bounded_array(bigint) TO app_role;
GRANT EXECUTE ON FUNCTION api.orders_dv__delete_document(bigint,text) TO app_role;
```

The source privileges are technically required by invoker execution. Do not
expose this role through arbitrary SQL, because direct source DML bypasses
document ETags. See [security](../SECURITY_MODEL.md).

## Expected result, common errors and reference

The role performs only intended operations; `42501` identifies a missing
capability. Never grant all functions. See [helper ACL policy](../HELPER_ACL.md).

