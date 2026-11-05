# pg_jsontailor

`pg_jsontailor` generates writable JSON documents over relational PostgreSQL tables. From a compact JSON specification it creates a view, guarded write paths and optimistic concurrency controls, while the source of truth remains in ordinary PostgreSQL tables.

It is useful for APIs, integration layers and migrations where applications need document-shaped SQL or REST access without replacing the relational model. A generated view can be exposed through PostgREST and supports optimistic concurrency through document ETags.

The design follows the same core idea as Oracle JSON Relational Duality Views: one logical JSON document can be read from and written back to normalised relational tables. `pg_jsontailor` provides a focused PostgreSQL implementation rather than a drop-in implementation of every Oracle feature or ORDS interface.

## Status

The unreleased 0.2.0 development version is suitable for evaluation and controlled pilots on PostgreSQL 17, 18 and 19. PostgreSQL 19 is tested against the current release candidate until its final release is available. The extension has passed the supplied regression, isolation, row-level security, hostile-identifier and PostgREST scenarios. It is not yet presented as a production-ready compatibility layer for unrestricted workloads.

The current implementation supports:

- one root table;
- an optional read-only many-to-one object;
- an optional writable one-to-many array;
- document reads, inserts, full replacements and guarded deletes;
- optimistic concurrency using SHA-256 ETags;
- PostgreSQL row-level security and invoker rights;
- generator provenance, catalogue health checks and transactional regeneration;
- generated DDL that has no runtime dependency on the extension.

## Runtime requirements

- PostgreSQL 17, 18 or 19;
- permission to place the extension control and SQL files in the PostgreSQL extension directory.

`pg_jsontailor` is implemented entirely in SQL and PL/pgSQL. It does not compile or install a shared library.

## Install

The supplied PGXS Makefile provides the conventional PostgreSQL source-installation path:

```sh
make install
```

For this installation method, GNU Make, PGXS and the matching PostgreSQL server development package are required only to locate the correct installation directories and copy the files. They do not compile product code.

Packagers may instead install these two files directly into PostgreSQL's `share/extension` directory:

```text
pg_jsontailor.control
sql/pg_jsontailor--0.2.0.sql
```

The SQL file must be installed under the name `pg_jsontailor--0.2.0.sql` next to the control file.

For example, an administrator can install them without PGXS or Make:

```sh
extension_dir="$(pg_config --sharedir)/extension"
install -m 0644 pg_jsontailor.control "$extension_dir/"
install -m 0644 sql/pg_jsontailor--0.2.0.sql "$extension_dir/"
```

After the files are installed, enable the extension in a database:

```sql
CREATE EXTENSION pg_jsontailor;
```

## Development requirements

The following tools are needed only for development, regression testing and optional integration scenarios:

- PostgreSQL server development files and PGXS;
- GNU Make;
- Python 3 for the extended test and benchmark tools;
- PostgREST 16.5 for the optional REST scenarios.

Run the extension regression and isolation suites against an available test instance:

```sh
make installcheck
```

## Quick start

Given ordinary relational tables such as those in [`examples/shop_schema.sql`](examples/shop_schema.sql), generate a writable JSON view from a specification:

```sql
CREATE SCHEMA api;

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
WHERE _id = 42;
```

Updates are complete-document replacements, but the client does not have to retain or transmit the complete JSON value. It can pass the identity, the ETag from an earlier read and the new field value; PostgreSQL can construct complete `NEW.data` inside the statement:

```sql
UPDATE api.orders_dv
SET data = jsonb_set(data, '{status}', to_jsonb($3::text))
WHERE _id = $1
  AND data #>> '{_metadata,etag}' = $2
RETURNING data;
```

Here `$1` is the root identity, `$2` is the ETag retained from the client's earlier read and `$3` is the new status. An ETag that is already stale selects no row and returns `UPDATE 0`. If the document changes after row selection while the trigger waits for locks, the post-lock check returns `PT412`. The value ultimately passed to the trigger must still be a complete valid document; it may be assembled from `data`, a correlated subquery or a materialised CTE in the same statement.

Delete through the generated function and pass the identifier and ETag explicitly:

```sql
SELECT api.orders_dv__delete_document(42, 'ETAG_FROM_THE_PRECEDING_READ');
```

SQL clients can also include both values in a direct delete:

```sql
DELETE FROM api.orders_dv
WHERE _id = 42
  AND data #>> '{_metadata,etag}' = 'ETAG_FROM_THE_PRECEDING_READ'
RETURNING _id;
```

The trigger locks the relational state, reads the document again and compares its current ETag with `OLD.data`. A concurrent change therefore returns `PT412`. An ETag that does not match during the initial `WHERE` evaluation selects no row and returns `DELETE 0`.

The complete example specification is in [`examples/orders_dv.spec.json`](examples/orders_dv.spec.json). The detailed API and document rules are described in [`docs/API.md`](docs/API.md) and [`docs/SEMANTICS.md`](docs/SEMANTICS.md).

Generated functions contain copied write logic. Follow [`docs/UPGRADING.md`](docs/UPGRADING.md) to regenerate existing mappings transactionally after relevant extension or source-schema changes.

Before a controlled deployment, complete the fit, acceptance, SLO and release
checks in [`docs/PILOT_READINESS.md`](docs/PILOT_READINESS.md).

PostgreSQL 17, 18 and 19 can also route a documented subset of native SQL `MERGE` through the generated insert, update and delete paths. The source key must be unique, updates remain complete ETag-checked replacements, deletes require an ETag action condition, and the `WHEN NOT MATCHED` branch must be used only when creation is intended. See [`docs/MERGE.md`](docs/MERGE.md) for the supported subset and copyable staging examples.

## Testing

The repository contains several layers of verification:

- `make installcheck` for PGXS regression and isolation tests;
- `tests/core_operations.py` for the core document operations;
- `tests/validation_security.py` for validation, security, RLS and edge cases;
- `tests/integrity_regressions.py` for generated write-path and integrity regressions;
- `tests/merge_operations.py` for native SQL MERGE compatibility and concurrency;
- `tests/run_postgrest.py` for the optional REST surface;
- `tests/setbased_reconciliation.py` for bulk array reconciliation;
- `tests/dump_restore.py` for custom and plain dump/restore verification;
- `pilot/synthetic/run_pilot.py` for an isolated synthetic rehearsal;
- `bench/duality_overhead.py` for reproducible server-side measurements.
- `bench/verify_results.py` for independent validation and recomputation of benchmark artifacts.

The extended tools create disposable schemas or databases. Review their connection settings before running them outside an isolated development environment.

Every push to `main` and every pull request targeting `main` runs the installation-contract, regression, isolation, functional, security, set-based reconciliation, native MERGE and dump/restore suites on PostgreSQL 17, 18 and 19. The PostgreSQL 19 job follows the current release candidate until the final packages are published. Repository checks also enforce the release-candidate version, benchmark and public-source contract. CodeQL and dependency review run separately with read-only default permissions. PostgREST, pilot and benchmark execution are explicit integration or measurement scenarios rather than per-commit CI gates; committed benchmark evidence is independently verified on every run. Retired tools for the removed merge-patch API are isolated under `archive/merge_patch/` and are not part of the supported product.

## Performance

The write path uses set-based array reconciliation and set-wise validation.
The current verified Azure reference at commit `11e6d74` records a 50.471 ms
median and 50.876 ms p95 for complete replacement of a document with 1,000
children. Results are host-specific and do not represent a production SLO. See
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) for the full semantic and provenance
context.

## Limitations

- The JSON shape is intentionally limited to one root, one optional object and one optional array.
- Updates replace the complete document; JSON Patch is not provided.
- The document ETag covers the configured relational state, so large child collections still require linear read, lock and hash work.
- Concurrent throughput, network latency and client-specific access policies must be measured for each deployment.
- PostgREST configuration, authentication, TLS and operational controls remain deployment responsibilities.

The complete mapped-type, foreign-key, identity and source-shape limits are in
[`docs/SEMANTICS.md`](docs/SEMANTICS.md).

## Licence

`pg_jsontailor` is released under the PostgreSQL Licence. See [`LICENSE`](LICENSE).

Copyright © 2026 Baremon s.r.o.

For support and contribution guidance, see [`SUPPORT.md`](SUPPORT.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
