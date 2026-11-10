# 3. Review generated DDL

## Goal

Inspect unpadded generated DDL before deployment.

## Prerequisites

A disposable generator database with matching extension and source schema.

## Steps

Raw `duality_view_ddl` output is for inspection:

```sh
spec="$(tr -d '\n' < examples/orders_dv.spec.json)"
printf '%s\n' "SELECT jsontailor.duality_view_ddl('api.orders_dv', :'spec'::jsonb);" | \
  psql -XAt -v ON_ERROR_STOP=1 -v spec="$spec" -d generator \
  > generated.raw.sql
```

For deployment use `tools/export_standalone.py`: its bundle carries a transaction, major guard, fixed search path and notice. Review view definitions, triggers, ACL blocks, metadata, then the shared trigger body branching on `TG_OP`. If default psql adds padding, use `-XAt`.

## Expected result, common errors and reference

The file is non-empty and matches the reviewed major-specific hash. Padding
means `-XAt` was omitted. See [the DDL outline](generated-ddl-outline.md).

