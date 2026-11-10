# Synthetic pre-pilot acceptance plan

This rehearsal exercises the currently supported thin slice: one order root, one read-only customer object, and one array of order items. It does not claim compatibility with an unknown client schema.

## Acceptance scenarios

1. Read back five seeded documents and compare semantic JSON after excluding generated `_metadata`.
2. Filter by status and apply paging.
3. Insert a document without root or child identifiers and receive generated identifiers.
4. Replace a complete document and verify the relational table state.
5. Let two clients write from the same ETag; the first must succeed and the second must receive 412.
6. Reject a missing ETag with 428 and incomplete/unknown documents with 422.
7. Keep base tables out of the REST surface while allowing the generated duality view.
8. Keep internal per-role binding views inaccessible through the REST surface.
9. Delete a document and its children atomically.
10. Complete 20 local operational reads without errors; timings are diagnostic only, not a benchmark.
11. Restore the pre-pilot snapshot into a fresh database and recover the root,
    child and document counts derived from the fixture. The current orders
    fixture contains five roots and ten children, so its expected result is
    `5:10:5`; this is verified against the files, not assumed by the runner.
12. Exercise the separate mixed-type fixture whose JSONB field order starts
    with numeric, boolean and timestamp-with-time-zone fields: both multi-row
    transaction orders and HTTP INSERT/replacement through the proxy.
13. Reject direct view DELETE, unfiltered PATCH and duplicate identity filters
    through the proxy with safe errors and unchanged source rows.

The runner creates the required child index, grants the array-reader and
delete-RPC capabilities, and requires both generated health checks to return
`OK`. PostgREST has a private Unix socket; requests use the boundary proxy.
The broad SQL grants are an isolated test fixture, not a deployment template.

The decision is `GO` only if every check passes. A GO is technical evidence
from synthetic data only. It does not authorise use of client data, a customer
pilot, production deployment or a release.

## Running the rehearsal

The runner accepts explicit PostgreSQL, PostgREST, network, database and output settings and otherwise respects `PGHOST`, `PGPORT`, `PGUSER`, `PG_JSONTAILOR_PG_BIN` and `PG_JSONTAILOR_POSTGREST`:

```sh
python3 pilot/synthetic/run_pilot.py \
  --pg-bin /usr/lib/postgresql/18/bin \
  --pg-host /var/run/postgresql \
  --pg-port 5432 \
  --pg-user "$(id -un)" \
  --postgrest /usr/local/bin/postgrest \
  --database jsontailor_synthetic_pilot \
  --restore-database jsontailor_synthetic_pilot_restore
```

Both database names must identify new, dedicated disposable databases. Existing
databases are refused without being removed. The runner removes only the
databases and unique roles it created, stops its HTTP services, and retains the
snapshot and diagnostic files. Use a private test cluster; no customer material
is accepted. Use `--postgrest-db-host`, `--rest-host`, `--rest-port` and
`--admin-port` when the services do not use their local defaults.

Pass a fresh `--output` directory for evidence. The fixed fourteen-result JSONL
file is `synthetic-pilot-results.jsonl` inside it, or a fresh path supplied with
`--result`. A failed scenario still emits the complete inventory with unfinished
checks marked `FAIL`; a cleanup failure also prevents `GO`. Upload only the
reviewed result file and summary, not the private `raw/` diagnostics.

Database-free runner regressions:

```sh
python3 pilot/synthetic/test_runner.py
```
