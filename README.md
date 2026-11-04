# pg_jsontailor

`pg_jsontailor` is a PostgreSQL extension for exposing relational data as writable JSON documents. It generates a view and `INSTEAD OF` triggers from a compact JSON specification, while keeping the underlying data in ordinary PostgreSQL tables.

The project is aimed at migrations and integrations where applications need document-shaped SQL or REST access without replacing the relational model. A generated view can be exposed through PostgREST and supports optimistic concurrency through document ETags.

## Status

Version 0.2.0 is suitable for evaluation and controlled pilots on PostgreSQL 17, 18 and 19. PostgreSQL 19 is tested against the current release candidate until its final release is available. The extension has passed the supplied regression, isolation, row-level security, hostile-identifier, PostgREST and Oracle ORDS comparison scenarios. It is not yet presented as a production-ready compatibility layer for unrestricted workloads.

The current implementation supports:

- one root table;
- an optional read-only many-to-one object;
- an optional writable one-to-many array;
- document reads, inserts, full replacements and deletes;
- optimistic concurrency using SHA-256 ETags;
- PostgreSQL row-level security and invoker rights;
- generated DDL that has no runtime dependency on the extension.

## Requirements

- PostgreSQL 17, 18 or 19;
- PostgreSQL server development files and PGXS;
- GNU Make;
- Python 3 for the extended test and benchmark tools;
- PostgREST 16.5 only for the optional REST scenarios.

## Build and install

```sh
make
make install
```

Run the extension regression and isolation suites against an available test instance:

```sh
make installcheck
```

Create the extension in the target database:

```sql
CREATE EXTENSION pg_jsontailor;
```

## Quick start

Given ordinary relational tables such as those in [`examples/shop_schema.sql`](examples/shop_schema.sql), generate a writable JSON view from a specification:

```sql
SELECT jsontailor.create_duality_view(
    'api.orders_dv',
    '{
      "root": {
        "table": "shop.orders",
        "key": "order_id",
        "fields": {"status": "status", "created": "created_at"},
        "insert": true,
        "update": true,
        "delete": true
      }
    }'::jsonb
);
```

The same DDL can be inspected before execution:

```sql
SELECT jsontailor.duality_view_ddl(
    'api.orders_dv',
    '{
      "root": {
        "table": "shop.orders",
        "key": "order_id",
        "fields": {"status": "status", "created": "created_at"},
        "insert": true,
        "update": true,
        "delete": true
      }
    }'::jsonb
);
```

In applications, pass the specification as a query parameter rather than reading a server-side file. Both the target view and every source table must be schema-qualified.

Read a document:

```sql
SELECT data
FROM api.orders_dv
WHERE _id = 1001;
```

For updates and deletes, return the `_metadata.etag` value from the preceding read. A stale ETag is rejected atomically.

The complete example specification is in [`examples/orders_dv.spec.json`](examples/orders_dv.spec.json). The detailed API and document rules are described in [`docs/API.md`](docs/API.md) and [`docs/SEMANTICS.md`](docs/SEMANTICS.md).

## Testing

The repository contains several layers of verification:

- `make installcheck` for PGXS regression and isolation tests;
- `tests/core_operations.py` for the core document operations;
- `tests/validation_security.py` for validation, security, RLS and edge cases;
- `tests/run_postgrest.py` for the optional REST surface;
- `tests/setbased_reconciliation.py` for bulk array reconciliation;
- `pilot/synthetic/run_pilot.py` for an isolated synthetic rehearsal;
- `bench/duality_overhead.py` for reproducible server-side measurements.

The extended tools create disposable schemas or databases. Review their connection settings before running them outside an isolated development environment.

Every push and pull request is tested on PostgreSQL 17, 18 and 19 by GitHub Actions. The PostgreSQL 19 job follows the current release candidate until the final packages are published. CodeQL and dependency review run separately with read-only default permissions.

## Performance

The write path uses set-based array reconciliation. In the reference single-session benchmark, replacing a document with 1,000 child items improved from approximately 3.49 seconds in the earlier implementation to 102 milliseconds. Results are host-specific and do not represent a production SLO. See [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

## Limitations

- The JSON shape is intentionally limited to one root, one optional object and one optional array.
- Updates replace the complete document; JSON Patch is not provided.
- The document ETag covers the configured relational state, so large child collections still require linear read, lock and hash work.
- Concurrent throughput, network latency and client-specific access policies must be measured for each deployment.
- PostgREST configuration, authentication, TLS and operational controls remain deployment responsibilities.

## Licence

`pg_jsontailor` is released under the PostgreSQL Licence. See [`LICENSE`](LICENSE).

Copyright © 2026 Baremon Consulting.

For support and contribution guidance, see [`SUPPORT.md`](SUPPORT.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
