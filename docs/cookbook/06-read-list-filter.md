# 6. Read, list and filter

## Goal

Use relational identity predicates and bounded keyset pages.

## Prerequisites

A healthy mapping and application role with reviewed read capabilities.

## Steps

```sql
SELECT data FROM api.orders_dv WHERE _id=42;
SELECT data FROM api.orders_dv WHERE _id>42 ORDER BY _id LIMIT 50;
```

Expected: one point result and at most 50 ordered page results. Use mapped relational keys, bounded keyset pages and PostgREST `db-max-rows`.

## Expected result, common errors and reference

Expect one point row and no more than 50 page rows. Avoid unbounded document
scans. See [performance qualification](../PERFORMANCE_QUALIFICATION.md).

