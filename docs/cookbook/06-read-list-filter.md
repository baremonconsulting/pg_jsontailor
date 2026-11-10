# 6. Read, list and filter

## Goal

Use relational identity predicates and bounded keyset pages.

## Prerequisites

A healthy mapping and application role with reviewed read capabilities.

## Steps

```sql
SELECT data FROM api.orders_dv WHERE _id=42;
SELECT data FROM api.orders_dv WHERE _id>42 ORDER BY _id LIMIT 50;
SELECT document._id,document.data
FROM (
 SELECT order_id FROM shop.orders
 WHERE status='approved' AND order_id>42
 ORDER BY order_id LIMIT 20
) AS selected
JOIN api.orders_dv AS document ON document._id=selected.order_id
ORDER BY document._id;
```

Expected: one point result, at most 50 ordered page results and at most 20
root-prefilter results (zero with the untouched fixture). Use the same invoker
role and RLS context on both relations. Review EXPLAIN: a subquery alone does
not guarantee execution order or bounded document-construction work.
Use mapped relational keys, bounded keyset pages and PostgREST `db-max-rows`.

## Expected result, common errors and reference

Expect one point row and no more than 50 page rows. Avoid unbounded document
scans. See [performance qualification](../PERFORMANCE_QUALIFICATION.md).

