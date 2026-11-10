# 5. Application role

## Goal

Grant only the capabilities required by the application.

## Prerequisites

A provisioning owner able to create a `NOLOGIN` application role, reviewed RLS
policies and an existing mapping. Create the role once as that owner:

```sql
CREATE ROLE app_role NOLOGIN NOSUPERUSER NOBYPASSRLS;
```

The authentication layer assumes or `SET ROLE`s to `app_role`; do not give the
role a password or expose it through an arbitrary-SQL connection.

## Steps

```sql
GRANT USAGE ON SCHEMA api,shop TO app_role;
GRANT SELECT,INSERT,UPDATE,DELETE ON api.orders_dv TO app_role;
GRANT SELECT,INSERT,UPDATE,DELETE ON shop.orders,shop.order_items TO app_role;
GRANT SELECT ON shop.customers TO app_role;
SELECT jsontailor.grant_array_reader('api.orders_dv','app_role');
SELECT jsontailor.grant_delete_rpc('api.orders_dv','app_role');
```

Expected: `GRANT` and two successful helper calls. Without the array capability
the first array read fails 42501. If an ETag-covered object is locked, grant
`UPDATE` on at least one referenced-table column in addition to `SELECT`.
The shipped schema uses identity keys, so it needs no sequence grant. For a
mapping backed by a legacy `serial` default, grant only `USAGE` on that exact
sequence; do not grant `SELECT` or privileges on every sequence in the schema.

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

