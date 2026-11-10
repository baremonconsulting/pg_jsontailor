# 2. Map your own tables

## Goal

Prepare supported source relations and their child-access index.

## Prerequisites

Reviewed schema-qualified tables, primary keys and foreign keys.

## Steps

Use schema-qualified names, a view name of at most 46 bytes and supported scalar types from [API](../API.md). For an array add an index beginning with `(parent_fk, order_by, key)`:

```sql
CREATE INDEX order_items_document_order ON shop.order_items(order_id,item_id);
```

Expected: `CREATE INDEX`. The general prefix is
`(parent_fk, order_by, key)` with duplicate columns removed; in the shipped
mapping `order_by` and `key` are both `item_id`. A missing index permits
creation but makes `__check()` return PT422. Use one root, at most one
referenced object and one child array; exclude high-contention write-heavy
workloads.

## Expected result, common errors and reference

Expect `CREATE INDEX`. Invalid mappings fail `22023`; missing index health
fails `PT422`. See [troubleshooting](troubleshooting.md).

