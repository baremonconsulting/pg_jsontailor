# Workload performance qualification

The configured admission ceiling is not a performance promise. Identity
lookups, typed relational prefilters, document JSON predicates and deep OFFSET
pages are distinct workloads and must not share a single qualification result.

## Read access paths

Prefer identity keyset pagination and indexed typed predicates before document
construction. For an order listing, select candidate identities from the root
table and then join the generated view:

```sql
SELECT document._id, document.data
FROM (
    SELECT order_id
    FROM shop.orders
    WHERE status = 'approved' AND order_id > 42
    ORDER BY order_id
    LIMIT 20
) AS selected
JOIN shop.orders_dv AS document ON document._id = selected.order_id
ORDER BY document._id;
```

Use the same invoker role and tenant/RLS context on both relations. Review the
actual plan: a SQL subquery is not itself a guarantee of execution order or
bounded work. The acceptance plan must show document-construction loops close
to the selected page cardinality. A small LIMIT after JSON filtering, sorting
or a deep OFFSET does not establish that property. PostgREST embedding can
apply typed root filters before constructing nested responses; it is a useful
read baseline but does not automatically provide the document ETag contract.

## Required qualification matrix

Retain actual EXPLAIN plans, buffers, temporary blocks, WAL records/bytes and
client latency. Include unrelated roots and tenant skew, not only a five-root
fixture. Exercise point reads, keyset pages, deep OFFSET, selective root
predicates, JSON predicates and JSON sorting. Report construction loops and
selected/returned root counts. Never extrapolate point-read results to lists.

For writes, include no-op, root-only, one-child and all-child changes, complete
inserts and omitted-default inserts. Vary scalar width and 0/1/10/100/1000
children; separately qualify larger supported limits. Verify exact keyed
results, unchanged-row update counts and unchanged TOAST values. Measure
committed transactions, WAL bytes, storage/TOAST growth, vacuum equilibrium
and same-root versus unrelated-root concurrency. Rollback-only EXPLAIN
measurements do not qualify committed throughput or maintenance demand.

## PostgREST comparison

Separate three costs: database execution, raw private PostgREST transport,
and the supported PostgREST plus public boundary stack. The fair service
comparison is a handwritten guarded RPC versus generated document operations
with identical RLS, validation, ETag checks, locking, atomicity and response
shape. Direct table CRUD is a lower-contract reference, not equal functionality.

Pin candidate SHA, PostgreSQL/PostgREST versions, pool size, acquisition timeout,
statement/lock timeout, ingress/boundary policy, payload bytes and connection
reuse. Require pool wait + SQL budget + delivery margin below the upstream
deadline. Do not rely on equal ten-second pool and proxy defaults.

Run disjoint-root and hot-root cases, closed-loop concurrency sweeps and
open-loop arrival-rate/overload/recovery tests. Retain every outcome, including
503, 502, conflicts, retries and timeouts. Report successful logical operations
per second, offered rate, p50/p95/p99/max and uncertainty across independent
repetitions. A failed request is not a fast successful operation. Measure full
JSON delivery and COMMIT; `pg_column_size(data)` does neither.

Qualification is specific to the tested contract and workload. No current
production capacity or performance advantage is implied by this procedure.
