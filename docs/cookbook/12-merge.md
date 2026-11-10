# 12. MERGE

## Goal

Rehearse supported native MERGE replacement and guarded deletion.

## Prerequisites

Disposable shipped mapping and reviewed application role; PostgreSQL 17, 18 or
the pinned 19 build. Start a clean psql session with ON_ERROR_STOP.

## Steps

```sql
BEGIN;
CREATE TEMP TABLE document_stage(root_id bigint PRIMARY KEY,doc jsonb NOT NULL,
 CHECK ((doc->'_id'=to_jsonb(root_id)) IS TRUE));
INSERT INTO document_stage
SELECT _id,jsonb_set(data,'{status}','"merge-approved"'::jsonb)
FROM api.orders_dv WHERE _id=42;
MERGE INTO api.orders_dv AS target USING document_stage AS source
ON target._id=source.root_id
WHEN MATCHED THEN UPDATE SET data=source.doc
RETURNING merge_action(),target._id;
MERGE INTO api.orders_dv AS target USING document_stage AS source
ON target._id=source.root_id
WHEN MATCHED AND target.data#>>'{_metadata,etag}'=source.doc#>>'{_metadata,etag}'
THEN DELETE RETURNING merge_action(),target._id;
SELECT data->>'status'='merge-approved' AS stale_delete_preserved
FROM api.orders_dv WHERE _id=42;
TRUNCATE document_stage;
INSERT INTO document_stage SELECT _id,data FROM api.orders_dv WHERE _id=42;
MERGE INTO api.orders_dv AS target USING document_stage AS source
ON target._id=source.root_id
WHEN MATCHED AND target.data#>>'{_metadata,etag}'=source.doc#>>'{_metadata,etag}'
THEN DELETE RETURNING merge_action(),target._id;
SELECT NOT EXISTS(SELECT FROM api.orders_dv WHERE _id=42) AS deleted;
ROLLBACK;
```

## Expected result

UPDATE action, no stale DELETE action, true, current DELETE action, true.
ROLLBACK restores order 42 and its children.

## Stop, common errors and recovery

Match identity only in ON; put DELETE ETag in WHEN MATCHED AND. Duplicate source
keys, missing/stale replacement tokens or constraints fail the whole statement.
ROLLBACK and inspect the error. Add WHEN NOT MATCHED INSERT only when creation
or recreation is explicitly intended; it is not an upsert guarantee.

## Evidence and reference

Retain output and commit/build identifiers. See [MERGE](../MERGE.md) for insert
examples, supported subset and concurrent creation behaviour.

