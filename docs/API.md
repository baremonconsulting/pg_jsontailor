# API and specification

`pg_jsontailor` maps writable JSON documents to ordinary PostgreSQL tables. Its
document model is duality-like, but it does not claim Oracle JSON Relational
Duality View or ORDS compatibility.

`pg_jsontailor` exposes seven functions in the `jsontailor` schema.

```sql
jsontailor.duality_view_ddl(view_name text, spec jsonb) RETURNS text
jsontailor.create_duality_view(view_name text, spec jsonb) RETURNS void
jsontailor.duality_view_inventory() RETURNS TABLE (
  view_name text, generator_version text, generator_hash text,
  current_generator_hash text, health_status text, health_detail text)
jsontailor.regenerate_duality_view(view_name text) RETURNS void
jsontailor.grant_delete_rpc(view_name text, role_name text, grant_option boolean DEFAULT false) RETURNS void
jsontailor.grant_array_reader(view_name text, role_name text, grant_option boolean DEFAULT false) RETURNS void
jsontailor.review_helper_grant(view_name text, role_name text, helper_suffix text, grant_option boolean) RETURNS void
```

`duality_view_ddl` validates the specification against PostgreSQL catalogues and returns a complete DDL script. `create_duality_view` executes the same script in the caller's transaction. `duality_view_inventory` reports generator provenance and evaluates catalogue health without executing discovered callbacks. `regenerate_duality_view` transactionally replaces a managed view from its stored specification. None of these functions is a security definer.

`grant_delete_rpc` and `grant_array_reader` are owner-only review operations:
they grant the named role its explicit delete or array-read capability and record
the owner, recipient and grant-option provenance in managed-view metadata.
`review_helper_grant` is their shared owner-only primitive and accepts only
`delete_document` or `bounded_array` as `helper_suffix`. All seven functions use
invoker privileges; calling a review operation does not confer view ownership.

Both the view name and source table names must contain exactly two identifier
parts, for example `api.orders_dv` and `shop.orders`. A view name is limited to
46 UTF-8 bytes so generated suffixes fit PostgreSQL identifiers. Names
containing `$revoke$` fail closed in 0.2.0 and must not be used.

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

Generated columns are not supported in mapped keys, fields, foreign keys or
array ordering. Keys must be single-column primary keys. `root.fields` must
not remap the root key or `object.root_fk`; each source column can appear only
once within a role's fields. The object key must appear in `object.fields`,
and the child key in `array.fields`. The child `parent_fk` must not appear in
`array.fields`. Object and child foreign keys must be writable, validated,
enforced and non-deferrable; the object foreign key cannot be the root primary
key, and the child foreign key must use `NO ACTION` or `RESTRICT`.
`GENERATED ALWAYS` identity fields are not writable, except that a child key
may be mapped and omitted on insert so its sequence generates the value.

ETags are the first 128 bits of SHA-256 encoded as 32 uppercase hexadecimal
characters, not authentication tokens. See [`SEMANTICS.md`](SEMANTICS.md) for
object-key coverage with `object.etag=false` and visibility-relative RLS reads.

Finite AD `timestamptz` values are emitted as UTC strings in the fixed form
`YYYY-MM-DDTHH:MM:SS.ffffffZ`; finite BC values append the PostgreSQL era suffix
` BC`. Input strings may contain any PostgreSQL-accepted
time-zone offset; reads normalise the represented instant to UTC. This makes
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
- `api.orders_dv__bounded_array(bigint)`, the owner-only cardinality gate used before array aggregation;
- zero-row `api.orders_dv__root_binding` and optional `__object_binding` and `__array_binding` internal views;
- a view comment containing the normalised specification, generator version and generator hash.

Generated objects have no runtime dependency on the `jsontailor` schema. Existing objects are not replaced automatically. For every configured source role the generator creates a zero-row `<view>__<role>_binding` view in the API schema. These internal views carry an unambiguous PostgreSQL dependency on that role's relation and mapped columns; they expose no rows, are intrinsically non-updatable, and receive no explicit application grants. The non-updatable property remains effective if deployment-wide default privileges or a later broad grant includes the helper views. If a configured source relation changes role, is renamed or replaced, mapped column names are renamed, reused or permuted, classic inheritance is added, or a required foreign key becomes invalid or not enforced, generated writes fail with `PT422` until the view is regenerated. The guard also checks the relative physical order of mapped attributes, the role's primary-key constraint identity and configured foreign-key direction. Adding or dropping an unmapped source column does not invalidate the binding. The guard remains valid after dump and restore and does not depend on rendered view definitions or `quote_all_identifiers`. Renaming a bound primary-key constraint therefore also requires regeneration.

The generated SQL is copied into each generated object. Regenerate a view after upgrading the extension when the release notes identify a generated write-path change.
Name-changing drift requires an updated source-controlled specification and
transactional recreation, not a call to the name-preserving regenerator. See
[`UPGRADING.md`](UPGRADING.md); regeneration never infers renamed tables or columns.

Statement writes and the generated `__delete_document` helper take fixed-order
`ACCESS SHARE` locks on configured source relations before validating their
bindings. These locks stabilise relation identity against DDL that requires
`ACCESS EXCLUSIVE`; they do not replace row locks, RLS or a write-free reviewed
schema-migration window.

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

`health_status=OK` means validated catalogue bindings. `SKIPPED` means the
caller cannot safely evaluate the row and is not a health result. Other values
are SQLSTATEs from validation and `health_detail` describes the observed
problem. Inventory discovery begins with an owner-editable catalogue comment;
the function validates it against trusted catalogue data before reporting OK.

`regenerate_duality_view` must be run by the view owner. It reads the stored
normalised specification, rejects dependent relations and unmanaged view
triggers or rules, generates and validates replacement DDL before dropping
anything, and replaces the generated objects inside the caller's transaction.
Only binding views for source roles configured in the stored specification are
replaced. Objects using optional binding names for absent roles are left intact;
an incompatible object at a configured binding name blocks regeneration.
Direct relation and column grants issued by the owner to other recipients,
including grant options, and generated-function comments are restored.
Generated-function EXECUTE grants are restored only for the delete and array-read
capabilities whose existing ACL entries still match recorded reviewed provenance;
unreviewed, revoked, default or legacy helper grants are not replayed. Reapply
approved capabilities through `grant_delete_rpc` and `grant_array_reader`, not a
blanket function grant. A non-default ACL entry for the
owner itself on the managed view, one of its columns or a managed generated
function blocks regeneration with `2BP01` before any object is replaced. The
regenerator does not guess whether an owner self-grant or self-revoke should be
recreated; resolve that ACL state explicitly before retrying.
Delegated grant chains are rejected before replacement because replaying them
as the owner would change later `REVOKE ... CASCADE` semantics. The metadata comment is
deliberately replaced with current provenance. A failure rolls back the complete
operation. The function never uses `CASCADE`; dependencies on other generated
objects also cause PostgreSQL to stop and roll back the operation. Remove and
explicitly recreate reviewed application triggers or rules around regeneration;
they are never silently dropped or reconstructed.

Write paths deserialise only the physical columns named by the specification.
Unmapped columns, including columns using NOT NULL domains, are omitted from
the generated projection so their table or domain defaults remain effective.

## Permissions

The caller needs `CREATE` on the target schema and the relevant privileges on source tables. Runtime users need privileges on the view and source tables for the operations they perform. Reads require `SELECT`; writes additionally require the corresponding `INSERT`, `UPDATE` or `DELETE` privileges. Root and array rows locked with `SELECT FOR UPDATE` require `UPDATE`. During replacement, MERGE UPDATE and delete, a referenced object included in the ETag is stabilised with `SELECT FOR SHARE`; PostgreSQL requires `SELECT` and `UPDATE` on at least one column of the referenced table for that lock. A column-level `UPDATE` grant is sufficient and does not permit other columns to be changed. Mappings with `object.etag=false` do not acquire this object lock. Row-level security policies must also permit every required read, lock and write.

Generated functions default to owner-only EXECUTE. Array document projection
also requires explicit execution of `__bounded_array(bigint)`; grant this as the
view owner with `jsontailor.grant_array_reader('api.orders_dv','application_role',false)`.
The explicit delete RPC separately requires
`jsontailor.grant_delete_rpc('api.orders_dv','application_role',false)`.
Neither capability grants source-table or view privileges. Trigger, guard and
health-check helpers remain owner-only. Standalone generated SQL has no
`jsontailor` runtime schema: apply reviewed exact-signature EXECUTE grants for
the bounded-array and delete helpers directly, never `GRANT ... ON ALL FUNCTIONS`.
See [`RESOURCE_LIMITS.md`](RESOURCE_LIMITS.md) for limit configuration and migration.

## Errors

- `22023`: invalid specification or argument;
- `0A000`: document write attempted outside `READ COMMITTED` isolation;
- `PT412`: stale ETag;
- `PT428`: required ETag missing;
- `PT422`: invalid document or operation disabled by the specification;
- `PT413`: document bytes, nesting depth or supplied/stored child count exceeds its configured resource limit;
- `42501`: insufficient privilege or RLS coverage failure.

`PT422` includes a mapped JSON value with the wrong JSON type, a numeric value
that cannot be represented without loss at the target precision and scale, an
explicit value for a `GENERATED ALWAYS` identity, an array key owned by another
document, and an array row for which no target partition exists. SQL/JSON null
is passed to PostgreSQL so target nullability remains authoritative. Unrelated
table constraints keep their native SQLSTATE rather than being rewritten.
`PT422 object fields do not match referenced row` means a replacement supplied
non-key object fields inconsistent with the referenced row while
`object.etag=true`; these fields are not independently writable.

The mapped child primary key may be `DEFERRABLE INITIALLY IMMEDIATE`. Generated
writes detect that property after stabilising source identities and use an
explicit-key fallback. Visible existing identities and concurrent mapped-key
claims under immediate checking are reported as `PT422`; secondary uniqueness
failures retain native `23505` diagnostics. Explicitly deferred transaction
constraint mode is not changed by the extension.

PostgreSQL can also return `40001` (serialisation failure) or `40P01` (deadlock) for concurrent transactions. Retry
the complete transaction after either error, re-read the document and use its current ETag; do not replay only the
failed statement with an old token.

Run DDL returned by `duality_view_ddl` in one transaction with stop-on-error behaviour.

For direct SQL deletion, constrain the generated view by both `_id` and `data #>> '{_metadata,etag}'`. A token that is already stale selects no row (`DELETE 0`). If the document changes while the trigger waits for locks, its post-lock check returns `PT412`. The trigger cannot require or inspect a client-supplied predicate: deleting by identity alone is allowed but does not assert the client's earlier ETag. The generated delete function provides the explicit-argument contract and returns `PT412` for both stale and missing documents.

The same predicate pattern applies to updates. The client may provide only the identity, earlier ETag and changed values while the `SET` expression builds complete `NEW.data` from the view's `data` column. An initially stale token returns `UPDATE 0`; a post-selection race returns `PT412`.

## Native SQL MERGE

Native PostgreSQL `MERGE` can use the generated view for a tested subset of insert, complete replacement, ETag-checked delete and `DO NOTHING` actions. It reuses the generated action-specific triggers. Match only on the typed root identity, place a delete ETag check in the matched-action condition and make source keys unique before the statement. See [`MERGE.md`](MERGE.md) for the exact contract, staging examples and concurrency limits.
