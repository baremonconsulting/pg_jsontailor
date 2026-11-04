# Document semantics

## Shape

Each row in a generated view has an `_id` column and a `jsonb` `data` column. The root primary key also appears inside the document as `_id`; optimistic concurrency information appears under `_metadata.etag`. Configured root fields, an optional referenced object and an optional child array complete the document.

Array order is deterministic and uses the configured `order_by` column followed by the child primary key where needed.

## ETag

The ETag is a SHA-256 digest of the configured relational state in a deterministic representation. It includes root fields, referenced-object fields unless disabled with `object.etag`, and child items in deterministic order.

A replacement or delete must carry the ETag obtained from a preceding read. The operation locks and rechecks the current document before changing data. Missing and stale ETags fail atomically.

## Insert

An insert validates the complete document, creates the root row and then creates configured child rows. Database defaults and supported generated keys remain available when the corresponding document fields are omitted. The returned document contains generated identifiers and its current ETag.

## Replacement

An update is a complete document replacement, not a partial patch. The root identity cannot change. Child rows are reconciled by key using set-based insert, update and delete operations. Operation flags in the specification determine which changes are allowed.

## Delete

Call the generated `<view>__delete_document(expected_id, expected_etag)` function. It locks the configured document state, reads it back after acquiring the lock, verifies the caller's ETag and then removes or unlinks children according to the schema and specification before deleting the root row. A missing ETag returns `PT428`; a stale ETag returns `PT412` and changes nothing.

Direct `DELETE` on the generated view returns `PT428`. Use the function from SQL or expose it as a PostgREST RPC so that the expected ETag is explicit.

## Security and RLS

The extension functions and generated objects use invoker rights. Generated SQL uses qualified, quoted identifiers and a controlled search path. Row-level security remains effective on source tables. A write fails if policies hide rows required to validate, lock or reconcile the complete document.

## REST

PostgREST can expose a schema containing generated views. Keep source tables in a separate, unexposed schema. Use the generated delete function through `/rpc/<view>__delete_document` with `expected_id` and `expected_etag`. PostgreSQL errors map to the REST responses used by the deployment; authentication, role switching and TLS are outside the extension.

## MERGE

Native SQL `MERGE` uses the same insert and update semantics as direct writes to the generated view. A matched update must supply the complete document and the client's ETag. A not-matched insert expresses explicit permission to create or recreate that identity. Source keys must be unique. Delete actions are outside the supported subset; use the generated ETag-aware delete function. The complete contract is in [`MERGE.md`](MERGE.md).

## Known limits

- one root table, one optional referenced object and one optional child array;
- single-column primary and foreign keys;
- complete replacement rather than partial patching;
- no recursive nesting;
- document-wide ETags require linear work over child collections;
- deployment-specific throughput and latency require separate measurement.
