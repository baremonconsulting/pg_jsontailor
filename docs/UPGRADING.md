# Upgrading generated views

Generated views contain a copy of the generator's SQL. Installing newer
extension files does not alter an existing view. Keep each specification and
reviewed bundle in source control, then regenerate only through a planned
deployment transaction.

The 0.2.0 generated write functions remove per-document subtransactions from
ordinary bulk writes. Regenerate existing duality views after installing this
version; upgrading the extension alone does not replace SQL already copied
into generated objects.

Generated views contain copied SQL and PL/pgSQL. Upgrading the `pg_jsontailor` extension changes the generator, but does not rewrite objects that were generated earlier. Regenerate each mapping when release notes identify a generated write-path change or after a supported source-schema change.

The current replacement path stabilises ETag-included referenced objects with
`FOR SHARE`. Before regenerating, verify that replacement and MERGE roles have
`SELECT` plus `UPDATE` on at least one referenced-table column and that RLS
permits the required locks. A column-level `UPDATE` grant is sufficient.

Statement writes and explicit `__delete_document` calls also hold fixed-order
`ACCESS SHARE` locks on all configured source relations. Schema migrations that
need `ACCESS EXCLUSIVE` wait for those transactions; use a reviewed write-free
window and deployment-appropriate `lock_timeout`. Missing source relations are
reported as `PT422`. Regenerate existing helpers to obtain this behaviour.

Any view generated before the selected pilot candidate must be regenerated from
that pinned full commit and retained verified bundle before the pilot. Public
release still requires the separate tag, benchmark and release-authority gate.

The mixed-type validation correction changes the generator definition and its
provenance hash. Regenerate every view made by an earlier 0.2.0 development
snapshot; reinstalling the extension alone is insufficient.

## Source-schema change matrix

| Change | Effect | Required action |
|---|---|---|
| Add or drop an unmapped column | Binding remains valid | No regeneration; run the health check |
| Rename, replace or move a source table | Stored specification no longer identifies the same relation | Update the specification in source control and explicitly recreate the mapping |
| Rename a mapped column | Writes return `PT422`; the stored specification still uses the old name | Change the source-controlled specification and transactionally recreate the mapping |
| Reuse or permute a mapped column without changing its name | Writes return `PT422` | Review the new meaning; regenerate only if the stored specification remains correct |
| Change a mapped column type | PostgreSQL normally blocks the change while binding views depend on it | Regenerate through a reviewed migration using the updated specification |
| Rename a bound primary-key constraint | Writes return `PT422` | Regenerate |
| Change child primary-key deferrability | Existing generated code may use the previous explicit-key path | Quiesce writers and regenerate |
| Change or invalidate a required foreign key | Writes return `PT422`; unsupported actions are rejected | Restore a validated, enforced, non-deferrable supported FK and regenerate |
| Add classic `INHERITS` descendants | Writes return `PT422` | Remove the unsupported inheritance or redesign the mapping |
| Attach or detach declarative partitions | Supported when the mapped root contract remains valid | Run health and functional checks |
| Dump and restore | Generated bindings are restored by catalogue dependency | Run inventory, health, privilege and smoke checks |
| Logical replication | DDL and generated objects are not assumed to replicate | Install the extension and generate or regenerate mappings separately on the subscriber |

Never use regeneration to guess a new table or column name. Update the
source-controlled specification explicitly when names or mappings change.
Name-preserving regeneration reuses the stored specification: it is suitable
for a primary-key constraint rename or a generator upgrade, not a renamed
mapped column or changed JSON field. Specification changes require transactional
recreation, even if the view name stays the same.

Keep every JSON mapping in source control. Before regeneration, inventory objects that depend on the generated view and confirm that no application writer is active. Inspect all managed views and their catalogue health first:

```sql
SELECT * FROM jsontailor.duality_view_inventory();
SELECT api.orders_dv__check();
```

Run inventory as the owner of the managed views when catalogue health is
required. Inventory reports views owned by other roles as `SKIPPED` and never
executes their health functions. An explicitly qualified `__check()` invocation
is a separate operational action that executes code owned by the managed-view
owner and must be reviewed accordingly.

For a view generated with provenance metadata, use the transactional
regenerator. It preserves direct view grants issued by the owner to other
recipients and generated-function comments, rejects
dependent relations and unmanaged triggers or rules, and never uses `CASCADE`.
Direct relation and column grants to other recipients, including grant options,
are preserved. A non-default owner ACL on the managed view, one of its columns
or a managed generated function blocks regeneration before any DDL. Review and
resolve that state explicitly; the regenerator does not silently restore default
owner privileges. Delegated grant chains
block regeneration before any object is replaced and must be reapplied through
the reviewed deployment procedure. Application triggers and rules must be removed and
explicitly recreated as reviewed deployment objects.

Generated-function EXECUTE grants survive regeneration only when the existing
delete or array-read ACL entry still matches its recorded reviewed provenance.
Unreviewed, revoked, default and earlier helper grants are not restored. Review
approved delete capabilities with `jsontailor.grant_delete_rpc` and array-read
capabilities with `jsontailor.grant_array_reader` as the view owner. Array document
reads require the owner-only `__bounded_array(bigint)` capability in addition
to source/view SELECT and RLS; otherwise PostgreSQL returns `42501`. Trigger,
guard and health-check helpers remain owner-only. See
[`RESOURCE_LIMITS.md`](RESOURCE_LIMITS.md) for migration and `PT413` resource limits.

Regenerate inside the reviewed deployment transaction:

```sql
BEGIN;
SELECT jsontailor.regenerate_duality_view('api.orders_dv');
-- Run deployment-specific read, privilege and RLS acceptance checks here.
COMMIT;
```

If a consumer depends on the view, PostgreSQL stops the operation instead of silently destroying that consumer. Failure rolls back all drops and creations. The function also accepts the legacy `pg_jsontailor duality view spec v1` comment written by earlier 0.2.0 development builds and replaces it with current provenance metadata.

## Transactional recreation when the specification changes

For example, renaming `shop.orders.status` to `state` requires changing
`root.fields.status` from `status` to `state` in the source-controlled JSON.
Do not call `regenerate_duality_view`: its stored specification still names
`status`. The JSON field can remain `status` while its source column changes.

For a view whose metadata comment was removed or replaced, recover the mapping
and its original generated DDL from source control before attempting cleanup.
Compare `pg_get_viewdef`, `pg_get_functiondef`, owner and exact function
signatures with that reviewed export. If the mapping's ownership cannot be
proved, stop and resolve it manually; a matching name alone is not provenance.

Record view, column and function ACLs, grant options/grantors, comments,
dependent-object definitions and RLS policies before starting. Source-table RLS
policies are not dropped by this procedure. Reapply only the reviewed grants;
never copy earlier or default `PUBLIC` helper grants indiscriminately. Test with
the actual application role, not a superuser or an RLS-bypassing owner.

The following exact identity list applies only to the complete shipped orders
mapping with a `bigint` root key and all three source roles. Confirm those
identities belong to the mapping before execution. It intentionally preserves
unrelated overloads such as `orders_dv__check(integer)`; do not replace it with
a function-name search or drop optional binding names absent from your mapping.
For a mapped rename, include the `ALTER TABLE` after the drops and before
creation, and supply the updated `orders_dv_spec`:

```sql
BEGIN;

DROP VIEW api.orders_dv;
DROP VIEW api.orders_dv__root_binding;
DROP VIEW api.orders_dv__object_binding;
DROP VIEW api.orders_dv__array_binding;
DROP FUNCTION api.orders_dv__insert();
DROP FUNCTION api.orders_dv__update();
DROP FUNCTION api.orders_dv__delete();
DROP FUNCTION api.orders_dv__guard();
DROP FUNCTION api.orders_dv__delete_document(bigint, text);
DROP FUNCTION api.orders_dv__check();
DROP FUNCTION api.orders_dv__bounded_array(bigint);

-- For the mapped-column rename example only:
ALTER TABLE shop.orders RENAME COLUMN status TO state;

SELECT jsontailor.create_duality_view(
    'api.orders_dv',
    :'orders_dv_spec'::jsonb
);

-- Reapply deployment-specific view/column/function grants, comments and
-- dependent objects from the reviewed manifest before acceptance testing.

-- Run deployment-specific catalogue, read, privilege and RLS acceptance checks here.
-- Exercise writes only against disposable test rows created inside this transaction.

COMMIT;
```

Pass `orders_dv_spec` as a `psql` variable or application parameter; do not interpolate untrusted text into the script. The runtime role also needs the documented source-table privileges, explicitly reviewed array-reader capability and, when used, delete-function capability. Reapply those through the owner-only review APIs after recreation, before deployment-role document reads. If dependent objects must be replaced, inventory their definitions, owners and grants first, then include their explicit drop and recreation in the same reviewed transaction. Do not substitute an unqualified `CASCADE` operation. PostgreSQL stops on an unhandled dependency, and `ROLLBACK` restores the original objects, column names and grants. Run with `psql -X -v ON_ERROR_STOP=1`; do not commit a session after ignoring a failure.

Before committing, verify generated objects, grants, a representative read and row-level security with the deployment role. Perform an ETag-checked replacement and delete only against disposable rows created within the transaction, enclosing the write checks in a savepoint and using `ROLLBACK TO SAVEPOINT` before the final commit. After committing, run separate non-mutating smoke tests against the deployed view. A relation role change, rename, replacement, primary-key constraint rename or mapped-column identity change is detected by generated write paths and returns `PT422` until regeneration. PostgreSQL dump and restore preserve the binding check because it is resolved from restored per-role binding-view dependencies, constraint names and catalogue structure rather than stored numeric OIDs. Binding views for roles present in the reviewed mapping are generated implementation objects and must be dropped and recreated with the main view as shown above. Objects at optional binding names for absent roles are outside that mapping and must not be removed.

Generated write statements acquire `ACCESS SHARE` locks on configured root,
object and array source relations in that fixed order before validating their
catalogue bindings. Source DDL requiring `ACCESS EXCLUSIVE`, including relation
renames and replacements, therefore waits for active writers. Schedule source
schema changes in a write-free maintenance window;
set a deployment-appropriate `lock_timeout` so an accidental overlap fails
instead of waiting indefinitely.

If validation fails before `COMMIT`, issue `ROLLBACK`. Regeneration replaces database objects and therefore requires a planned write window; it is not an online schema migration mechanism.
