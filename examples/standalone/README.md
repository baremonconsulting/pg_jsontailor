# Standalone deployment

For the complete lifecycle, prerequisites, expected results and rollback, start
with [cookbook recipe 8](../../docs/cookbook/08-no-extension.md).

Use `tools/export_standalone.py` against a trusted generator database containing
the candidate extension and matching source tables. The view must not exist in
the target database before application; it may exist in the generator database
for inspection. For example:

```sh
python3 tools/export_standalone.py --psql /path/to/psql \
  --database generator_database --view api.orders_dv \
  --spec examples/orders_dv.spec.json --output /new/path/orders-bundle
python3 tools/verify_generated_sql_notice.py --bundle /new/path/orders-bundle
```

The output directory must not already exist. Copy the whole bundle, not just
`generated.sql`. On a clean target, create the same reviewed source tables and
API schema, then run `psql -X -v ON_ERROR_STOP=1 -d target
-f /new/path/orders-bundle/generated.sql`. Source tables, roles, RLS policies
and reviewed privileges remain deployment responsibilities. Neither the
extension nor the `jsontailor` schema is required at runtime. Inventory and
regeneration are generator operations, so use the generator database for those;
the generated `__check()` health function remains available on the target.

Standalone DDL and direct SQL do not sanitise PostgreSQL diagnostics for an
untrusted HTTP client. Any public HTTP deployment needs a separately verified
response boundary; a raw database or PostgREST error is not a safe public response.

`tests/standalone_generated_sql.py` exports the candidate and checks the clean
database, operations, privileges, RLS and both dump formats on each supported
PostgreSQL major. It records stable result IDs; a partial or missing result set
is not acceptance evidence.

When running that harness from an extracted source archive rather than a Git
checkout, supply `--source-commit <full-SHA> --source-archive /path/source.tar.gz
--archive-sha256 <approved-checksum>`. The SHA/checksum must come from the
independently verified rehearsal/release manifest, not from an untrusted
archive's contents. The harness checks the archive checksum and every extracted
source file's identity, content and executable bit; missing, extra or changed
source files fail. Keep result files outside the extracted tree. Provenance is
labelled `archive-declared`, not Git-derived; these integrity checks do not
themselves confer release authority or authenticate the declaring actor.

The bundle embeds the complete `GENERATED_SQL_NOTICE`, a major-version guard,
a fixed search path and its own transaction. Retain its copyright, permission
and disclaimer text. It gives no warranty, support commitment or SLA.
Keep the complete notice with every copy of `generated.sql`, and hand over the
whole bundle for deployment, contractor access and backups. The notice lives
in the script, not in generated database objects; a database backup alone does
not preserve the script notice. Packagers include `LICENSE` with their package.
Because the bundle owns its `BEGIN` and `COMMIT`, do not use it to enclose other
migration files in one larger transaction.
