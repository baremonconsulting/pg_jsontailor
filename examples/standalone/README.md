# Standalone deployment

Generated SQL contains copied implementation logic under the PostgreSQL Licence.
Redistribute it with the complete [GENERATED_SQL_NOTICE](../../GENERATED_SQL_NOTICE),
including the copyright, permission and disclaimer paragraphs verbatim. Do not
apply British-English spelling replacements to legal text. This notice does
not claim rights over your data or specification and provides no warranty,
support commitment or SLA; see [SUPPORT.md](../../SUPPORT.md).

Use `tools/export_standalone.py` against a trusted generator database containing
the candidate extension and source tables. The target view must not exist.
For example, after loading the disposable shop fixture and creating `api` in
that generator database:

```sh
python3 tools/export_standalone.py --psql /path/to/psql \
  --database generator_database --view api.orders_dv \
  --spec examples/orders_dv.spec.json --output /new/path/orders-bundle
python3 tools/verify_generated_sql_notice.py --bundle /new/path/orders-bundle
```

The output directory must not already exist. Copy the whole bundle, not just
`generated.sql`. On a clean target, create the same reviewed source tables and
API schema, then run `psql -X -v ON_ERROR_STOP=1 --single-transaction -d target
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
