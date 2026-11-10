# 7. Write with ETags

## Goal

Demonstrate successful and stale-token UPDATE/DELETE from a clean psql session.

## Prerequisites

The retained README quick-start fixture or recipe 1, followed by recipe 5, in a
disposable database, run as the reviewed application role. Recipes 3–4 are the
separate generator path and are not prerequisites for this walkthrough.
Start `psql -X -v ON_ERROR_STOP=1 -d pgjt_cookbook`; no token variables are supplied.
The transaction rolls all demonstration data back.
In that session run `SET ROLE app_role;` and verify
`SELECT current_user;` returns `app_role` before executing the block below.

`\gset` stores a one-row SELECT's named columns as psql variables. For example,
the SELECT below stores `replacement` and `etag`; `:'replacement'` and `:'etag'`
then quote those values as SQL literals. These are psql syntax, not server
parameters; applications bind their own parameters. No external `-v` token is needed.

## Steps

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
INSERT INTO api.orders_dv(data) VALUES
('{"_id":700,"status":"new","created":"2026-11-03","customer":{"customerId":7},"items":[]}'::jsonb);
SELECT jsonb_set(data,'{status}','"updated"'::jsonb) AS replacement,
       data#>>'{_metadata,etag}' AS etag
FROM api.orders_dv WHERE _id=700 \gset
UPDATE api.orders_dv SET data=:'replacement'::jsonb
WHERE _id=700 AND data#>>'{_metadata,etag}'=:'etag';
UPDATE api.orders_dv SET data=:'replacement'::jsonb
WHERE _id=700 AND data#>>'{_metadata,etag}'=:'etag';
SELECT (data->>'status'='updated') AS unchanged_after_stale_update
FROM api.orders_dv WHERE _id=700;
SELECT data#>>'{_metadata,etag}' AS delete_etag
FROM api.orders_dv WHERE _id=700 \gset
UPDATE api.orders_dv SET data=jsonb_set(data,'{status}','"before-delete"'::jsonb)
WHERE _id=700 AND data#>>'{_metadata,etag}'=:'delete_etag';
DELETE FROM api.orders_dv WHERE _id=700
AND data#>>'{_metadata,etag}'=:'delete_etag';
SELECT (data->>'status'='before-delete') AS unchanged_after_stale_delete
FROM api.orders_dv WHERE _id=700;
SELECT data#>>'{_metadata,etag}' AS current_delete_etag
FROM api.orders_dv WHERE _id=700 \gset
DELETE FROM api.orders_dv WHERE _id=700
AND data#>>'{_metadata,etag}'=:'current_delete_etag';
SELECT NOT EXISTS(SELECT FROM api.orders_dv WHERE _id=700) AS deleted;
ROLLBACK;
```

## Expected result

In order: `INSERT 0 1`, `UPDATE 1`, stale `UPDATE 0`, true,
`UPDATE 1`, stale `DELETE 0`, true, current `DELETE 1`, true.
Seeded order 42 remains unchanged.

## Stop, common errors and recovery

An initially stale WHERE token selects no row; a post-selection lock race
raises PT412. A missing replacement-document token or missing token argument to
the explicit delete function raises PT428. Omitting the direct SQL DELETE
predicate does not: it performs an unguarded identity delete and is forbidden
for this client workflow. Roll back on unexpected results.
Retry 40001/40P01 only as bounded whole-transaction retries after a fresh read.
Writes require READ COMMITTED. Never replace a retained client token with a
current-token subquery.

## Evidence and reference

Retain output and candidate hash privately. See [semantics](../SEMANTICS.md).

