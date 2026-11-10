# Troubleshooting

| SQLSTATE/message | Meaning | Action | HTTP |
|---|---|---|---|
| PT413 `stored child limit exceeded` | An RLS-visible collection exceeds `stored_children` | Inspect the source count distribution and repair the oversized root through approved source operations; collection reads reaching it fail, while another point read may work | 413 |
| PT413 `document byte limit exceeded` | Materialised or submitted document exceeds the byte budget | Reduce/split the document; never blindly raise limits | 413 |
| PT422 `document value cannot be converted to the mapped SQL type` | Value does not fit its mapped SQL type | Compare the JSON value with the [supported-type matrix](../API.md#supported-mapped-column-types) | 422 |
| PT422 `_id column requires _id in the document` | Column identity is unconfirmed | Put the same ID in document or omit column | 422 |
| PT422 `source relation role changed; regenerate the duality view` | Relation/PK/FK identity no longer matches the binding | Stop writes; restore the reviewed schema or use the reviewed regeneration/recreation procedure | 422 |
| PT422 `source relation or column binding changed; regenerate the duality view` | Mapped physical columns changed | Stop writes and follow [upgrading](../UPGRADING.md), not CASCADE | 422 |
| 22023 `root.table is not an ordinary or partitioned table` | Root relation is missing, wrongly qualified, or not a table/partitioned table | Correct the schema-qualified root table and regenerate | N/A — generator |
| 22023 `invalid or unsupported root.fields.x` | Mapped `x` is missing, generated, or has an unsupported type | Map a writable column from the [supported-type matrix](../API.md#supported-mapped-column-types) | N/A — generator |
| 22023 `root.key is not a single-column primary key` | Root PK is absent/composite or another column was mapped | Map a supported single-column primary key | N/A — generator |
| 22023 `root.table must have two parts` | Source name is not schema-qualified | Use `schema.table` | N/A — generator |
| 22023 `unknown key root.typo` | Specification includes the unknown root key `typo` | Remove it; `<key>` in `unknown key root.<key>` stands for the actual submitted key | N/A — generator |
| 22023 `invalid mapping root.fields.x` | Field name/value has an invalid mapping shape | Use a valid field name and a nonempty column-name string | N/A — generator |
| 22023 `array.parent_fk requires a validated enforced non-deferrable NO ACTION/RESTRICT foreign key` | Child FK is absent, unvalidated, not enforced, deferrable, or uses an `ON DELETE` CASCADE/SET action | Add the exact validated enforced FK with `NOT DEFERRABLE` and `ON DELETE NO ACTION` or `RESTRICT` | N/A — generator |
| PT422 valid child parent-FK/order/key index required | Array access lacks its required live B-tree prefix | Create the documented child index on every partition and rerun `__check()` | 422 |
| 22023 `generated object name exceeds 63 bytes` | View name leaves no room for generated helper suffixes | Choose a shorter schema/view name; the practical view-name limit is 46 bytes | N/A — generator |
| PT428 missing ETag | Required token omitted | Re-read and submit current token | 428 |
| PT412 ETag changed after locking | Concurrent change | Roll back, re-read, retry | 412 |
| 0A000 `document writes require READ COMMITTED isolation` | Writes use an unsupported isolation level | Use READ COMMITTED and retry the whole transaction | 400 |
| 40001 serialisation failure | Concurrent transaction conflict | Retry the whole transaction with a fresh document and ETag | Upstream mapping; verify at the deployed boundary |
| 40P01 deadlock | Transactions acquired conflicting locks | Retry the whole transaction with bounded backoff | 500 (tested PostgREST contract) |
| 42501 `permission denied for function orders_dv__bounded_array` | Array-reader capability is absent | Apply the reviewed exact-signature grant | 401 anonymous / 403 authenticated |
| 42501 `delete RPC requires DELETE on the duality view` | Function EXECUTE alone does not grant deletion | Apply reviewed view DELETE plus the required source/reader capabilities | 401 anonymous / 403 authenticated |
| 42501 `row-level security prevents locking root` | Root SELECT visibility and write-lock/RLS permissions disagree | Review policies as the actual application role; do not disable RLS or use SECURITY DEFINER | 401 anonymous / 403 authenticated |
| PT422 `root insert is disabled` / `root update is disabled` / `root delete is disabled` | The corresponding operation flag defaults to false | Enable only the intended operation in the reviewed specification and recreate/regenerate appropriately | 422 |
| PT422 `array insert is disabled` / `array update is disabled` / `array delete is disabled` | Child reconciliation needs an operation not enabled in the mapping | Review intended child operations; do not silently broaden them | 422 |
| PT422 `object fields do not match referenced row` | Replacement edits non-key object fields with `object.etag=true` | Re-read the referenced values; object fields are not independently writable | 422 |
| 42P10 `there is no unique or exclusion constraint matching the ON CONFLICT specification` | A view has no native conflict target | Use the documented guarded INSERT/replacement/MERGE contract, not upsert | 400 |
| 23502 `null value in column "order_id" of relation "order_items" violates not-null constraint` | Deleting a root with `array.delete=false` attempts to unlink a NOT NULL child FK | Choose reviewed child deletion or a nullable unlink FK; rollback preserves data | 400 (native PostgREST mapping) |
| 22023 `resource limit must be a supported integer` / unknown `limits` key | Limit is unknown, non-integer, or outside its supported range | Remove the key or use a documented integer and range | N/A — generator |

Keep the complete server message and SQLSTATE in the customer environment only.
Before sharing outside it, redact DETAIL lines and values, identifiers and any
customer-sensitive context, and use the agreed private channel; native constraint
DETAIL can contain key values. Do not infer a fix from the HTTP status alone.
A failed create/regeneration is transactional: correct
the schema or specification and rerun the complete operation.
See [oversized-source detection and recovery](../RESOURCE_LIMITS.md#oversized-source-data)
and [private support](../../SUPPORT.md). Native messages include actual names;
the examples above use only the shipped synthetic fixture.

