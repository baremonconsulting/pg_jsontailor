# Security model and residual risks

pg_jsontailor uses security-invoker views and fixed `search_path` values in generated functions. It does not replace PostgreSQL privileges, row-level security, network controls or application authorisation.

## Minimum capabilities

| Operation | Required access |
|---|---|
| Read root-only document | `SELECT` on the view and every mapped table needed by the document |
| Read an array | Root read plus `grant_array_reader` (or exact-signature `EXECUTE` for standalone deployment) |
| Insert | `USAGE` on the API and source schemas; `SELECT, INSERT` on the view and inserted mapped tables; `SELECT` on referenced tables; array-reader capability when returning an array. Identity columns need no sequence grant; a `serial` default needs `USAGE` on its exact sequence. |
| Complete replacement | `USAGE` on both schemas; `SELECT, UPDATE` on the view and mapped tables, operation-specific child `INSERT`/`DELETE`, and array-reader capability. An ETag-covered object also needs a lock-capable column privilege documented by the mapping. |
| Delete by RPC | `USAGE` on the API and source schemas; `SELECT, DELETE` on the view and mapped root/child tables; array-reader and `grant_delete_rpc` (or their exact standalone function signatures) |
| Inspect health | `USAGE` on the API and source schemas plus `EXECUTE` on the exact generated `__check()` function |

Schema `USAGE` is required when resolving functions and relations for writes,
RPCs and health checks. A caller selecting an already-resolved view does not
need source-schema `USAGE` merely for that read; it still needs the mapped-table
`SELECT` privileges required by security-invoker execution. Grant no sequence
privilege for identity columns and never use `ALL SEQUENCES` as a shortcut.

Security-invoker views and triggers require source-table privileges on the
executing database role. Those privileges also allow direct SQL to bypass
document ETags. Therefore the role must be reachable only through the reviewed
document endpoint, not through an arbitrary SQL interface; RLS must enforce the
same tenant boundary on every source table. Default privileges must not grant
generated helpers or source tables to `PUBLIC` or unrelated roles.
Only the schema owner should hold `CREATE` on the API schema; runtime roles
receive `USAGE`, not `CREATE`.

Schema default privileges can independently grant DML, including `DELETE`, on
the generated writable view while generated helper functions remain owner-only.
That mismatched ACL is not a usable or reviewed delete capability and can expose
an unintended direct-delete surface. Revoke broad view defaults, inspect the
view and exact function ACLs after creation/regeneration, and grant only the
capabilities in the tested application-role recipe.

## Residual risks

- SQL `DELETE` or `UPDATE` without `WHERE` affects every visible document. Public HTTP deployments must use the boundary proxy; raw PostgREST must not be externally reachable.
- The delete RPC currently requires `DELETE` on the view. Separating that privilege is deferred.
- The proxy receive timeout applies to individual reads, not the whole request. Apply an ingress deadline too.
- Root-only changes still process mapped children. High-contention, write-heavy workloads are not qualified for 0.2.0.
- Standalone bundles are tied to the generator PostgreSQL major.
- Object fields other than the object key are not independently writable.
- RLS visibility can make documents and ETags role-relative, and native FK or
  uniqueness errors can disclose that a hidden key is allocated. Prefer
  generated identities when key existence is sensitive.
- A client-supplied object key can attempt to reference a row belonging to a
  different tenant. Enforce the same tenant predicate on root and referenced
  tables with RLS, prevent cross-tenant foreign-key targets in the data model,
  and test object-key INSERT and replacement through the application role.
- View creation can succeed even when the future application role lacks
  `SELECT` on a mapped source table. Successful creation is not deployment
  readiness: apply the minimum grants, run `__check()`, and execute read/write
  smoke tests as the actual application role.
- Resource limits do not replace statement timeouts, connection limits or workload isolation.

Record controls and recovery targets in the [deployment record](DEPLOYMENT_RECORD.md).
