# Document semantics

## Shape

Each row in a generated view has an `_id` column and a `jsonb` `data` column. The root primary key also appears inside the document as `_id`; optimistic concurrency information appears under `_metadata.etag`. Configured root fields, an optional referenced object and an optional child array complete the document.

Array order is deterministic and uses the configured `order_by` column followed by the child primary key where needed.

## ETag

The ETag is the first 128 bits of SHA-256 encoded as 32 uppercase hexadecimal
characters. It is a change detector, not an authentication token. It hashes a
deterministic representation of the root identity and mapped root fields,
referenced-object fields and child items in deterministic order.
With `object.etag=false`, non-key object fields are excluded, but the mapped
object key (or JSON null for a missing visible object) remains covered. Changing
the reference key therefore changes the ETag even when object field checking
is disabled. Unmapped source columns do not participate.

A replacement and the explicit delete function require the ETag obtained from
a preceding read. Direct DELETE obtains `OLD.data` from the selected row, not
an independently supplied client token; its predicate contract is described
below. The write path locks and rechecks the visible document before changing
data. A supplied missing or stale ETag fails atomically.

## Insert

An insert validates the complete document, creates the root row and then creates configured child rows. Database defaults and supported generated keys remain available when the corresponding document fields are omitted. The returned document contains generated identifiers and its current ETag.

Mapped JSON values must use the JSON type expected by the target PostgreSQL
column: strings for character, date, timestamp, timestamp-with-time-zone and UUID columns; numbers for
integer and finite numeric columns; and booleans for boolean columns. PostgreSQL
non-finite `numeric` values use exactly the canonical JSON strings `NaN`,
`Infinity` and `-Infinity`, matching their document read representation. Other
numeric strings and these tokens for integer columns are rejected. JSON `null`
continues to be governed by the target column's nullability. Numeric conversion
must be lossless at the declared target precision and scale. A type mismatch,
lossy numeric conversion, explicit value for a `GENERATED ALWAYS` identity, or
an array element that cannot be routed to a partition is rejected with
`PT422`. Other relational constraints retain their native PostgreSQL error.

Timestamp-with-time-zone values are rendered in UTC with a `Z` suffix and six
fractional-second digits. Finite BC values additionally carry the ` BC` era
suffix. Their document representation and ETag therefore do not change with the
session `TimeZone`, and BC and AD values cannot collapse to the same document.

## Replacement

An update is a complete document replacement, not a partial patch. `NEW.data` must be a complete valid document, but the client may pass only the identity, an earlier ETag and changed values while PostgreSQL constructs that document from the current view row in the statement. The root identity cannot change. Child rows are reconciled by key using set-based insert, update and delete operations. Operation flags in the specification determine which changes are allowed.

Replacement applies the same JSON-type and lossless-conversion rules as insert.

For a non-`GENERATED ALWAYS` child key, a supplied key identifies an existing element when it belongs to the document and creates a new element when it is globally unused and array insertion is enabled. A key owned by another document or an existing orphan key whose parent is `NULL` is rejected with `PT422`. Explicit new values for `GENERATED ALWAYS` child keys are rejected; omit those keys and let PostgreSQL generate them.

A single-column child primary key may be `DEFERRABLE INITIALLY IMMEDIATE`.
Explicit-key inserts use a compatibility fallback for that mapping;
non-deferrable keys retain the set-based `ON CONFLICT` path. The extension does
not issue `SET CONSTRAINTS`. If an
application explicitly defers the primary key, PostgreSQL may therefore report
a native uniqueness error at transaction commit rather than `PT422` during the
view statement.

When the constraint remains immediate, a concurrent direct writer that claims
a previously unused deferrable child key is normalised to `PT422` and the
document write remains atomic.

When the client ETag is included in `WHERE`, an initially stale token selects no row and returns `UPDATE 0`. If a change occurs after row selection while the trigger waits for locks, the trigger's post-lock comparison returns `PT412`.

For an ETag-checked referenced object, replacement locks the root first, then
locks the visible current and requested referenced-object rows in deterministic
key order with `FOR SHARE`, and only then locks children and reads the document
back for the final ETag comparison. The locks stabilise both the old reference
and a newly selected reference through `RETURNING`. Mappings with
`object.etag=false` exclude non-key object fields from change detection and do
not take these object row locks. PostgreSQL deadlock or serialisation errors remain whole-transaction
retry conditions when other application statements use conflicting lock orders.

## Delete

Call the generated `<view>__delete_document(expected_id, expected_etag)` function when an explicit-argument API is preferable. Before validating bindings, it takes fixed-order `ACCESS SHARE` locks on every configured source relation so DDL requiring `ACCESS EXCLUSIVE` cannot replace a source between validation and dynamic DML. It then locks the configured document rows, reads the document back, verifies the caller's ETag and removes or unlinks children according to the schema and specification before deleting the root row. A missing ETag returns `PT428`; a stale ETag returns `PT412` and changes nothing.

Direct SQL deletion is supported when the caller filters by both identity and ETag:

```sql
DELETE FROM api.orders_dv
WHERE _id = $1
  AND data #>> '{_metadata,etag}' = $2
RETURNING _id;
```

`OLD.data` contains the document selected by the statement. After locking the root and included child state, the trigger reads the current document again and rejects a changed ETag with `PT412`. If the ETag fails the initial `WHERE` condition, no trigger runs and PostgreSQL returns `DELETE 0`. PostgreSQL triggers cannot inspect whether a particular predicate was written, so clients that require an explicit precondition must follow this statement shape or use the generated function.

## Security and RLS

The extension functions and generated objects use invoker rights. Generated SQL
uses qualified, quoted identifiers and a controlled search path.
Row-level security remains effective on source tables. Reads and ETags are
visibility-relative: they describe only the root, object and child rows visible
to the current role and policies, not a privileged global snapshot. Roles with
different visibility can observe different documents and ETags. Hidden child
rows are preserved during replacement; deletion fails atomically if hidden
dependants prevent removal of the root. A write fails when required visible
rows cannot be locked or changed under the applicable policies. Do not use an
ETag from a different role or policy context as a global revision number.

## REST

PostgREST can expose a schema containing generated views. Keep source tables in a separate, unexposed schema. Use the generated delete function through `/rpc/<view>__delete_document` with `expected_id` and `expected_etag`. PostgreSQL errors map to the REST responses used by the deployment; authentication, role switching and TLS are outside the extension.

## MERGE

Native SQL `MERGE` uses the same insert, update and delete triggers as direct writes to the generated view. A matched update must supply the complete document and the client's ETag. A matched delete must put the client ETag in a `WHEN MATCHED AND` condition while `ON` matches only the identity. A not-matched insert expresses explicit permission to create or recreate that identity. Source keys must be unique. The complete contract is in [`MERGE.md`](MERGE.md).

## Known limits

- one root table, one optional referenced object and one optional child array;
- ordinary source tables must not have classic `INHERITS` descendants; declaratively partitioned roots are supported;
- single-column primary and foreign keys;
- mapped types are limited to the matrix in [`API.md`](API.md); mapped domains,
  enums, JSON types and arrays are not supported, while unmapped domain columns
  retain their table defaults;
- required object and array foreign keys must be validated, enforced and
  non-deferrable; an array foreign key must use `NO ACTION` or `RESTRICT`, not
  `ON DELETE CASCADE`;
- a new array element with an explicit business key must provide every mapped
  field; table defaults apply only when a generated key is omitted;
- an explicit value for a `GENERATED BY DEFAULT` identity does not advance its
  sequence; the deployment must synchronise that sequence or avoid mixing
  explicit and generated values;
- complete replacement rather than partial patching;
- no recursive nesting;
- document-wide ETags require linear work over child collections;
- deployment-specific throughput and latency require separate measurement.
