# API and specification

`pg_jsontailor` maps writable JSON documents to ordinary PostgreSQL tables. Its document model can support Oracle-to-PostgreSQL migration work, but it is not a drop-in implementation of every Oracle duality-view or ORDS feature.

`pg_jsontailor` exposes four functions in the `jsontailor` schema.

```sql
jsontailor.duality_view_ddl(view_name text, spec jsonb) RETURNS text
jsontailor.create_duality_view(view_name text, spec jsonb) RETURNS void
jsontailor.duality_view_inventory() RETURNS TABLE (...)
jsontailor.regenerate_duality_view(view_name text) RETURNS void
```

`duality_view_ddl` validates the specification against PostgreSQL catalogues and returns a complete DDL script. `create_duality_view` executes the same script in the caller's transaction. `duality_view_inventory` reports generator provenance and evaluates catalogue health without executing discovered callbacks. `regenerate_duality_view` transactionally replaces a managed view from its stored specification. None of these functions is a security definer.

Both the view name and source table names must contain exactly two identifier parts, for example `api.orders_dv` and `shop.orders`.

Source roles may use ordinary leaf tables or declaratively partitioned table roots. Ordinary tables with classic `INHERITS` descendants are rejected because their default scans cannot guarantee document-key uniqueness across independently constrained physical tables.

## Specification shape

```json
{
  "root": {
    "table": "shop.orders",
    "key": "order_id",
    "fields": {"status": "status", "created": "created_at"},
    "insert": true,
    "update": true,
    "delete": true
  },
  "object": {
    "name": "customer",
    "table": "shop.customers",
    "key": "customer_id",
    "root_fk": "customer_id",
    "fields": {"customerId": "customer_id", "name": "name"},
    "etag": false
  },
  "array": {
    "name": "items",
    "table": "shop.order_items",
    "key": "item_id",
    "parent_fk": "order_id",
    "order_by": "item_id",
    "fields": {
      "itemId": "item_id",
      "product": "product",
      "qty": "quantity",
      "price": "unit_price"
    },
    "insert": true,
    "update": true,
    "delete": true
  }
}
```

`root` is required. `object` and `array` are optional. Operation flags default to `false`; `object.etag` defaults to `true`; and `array.order_by` defaults to the array key.

Field names must match `^[A-Za-z][A-Za-z0-9_]{0,62}$`. `_id` and `_metadata` are reserved. Source identifiers are resolved from PostgreSQL catalogues and quoted in generated SQL.

## Supported mapped column types

| PostgreSQL type | Required JSON type |
|---|---|
| `text`, `varchar`, `bpchar` | string |
| `int2`, `int4`, `int8` | number |
| `numeric` | number; canonical strings `NaN`, `Infinity` and `-Infinity` for non-finite values |
| `boolean` | boolean |
| `date`, `timestamp`, `timestamptz`, `uuid` | string |

Mapped domains, enums, `json`/`jsonb`, arrays and other types are not
supported. An unmapped column may use any type supported by its source table,
including a domain, because the generated write projection omits it.

Finite AD `timestamptz` values are emitted as UTC strings in the fixed form
`YYYY-MM-DDTHH:MM:SS.ffffffZ`; finite BC values append the PostgreSQL era suffix
` BC`. Input strings may contain any PostgreSQL-accepted
time-zone offset; reads normalize the represented instant to UTC. This makes
documents and ETags independent of the session `TimeZone` setting while keeping
BC and AD instants distinct and reversible.

## Generated objects

For a view named `api.orders_dv`, the generator creates:

- `api.orders_dv`;
- `api.orders_dv__insert()` and its `INSTEAD OF INSERT` trigger;
- `api.orders_dv__update()` and its `INSTEAD OF UPDATE` trigger;
- `api.orders_dv__delete()` and its `INSTEAD OF DELETE` trigger;
- `api.orders_dv__guard()` and statement-level catalogue-guard triggers;
- `api.orders_dv__delete_document(expected_id, expected_etag)`, using the root key type;
- `api.orders_dv__check()`, a non-mutating catalogue health check;
- zero-row `api.orders_dv__root_binding` and optional `__object_binding` and `__array_binding` internal views;
- a view comment containing the normalised specification, generator version and generator hash.

Generated objects have no runtime dependency on the `jsontailor` schema. Existing objects are not replaced automatically. For every configured source role the generator creates a zero-row `<view>__<role>_binding` view in the API schema. These internal views carry an unambiguous PostgreSQL dependency on that role's relation and mapped columns; they expose no rows, are intrinsically non-updatable, and receive no explicit application grants. The non-updatable property remains effective if deployment-wide default privileges or a later broad grant includes the helper views. If a configured source relation changes role, is renamed or replaced, mapped column names are renamed, reused or permuted, classic inheritance is added, or a required foreign key becomes invalid or not enforced, generated writes fail with `PT422` until the view is regenerated. The guard also checks the relative physical order of mapped attributes, the role's primary-key constraint identity and configured foreign-key direction. Adding or dropping an unmapped source column does not invalidate the binding. The guard remains valid after dump and restore and does not depend on rendered view definitions or `quote_all_identifiers`. Renaming a bound primary-key constraint therefore also requires regeneration.

The generated SQL is copied into each generated object. Regenerate a view after upgrading the extension when the release notes identify a generated write-path change.

## Inventory, health and regeneration

`duality_view_inventory()` returns every managed view and its recorded and
installed generator provenance. It validates the stored specification and its
structured binding provenance against the current catalogues and never executes the discovered `__check()` function.
Rows owned by another role, or rows whose health function has an unexpected
owner or signature, or legacy metadata without binding provenance, are reported
as `SKIPPED`. `OK` means the stored catalogue bindings still satisfy the generated
guards. Explicitly invoking a `__check()`
function remains a separate privileged operational action. A different recorded and
current hash means regeneration is available even when the schema health check
is otherwise `OK`.

`regenerate_duality_view` must be run by the view owner. It reads the stored
normalised specification, rejects dependent relations and unmanaged view
triggers or rules, generates and validates replacement DDL before dropping
anything, and replaces the generated objects inside the caller's transaction.
Only binding views for source roles configured in the stored specification are
replaced. Objects using optional binding names for absent roles are left intact;
an incompatible object at a configured binding name blocks regeneration.
Direct owner-issued relation and column grants, including grant options,
generated-function grants and generated-function comments are restored.
Delegated grant chains are rejected before replacement because replaying them
as the owner would change later `REVOKE ... CASCADE` semantics. The metadata comment is
deliberately replaced with current provenance. A failure rolls back the complete
operation. The function never uses `CASCADE`; dependencies on other generated
objects also cause PostgreSQL to stop and roll back the operation. Remove and
explicitly recreate reviewed application triggers or rules around regeneration;
they are never silently dropped or reconstructed.

Write paths deserialize only the physical columns named by the specification.
Unmapped columns, including columns using NOT NULL domains, are omitted from
the generated projection so their table or domain defaults remain effective.

## Permissions

The caller needs `CREATE` on the target schema and the relevant privileges on source tables. Runtime users need privileges on the view and source tables for the operations they perform. Reads require `SELECT`; writes additionally require the corresponding `INSERT`, `UPDATE` or `DELETE` privileges. Root and array rows locked with `SELECT FOR UPDATE` require `UPDATE`. A referenced object included in the ETag is stabilized with `SELECT FOR SHARE`; PostgreSQL requires `SELECT` and `UPDATE` on at least one column of the referenced table for that lock. A column-level `UPDATE` grant is sufficient and does not permit other columns to be changed. Row-level security policies must also permit every required read, lock and write.

## Errors

- `22023`: invalid specification or argument;
- `PT412`: stale ETag;
- `PT428`: required ETag missing;
- `PT422`: invalid document or operation disabled by the specification;
- `42501`: insufficient privilege or RLS coverage failure.

`PT422` includes a mapped JSON value with the wrong JSON type, a numeric value
that cannot be represented without loss at the target precision and scale, an
explicit value for a `GENERATED ALWAYS` identity, an array key owned by another
document, and an array row for which no target partition exists. SQL/JSON null
is passed to PostgreSQL so target nullability remains authoritative. Unrelated
table constraints keep their native SQLSTATE rather than being rewritten.

PostgreSQL can also return `40001` (serialization failure) or `40P01` (deadlock) for concurrent transactions. Retry
the complete transaction after either error, re-read the document and use its current ETag; do not replay only the
failed statement with an old token.

Run DDL returned by `duality_view_ddl` in one transaction with stop-on-error behaviour.

For direct SQL deletion, constrain the generated view by both `_id` and `data #>> '{_metadata,etag}'`. A token that is already stale selects no row (`DELETE 0`). If the document changes while the trigger waits for locks, its post-lock check returns `PT412`. The generated delete function provides the alternative explicit-argument contract and returns `PT412` for both stale and missing documents.

The same predicate pattern applies to updates. The client may provide only the identity, earlier ETag and changed values while the `SET` expression builds complete `NEW.data` from the view's `data` column. An initially stale token returns `UPDATE 0`; a post-selection race returns `PT412`.

## Native SQL MERGE

Native PostgreSQL `MERGE` can use the generated view for a tested subset of insert, complete replacement, ETag-checked delete and `DO NOTHING` actions. It reuses the generated action-specific triggers. Match only on the typed root identity, place a delete ETag check in the matched-action condition and make source keys unique before the statement. See [`MERGE.md`](MERGE.md) for the exact contract, staging examples and concurrency limits.
