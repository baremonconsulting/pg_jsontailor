# 2. Map your own tables

## Goal

Translate a small schema into a specification and verify its document shape.

## Prerequisites

Use the extension-enabled disposable `pgjt_cookbook` database from recipe 1.
The independent `recipe_mapping` schema below must not exist. Run as owner in
`psql -X -v ON_ERROR_STOP=1 -d pgjt_cookbook`; this rehearsal rolls back.

Check fit before writing a real specification:

- one root, at most one referenced object and one child array;
- schema-qualified tables and view, view name at most 46 UTF-8 bytes;
- single-column primary/foreign keys and supported scalar mapped types;
- no mapped domains, enums, JSON types, SQL arrays, generated columns or classic inheritance;
- validated, enforced, non-deferrable foreign keys, with child `NO ACTION` or `RESTRICT`;
- usable defaults for unmapped `NOT NULL` columns;
- nullable child `parent_fk` when `array.delete=false` must unlink children;
- explicit operation flags, object ETag choice, ordering and agreed resource limits;
- child index beginning `(parent_fk, order_by, key)`, duplicate columns removed.

See the complete [fit assessment](../PILOT_READINESS.md#1-fit-assessment) and
[supported-type matrix](../API.md#supported-mapped-column-types).

## Steps

```sql
BEGIN;
CREATE SCHEMA recipe_mapping;
CREATE TABLE recipe_mapping.jobs(id bigint PRIMARY KEY,title text NOT NULL);
CREATE TABLE recipe_mapping.tasks(id bigint PRIMARY KEY,
 job_id bigint REFERENCES recipe_mapping.jobs(id) NOT DEFERRABLE,label text NOT NULL);
CREATE INDEX tasks_document_order ON recipe_mapping.tasks(job_id,id);
INSERT INTO recipe_mapping.jobs VALUES(1,'Launch');
INSERT INTO recipe_mapping.tasks VALUES(10,1,'Review');
-- Executes immediately, returns void and displays no DDL. This disposable
-- rehearsal is not deployment approval; review exact SQL first for pilots.
SELECT jsontailor.create_duality_view('recipe_mapping.documents',
'{"root":{"table":"recipe_mapping.jobs","key":"id","fields":{"title":"title"}},
 "array":{"name":"tasks","table":"recipe_mapping.tasks","key":"id","parent_fk":"job_id",
 "order_by":"id","fields":{"id":"id","label":"label"},"delete":false}}'::jsonb);
SELECT data-'_metadata' AS document FROM recipe_mapping.documents WHERE _id=1;
SELECT recipe_mapping.documents__check()->>'status' AS health;
ROLLBACK;
```

## Expected result

The document, ignoring JSON key order, is
`{"_id":1,"title":"Launch","tasks":[{"id":10,"label":"Review"}]}`;
health is `OK`. The rollback removes this separate example; it does not change
the shipped orders mapping. Flags omitted from the specification default to false.

## Stop, common errors and recovery

All three specification errors use `22023`:

| Exact message (example field `x`) | Fix |
|---|---|
| `root.table must have two parts` | Use `schema.table`, not a bare table name |
| `invalid or unsupported root.fields.x` | Map an existing writable supported scalar column |
| `root.key is not a single-column primary key` | Map the actual single-column primary key |

Missing child index permits creation but makes health raise `PT422`.
ROLLBACK on any unexpected result; do not use CASCADE to repair dependencies.

## Evidence and reference

Retain the reviewed schema/specification and output privately. See
[DDL review](03-review-ddl.md) and [troubleshooting](troubleshooting.md).

