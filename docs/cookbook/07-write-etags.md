# 7. Write with ETags

## Goal

Insert, read a client token, replace and delete with optimistic concurrency.

## Prerequisites

A healthy mapping and application role with reviewed write/delete capabilities.

## Steps

Insert a complete document:

```sql
INSERT INTO api.orders_dv(data) VALUES
(('{"_id":700,"status":"new","created":"2026-11-03",' ||
  '"customer":{"customerId":7},"items":[]}')::jsonb);
```

Expected: `INSERT 0 1`. Read and retain both the complete replacement document
and `_metadata.etag`:

```sql
SELECT data,data#>>'{_metadata,etag}' AS etag
FROM api.orders_dv WHERE _id IN (42,700) ORDER BY _id;
```

Expected: two rows with 32-character ETags. Retain the complete document and
token for 42 as `replacement` and `etag`, and the token for 700 as
`delete_etag`. Submit the complete document with its token unchanged:

```sql
UPDATE api.orders_dv SET data=:'replacement'::jsonb WHERE _id=42 AND data#>>'{_metadata,etag}'=:'etag';
```

Expected: `UPDATE 1`. A stale predicate yields `UPDATE 0`; change after locking raises PT412; missing token raises PT428. Retry whole transactions after 40001/40P01. Writes require READ COMMITTED (otherwise 0A000).

Delete with the current token:

```sql
DELETE FROM api.orders_dv WHERE _id=700
AND data#>>'{_metadata,etag}'=:'delete_etag';
```

Expected: `DELETE 1` and no remaining root or child row for document 700. A
stale client `delete_etag` returns `DELETE 0` and changes nothing; do not replace
the retained token with a subquery that reads the current ETag during DELETE.

## Expected result, common errors and reference

Expect `INSERT 0 1`, two token reads, `UPDATE 1`, stale `DELETE 0` and current
`DELETE 1`. `PT412` requires a fresh read. See [semantics](../SEMANTICS.md).

