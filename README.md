# pg_jsontailor

`pg_jsontailor` generates writable JSON documents over relational PostgreSQL tables. From a compact JSON specification it creates a view, guarded write paths and optimistic concurrency controls, while the source of truth remains in ordinary PostgreSQL tables.

It is useful for APIs and integration layers where applications need document-shaped SQL or REST access without replacing the relational model. A generated view can be exposed through PostgREST and supports optimistic concurrency through document ETags.

The design is inspired by the general duality-view model: one logical JSON
document can be read from and written back to normalised relational tables.
`pg_jsontailor` is an independent, focused PostgreSQL product and makes no
Oracle JSON Relational Duality View or ORDS compatibility claim.
Oracle, ORDS, PostgreSQL, PostgREST and other names belong to their owners and
are used only descriptively; no affiliation or endorsement is implied.

## Who it is for

For developers and operators evaluating a small document API over existing
relational tables, with reviewed database privileges and a controlled endpoint.
It is not a general document database, a partial-JSON-update API or a qualified
high-contention write-heavy service. Start in a disposable database; a customer
pilot requires the separate [pilot readiness](docs/PILOT_READINESS.md) process.

The current implementation supports:

- one root table;
- an optional read-only many-to-one object: INSERT ignores edited non-key object
  fields and reads return the referenced values; replacement checks them when
  `object.etag=true` and otherwise ignores them (see [object semantics](docs/API.md#errors));
- an optional writable one-to-many array;
- document reads, inserts, full replacements and guarded deletes;
- optimistic concurrency using the first 128 bits of SHA-256 as 32 uppercase hexadecimal ETags (not authentication tokens);
- PostgreSQL row-level security and invoker rights;
- generator provenance, catalogue health checks and transactional regeneration;
- generated DDL that has no runtime dependency on the extension.

## Runtime requirements

- tested PostgreSQL 17 and 18 (released), and pinned PostgreSQL 19 pre-release until GA;
- permission to place the extension control and SQL files in the PostgreSQL extension directory.

`pg_jsontailor` is implemented entirely in SQL and PL/pgSQL. It does not compile or install a shared library.

## Install

The supplied PGXS Makefile provides the conventional PostgreSQL source-installation path:

```sh
make install
```

For this installation method, GNU Make, PGXS and the matching PostgreSQL server development package are required only to locate the correct installation directories and copy the files. They do not compile product code.

Packagers may instead install these two files directly into PostgreSQL's `share/extension` directory, and must include `LICENSE` with their package:

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

After the files are installed, a superuser enables the extension in a database:

```sql
CREATE EXTENSION pg_jsontailor;
```

A third route is available when the target service does not allow
`CREATE EXTENSION`: generate and verify a protected standalone bundle on the
same PostgreSQL major, then apply it as the target database owner. Follow
[cookbook recipe 8](docs/cookbook/08-no-extension.md). No managed provider is
claimed as qualified by that generic procedure.

## Quick start

### Before you start

- Use `READ COMMITTED` for writes; other isolation levels are rejected.
- Create the required child index beginning with `(parent_fk, order_by, key)`.
- Keep the generated view name within 46 bytes so helper names fit PostgreSQL's
  63-byte identifier limit.
- Security-invoker writes require source-table privileges. Those privileges can
  bypass document ETags through direct SQL, so expose the role only through the
  reviewed endpoint and enforce the same RLS tenant boundary on every source.

Warning: use a disposable database only. The fixture
[`examples/shop_schema.sql`](examples/shop_schema.sql) executes `DROP SCHEMA shop CASCADE`
and destroys existing data and dependent objects. Never load it into an application database.
The following shell walkthrough runs from the repository root, creates its own
database and removes that database on exit. The connected role needs `CREATEDB`
and permission to install the extension; install the extension files first.
Connection settings (`PGHOST`, `PGPORT`, `PGUSER` and optionally the maintenance
`PGDATABASE`) follow the usual libpq environment variables.

The specification maps relational roles to the JSON shape:

```json
{
  "root":{"table":"shop.orders","key":"order_id",
          "fields":{"status":"status","created":"created_at"},
          "insert":true,"update":true,"delete":true},
  "object":{"name":"customer","table":"shop.customers",
            "key":"customer_id","root_fk":"customer_id",
            "fields":{"customerId":"customer_id","name":"name"},"etag":false},
  "array":{"name":"items","table":"shop.order_items","key":"item_id",
           "parent_fk":"order_id","order_by":"item_id",
           "fields":{"itemId":"item_id","product":"product",
                     "qty":"quantity","price":"unit_price"},
           "insert":true,"update":true,"delete":true}
}
```

The shipped fixture and specification produce this actual document:

<!-- real-document:start -->
```json
{
  "_id":42,
  "items":[
    {"qty":2,"price":99.00,"itemId":1,"product":"Kabel"},
    {"qty":10,"price":4.50,"itemId":2,"product":"Konektor"}
  ],
  "status":"new",
  "created":"2026-11-03",
  "customer":{"name":"ACME","customerId":7},
  "_metadata":{"etag":"70AF45CCE541C9A5C8B598F2DA5C2CBC"}
}
```
<!-- real-document:end -->

<!-- quickstart-smoke:start -->
```sh
set -euo pipefail
quickstart_db="${PGJT_QUICKSTART_DB:-pgjt_quickstart_$$}"
createdb --maintenance-db="${PGDATABASE:-postgres}" "$quickstart_db"
trap 'dropdb --maintenance-db="${PGDATABASE:-postgres}" "$quickstart_db"' EXIT
psql -X -v ON_ERROR_STOP=1 -d "$quickstart_db" \
  -v spec="$(tr -d '\n' < examples/orders_dv.spec.json)" \
  -f examples/quickstart.sql
if [ "${KEEP_PGJT_QUICKSTART:-0}" = 1 ]; then
  trap - EXIT
  printf 'quickstart/retained=%s\n' "$quickstart_db"
else
  dropdb --maintenance-db="${PGDATABASE:-postgres}" "$quickstart_db"
  trap - EXIT
  echo quickstart/cleanup=PASS
fi
```
<!-- quickstart-smoke:end -->

Expected markers are `quickstart/read=new`, `quickstart/update=PASS`,
`quickstart/delete=PASS` and `quickstart/cleanup=PASS`. The
[`walkthrough`](examples/quickstart.sql) loads the complete shipped mapping,
inserts an order and child, reads its ETag, performs a guarded replacement and
deletes the inserted order and its child using the current ETag. It leaves the
seeded order untouched before the database is removed. `psql` variables use
literal quoting (`:'spec'`); application clients should bind query parameters.

The default path prints `quickstart/cleanup=PASS` and removes the database. To
continue directly with cookbook recipes 5 and 7, invoke the block with
`PGJT_QUICKSTART_DB=pgjt_cookbook KEEP_PGJT_QUICKSTART=1`; it instead prints
`quickstart/retained=pgjt_cookbook`. Complete both recipes against that retained
database, then remove it with `dropdb pgjt_cookbook`.

## What gets created

For the complete shipped `api.orders_dv` mapping:

- Four views: `api.orders_dv`, `api.orders_dv__root_binding`,
  `api.orders_dv__object_binding`, `api.orders_dv__array_binding`.
- Seven functions: `api.orders_dv__insert()`, `api.orders_dv__update()`,
  `api.orders_dv__delete()`, `api.orders_dv__guard()`, `api.orders_dv__check()`,
  `api.orders_dv__bounded_array(bigint)`, `api.orders_dv__delete_document(bigint,text)`.
- Six triggers on `api.orders_dv`: `orders_dv__insert`, `orders_dv__update`,
  `orders_dv__delete`, `orders_dv__guard_insert`, `orders_dv__guard_update`,
  `orders_dv__guard_delete`.
- Seven helper ACL-revocation blocks (owner-only execution), and the
  `pg_jsontailor metadata v1: ` view comment with the specification and provenance.

To save exact UTF-8 DDL bytes, use a disposable `generator` database containing
the extension and matching source schema. Run from the repository root:

<!-- ddl-review-smoke:start -->
```sh
set -euo pipefail
umask 077
spec="$(tr -d '\n' < examples/orders_dv.spec.json)"
printf '%s\n' "SELECT encode(convert_to(jsontailor.duality_view_ddl('api.orders_dv', :'spec'::jsonb),'UTF8'),'hex');" | \
  psql -XAt -v ON_ERROR_STOP=1 -v spec="$spec" -d generator | \
  python3 -c 'import sys;sys.stdout.buffer.write(bytes.fromhex(sys.stdin.read()))' \
  > generated.raw.sql
sha256sum generated.raw.sql
```
<!-- ddl-review-smoke:end -->

See the [generated DDL outline](docs/cookbook/generated-ddl-outline.md), including
the real public view definition, and [recipe 3](docs/cookbook/03-review-ddl.md)
for approval and private evidence retention. Raw SQL is inspection-only.

`create_duality_view()` executes immediately, returns void and displays no DDL.
It regenerates the output of `duality_view_ddl()` rather than reading a saved file.
The complete mapping creates four views, seven functions and six triggers.
Use [the DDL review recipe](docs/cookbook/03-review-ddl.md) for unpadded output
and save/hash/approve that exact SQL before extension-mode creation. For a
no-extension target, verify and deploy the protected standalone exporter bundle.
Do not execute the raw review file as the deployment procedure.

In applications, pass the specification as a query parameter rather than reading a server-side file. Both the target view and every source table must be schema-qualified.

For copyable point-read, ETag replacement, RPC/direct delete and MERGE shapes,
see [`docs/SQL_SHAPES.md`](docs/SQL_SHAPES.md). They are application examples,
not additional quickstart steps after the disposable database is removed.

The complete example specification is in [`examples/orders_dv.spec.json`](examples/orders_dv.spec.json). Start with the [cookbook](docs/cookbook/README.md); the [documentation map](docs/README.md) separates user and maintainer material. The detailed contract is in [`docs/API.md`](docs/API.md), [`docs/SEMANTICS.md`](docs/SEMANTICS.md), [`docs/RESOURCE_LIMITS.md`](docs/RESOURCE_LIMITS.md), [`docs/HELPER_ACL.md`](docs/HELPER_ACL.md), [`docs/HTTP_BOUNDARY.md`](docs/HTTP_BOUNDARY.md) and [`docs/PERFORMANCE_QUALIFICATION.md`](docs/PERFORMANCE_QUALIFICATION.md).

Generated functions contain copied write logic. Follow [`docs/UPGRADING.md`](docs/UPGRADING.md) to regenerate existing mappings transactionally after relevant extension or source-schema changes.

Before a controlled deployment, complete the fit, acceptance, SLO and release
checks in [`docs/PILOT_READINESS.md`](docs/PILOT_READINESS.md).

The tested PostgreSQL 17/18 released builds and pinned PostgreSQL 19 pre-release build can also route a documented subset of native SQL `MERGE` through the generated insert, update and delete paths. The source key must be unique, updates remain complete ETag-checked replacements, deletes require an ETag action condition, and the `WHEN NOT MATCHED` branch must be used only when creation is intended. See [`docs/MERGE.md`](docs/MERGE.md) for the supported subset and copyable staging examples.

## Status

Release metadata: `0.2.0` / `development`.

Version 0.2.0 is unreleased. Baremon-led customer pilots are offered only under
separately signed written pilot terms, on a best-effort basis and without warranty.
These pilot-service conditions do not restrict rights granted by LICENSE.
PostgreSQL 17 and 18 are tested released majors; PostgreSQL 19 is tested only on
the pinned pre-release build until general availability.
A pilot needs a fixed source version, passing functional/security checks and
the customer's written acceptance.
Passing tests do not establish production readiness or a support commitment.

## Testing

For maintainers, development tools are PostgreSQL server development files and
PGXS, GNU Make, Python 3, and PostgREST 16.5 for the reference REST scenarios.
Run regression and isolation tests against a disposable test instance:

```sh
make installcheck
```

See [`docs/TESTING.md`](docs/TESTING.md) for the suite inventory, PostgreSQL
matrix, PostgREST schedule and local safety notes.

## Performance

The write path uses set-based array reconciliation and set-wise validation.
Cost grows with the mapped child collection: a root-only change still locks,
validates and reconciles that collection. High-contention write-heavy
workloads are unqualified. Final exact-SHA measurements on PostgreSQL 17, 18
and the pinned 19 pre-release build are pending final source approval, and no current figures are published.
The benchmark compares the candidate with lower-contract direct SQL using
signed installation evidence, semantic assertions and three independently
restarted paired-sample sessions. See [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

## Limitations

- The JSON shape is intentionally limited to one root, one optional object and one optional array.
- Updates replace the complete document; JSON Patch is not provided.
- The document ETag covers the configured relational state, so large child collections still require linear read, lock and hash work.
- Concurrent throughput, network latency and client-specific access policies must be measured for each deployment.
- PostgREST configuration, authentication, TLS and operational controls remain deployment responsibilities.
- Public REST deployments must use the tested boundary proxy; raw PostgREST
  must not be externally reachable.

The complete mapped-type, foreign-key, identity and source-shape limits are in
[`docs/SEMANTICS.md`](docs/SEMANTICS.md).

## Licence

`pg_jsontailor` is released under the PostgreSQL License. See [`LICENSE`](LICENSE).

Exported generated SQL contains copied implementation logic. Standalone
copies must retain the complete copyright, permission and disclaimer
text in [`GENERATED_SQL_NOTICE`](GENERATED_SQL_NOTICE). See the
[`standalone deployment example`](examples/standalone/README.md) and bundle
verifier; this does not claim rights over user-authored data or specifications,
or imply a warranty, support commitment or SLA. Keep the whole bundle, including
the complete notice, for deployment, contractor handover and backups. The notice
lives in the SQL script, not in the generated database objects.

Copyright © 2026 Baremon s.r.o.

For support and contribution guidance, see [`SUPPORT.md`](SUPPORT.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
