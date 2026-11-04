# Native SQL MERGE

PostgreSQL `MERGE` can route document inserts, complete replacements and ETag-checked deletes through a generated `pg_jsontailor` view. PostgreSQL selects the existing action-specific `INSTEAD OF` trigger; the extension does not implement a separate MERGE engine.

## Supported subset

The tested subset on PostgreSQL 17, 18 and 19 is:

- `WHEN MATCHED THEN UPDATE SET data = source.doc`;
- `WHEN NOT MATCHED THEN INSERT(data) VALUES(source.doc)`;
- conditional `WHEN` clauses and `DO NOTHING`;
- `WHEN MATCHED AND` client-ETag checks followed by `DELETE`;
- `RETURNING merge_action(), target._id, target.data`.

Match only on the typed root identity. Do not include the ETag in the `ON` expression: a stale token must reach the update trigger and return `PT412`, rather than changing the action into an insert.

The source must contain at most one row for each root key. A staging primary key provides that guarantee and a check constraint can verify agreement between the typed source key and `doc._id`:

```sql
CREATE TEMP TABLE document_stage (
    root_id bigint PRIMARY KEY,
    doc jsonb NOT NULL,
    CHECK (doc->'_id' = to_jsonb(root_id))
);
```

## Example

Keep the complete document returned by an earlier read, including `_metadata.etag`, and place the intended replacement in the staging table:

```sql
INSERT INTO document_stage(root_id, doc)
SELECT _id, jsonb_set(data, '{status}', '"approved"'::jsonb)
FROM api.orders_dv
WHERE _id = 1001;

MERGE INTO api.orders_dv AS target
USING document_stage AS source
ON target._id = source.root_id
WHEN MATCHED THEN
    UPDATE SET data = source.doc
WHEN NOT MATCHED THEN
    INSERT(data) VALUES(source.doc)
RETURNING merge_action(), target._id, target.data;
```

The returned document is the stored result. It therefore contains database defaults and generated root or child identifiers; it is not simply a copy of the source payload. Result order is unspecified. `DO NOTHING` returns no changed row and does not validate an ETag in the skipped document.

## Concurrency and create intent

An update is a complete replacement. Missing ETags return `PT428`, stale ETags return `PT412`, and incomplete documents return `PT422`. One failing action rolls back all persistent row changes made by the statement; PostgreSQL sequences are not required to rewind.

`WHEN NOT MATCHED` explicitly permits creation. If a target was deleted after a client read, the old payload can therefore recreate it through the insert branch. Use the insert branch only when recreation is intended. For strict update-only behaviour, omit that branch.

Two concurrent inserts for the same absent key retain native PostgreSQL MERGE behaviour: one statement may fail with `23505`. Retry only after reading the current target and deciding whether the operation is now an update. The extension does not promise `INSERT ... ON CONFLICT DO UPDATE` semantics.

## ETag-checked delete

Keep identity matching in `ON` and put the client token in the matched-action condition:

```sql
MERGE INTO api.orders_dv AS target
USING document_stage AS source
ON target._id = source.root_id
WHEN MATCHED
     AND target.data #>> '{_metadata,etag}' = source.doc #>> '{_metadata,etag}'
THEN DELETE
RETURNING merge_action(), target._id;
```

The delete trigger locks the state included in the ETag, reads the document again and compares it with `OLD.data`. A concurrent change after action selection returns `PT412`. A token that does not match during initial action selection produces no delete action and no error.

The supported subset does not include actions that mutate targets absent from the source. The generated `<view>__delete_document(expected_id, expected_etag)` function remains available for an explicit-argument SQL or PostgREST RPC contract.

All mapping flags, input validation, invoker privileges, row-level security policies and relation-binding checks remain applicable because MERGE reuses the generated insert, update and delete paths.
