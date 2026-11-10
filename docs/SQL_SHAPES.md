# Application SQL shapes

These examples run against an existing reviewed mapping. Bind parameters; do
not interpolate untrusted values.

## Point read

```sql
SELECT data FROM api.orders_dv WHERE _id=$1;
```

## Complete replacement assembled in SQL

```sql
UPDATE api.orders_dv
SET data=jsonb_set(data,'{status}',to_jsonb($3::text))
WHERE _id=$1 AND data#>>'{_metadata,etag}'=$2
RETURNING data;
```

`$2` is the ETag retained from the earlier read. A stale predicate returns
`UPDATE 0`; a change after selection and before the post-lock re-read raises
`PT412`. The trigger must receive a complete valid document.

## Delete

```sql
SELECT api.orders_dv__delete_document($1,$2);
```

```sql
DELETE FROM api.orders_dv
WHERE _id=$1 AND data#>>'{_metadata,etag}'=$2
RETURNING _id;
```

Both parameters come from the client read. A stale initial predicate returns
no row; a concurrent post-selection change raises `PT412`. See
[`MERGE.md`](MERGE.md) for the supported native MERGE subset.
