# Troubleshooting

| SQLSTATE/message | Meaning | Action | HTTP |
|---|---|---|---|
| PT413 limit exceeded | Resource bound exceeded | Reduce document or review bound | 413 |
| PT422 `document value cannot be converted to the mapped SQL type` | Value does not fit its mapped SQL type | Compare the JSON value with the [supported-type matrix](../API.md#supported-mapped-column-types) | 422 |
| PT422 `_id column requires _id in the document` | Column identity is unconfirmed | Put the same ID in document or omit column | 422 |
| PT422 binding changed | Catalogue no longer matches metadata | Stop writes, review, regenerate | 422 |
| 22023 `root.table is not an ordinary or partitioned table` | Root relation is missing, wrongly qualified, or not a table/partitioned table | Correct the schema-qualified root table and regenerate | N/A — generator |
| 22023 `invalid or unsupported root.fields.x` | Mapped `x` is missing, generated, or has an unsupported type | Map a writable column from the [supported-type matrix](../API.md#supported-mapped-column-types) | N/A — generator |
| 22023 root key is not a primary key | The root identity cannot be locked unambiguously | Add or map the declared primary key | N/A — generator |
| 22023 `array.parent_fk requires a validated enforced non-deferrable NO ACTION/RESTRICT foreign key` | Child FK is absent, unvalidated, not enforced, deferrable, or uses an `ON DELETE` CASCADE/SET action | Add the exact validated enforced FK with `NOT DEFERRABLE` and `ON DELETE NO ACTION` or `RESTRICT` | N/A — generator |
| PT422 valid child parent-FK/order/key index required | Array access lacks its required live B-tree prefix | Create the documented child index on every partition and rerun `__check()` | 422 |
| 22023 `generated object name exceeds 63 bytes` | View name leaves no room for generated helper suffixes | Choose a shorter schema/view name; the practical view-name limit is 46 bytes | N/A — generator |
| PT428 missing ETag | Required token omitted | Re-read and submit current token | 428 |
| PT412 ETag changed after locking | Concurrent change | Roll back, re-read, retry | 412 |
| 0A000 isolation | Writes are not READ COMMITTED | Use READ COMMITTED | 400 |
| 40001 serialisation failure | Concurrent transaction conflict | Retry the whole transaction with a fresh document and ETag | Upstream mapping; verify at the deployed boundary |
| 40P01 deadlock | Transactions acquired conflicting locks | Retry the whole transaction with bounded backoff | 500 (tested PostgREST contract) |
| 42501 `permission denied for function orders_dv__bounded_array` | Array-reader capability is absent | Apply the reviewed exact-signature grant | 401 anonymous / 403 authenticated |
| 42P10 view upsert unsupported | No native conflict target | Use explicit semantics | 400 |
| 22023 `resource limit must be a supported integer` / unknown `limits` key | Limit is unknown, non-integer, or outside its supported range | Remove the key or use a documented integer and range | N/A — generator |

Always retain the complete server message and SQLSTATE. Do not infer a fix from
the HTTP status alone. A failed create/regeneration is transactional: correct
the schema or specification and rerun the complete operation.

