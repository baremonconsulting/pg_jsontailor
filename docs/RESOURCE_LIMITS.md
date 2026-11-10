# Resource limits and pilot qualification

Every generated specification has these defaults:

```json
{"limits":{"request_bytes":1048576,"json_depth":32,
           "supplied_children":10000,"stored_children":10000}}
```

Limits are integer values. `request_bytes` permits 1,024–16,777,216;
`json_depth` permits 1–128; both child limits permit 0–100,000. Unknown limit
keys, non-integers and out-of-range values fail with `22023`. Set limits in the
source-controlled specification and regenerate transactionally when changing
them. Regeneration does not repair oversized source data.

HTTP request bytes include the entire UTF-8 request body, including any
PostgREST envelope. The proxy rejects an excessive declared length before
reading or parsing the body, and bounds streamed reads by that declared length.
It scans container depth before JSON decoding, ignoring brackets inside strings.
JSON root containers have depth one. The proxy's `--max-request-bytes` and
`--max-json-depth` use the same defaults and bounds. Configure the ingress and
proxy to at most the limits agreed for all routes they serve. SQL write helpers
also bound canonical UTF-8 `jsonb::text` document bytes and container depth:
that check is necessarily after PostgreSQL JSON parsing, and canonical byte
counts can differ from the wire representation. Direct SQL does not have a
pre-parse HTTP byte guarantee.

The final materialised insert/replacement document must also fit
`request_bytes`, including generated defaults, identifiers and `_metadata`.
If expansion exceeds that budget, the complete statement rolls back with
`PT413`. Leave space for the materialised output rather than treating the
input byte ceiling as available business-payload space.

`supplied_children` bounds the submitted array before element validation or
reconciliation. `stored_children` bounds the RLS-visible rows attached to each
root. A `LIMIT N+1` count precedes aggregation through an invoker cardinality
gate in a `CASE` expression; excessive data raises `PT413`, never truncates.
The count and aggregation share the statement snapshot and the same source/RLS
predicate. A post-root-lock refresh repeats the preflight before child row
locking. Concurrent direct-source growth is either blocked by the enforced FK's
root-key lock or detected by the fresh snapshot; no hidden rows are counted by
escalating privileges. Direct SQL can still create excessive source data; an
operator must repair it through source-table operations before document access
can resume. Reading just `_id` need not evaluate the document projection.

Resource violations use SQLSTATE `PT413` and HTTP 413; malformed JSON/framing
uses safe HTTP 400; unsupported body media uses 415; invalid configuration uses
`22023`; a failed index health check uses `PT422`. Existing shape, ETag and
native constraint SQLSTATEs retain their previous meanings.

## Runtime read capability migration

An array document now needs an explicit EXECUTE grant on its owner-only
`<view>__bounded_array(bigint)` cardinality gate. It returns a boolean or raises
an error; it cannot read or write source data itself. As the view owner:

```sql
SELECT jsontailor.grant_array_reader('api.orders_dv','application_role',false);
```

This records the same role/owner OID and grant-option provenance as the delete
RPC review API. Regeneration retains only matching existing reviewed grants.
For standalone generated SQL use an explicit reviewed `GRANT EXECUTE ON
FUNCTION api.orders_dv__bounded_array(bigint) TO application_role` after running
the DDL. Keep source SELECT/RLS and view SELECT grants unchanged. Trigger,
guard and check helpers remain owner-only; do not grant all functions.

## Index and execution-plan acceptance

`__check()` and inventory health require a valid, ready, live, non-partial,
non-expression B-tree whose leading columns are `(parent_fk, order_by, key)`;
when order is the parent FK or the key, use `(parent_fk, key)`. The check visits
every partitioned table and descendant partition, not just the parent. Creating
an index on the partitioned parent normally creates the required leaf indexes.
Missing/invalid indexes fail health checks; they do not silently create indexes
or escalate privileges. Ordinary reads still enforce cardinality even if a
deployment has ignored index health, but a scan-time bound is then not promised.

Before pilot GO, run `EXPLAIN (ANALYSE, BUFFERS, FORMAT JSON)` on the exact
preflight predicate with representative selective and hot-root data. Require an
index-eligible parent predicate, a `Limit` returning at most N+1 rows, no full
child aggregation before the gate, and customer-approved buffer/latency bounds.
RLS policy functions must also be reviewed for unbounded scans. Merely having
an index cannot force the optimiser to choose it.

## Operational defaults and deployment GO

Start with a 5-second `statement_timeout`, a 1-second `lock_timeout`, a
10-second proxy upstream timeout, at most 10 PostgREST pool connections and 16
proxy connections. The proxy supports `--max-connections` 1–256, `--timeout`
1–120 seconds and `--max-response-bytes` 1,024–67,108,864 (default 16 MiB).
An exhausted proxy cap returns safe HTTP 503. Apply authenticated per-tenant
quotas and initially 10 requests/second with a burst of 20 at the TLS ingress;
these are tuning starting points, not built-in per-tenant rate enforcement.

Retry a deadlock (`40P01`) or serialisation failure (`40001`) as a complete
transaction after re-reading the document and obtaining a fresh ETag; use
bounded exponential backoff with jitter and a finite retry budget. Never retry
a resource violation blindly. Test hot roots, shared object locks, stale ETags
and full rollback through the actual boundary.

Partition routing, omitted/default-preserving fields and deferrable child keys
must be measured separately from the ordinary fully supplied set-based path:
their existing fallback/subtransaction costs have not become constant-time.
Run the partition, set-based reconciliation and subtransaction suites alongside
the concurrent API campaign. A passing synthetic run is not a customer SLO.
Customer-specific p95/SLO and concurrency acceptance, backup/restore rehearsal,
named operational/withdrawal owners, protected diagnostic retention and an
approved rollback procedure are mandatory deployment GO conditions.
