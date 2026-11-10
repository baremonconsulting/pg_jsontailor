# pg_jsontailor cookbook

## Reading order and databases

Start with the [README quick start](../../README.md#quick-start). For the
required README → recipe 5 → recipe 7 path, set
`PGJT_QUICKSTART_DB=pgjt_cookbook KEEP_PGJT_QUICKSTART=1` so the wrapper retains
the fixture; then provision the role in recipe 5 and run recipe 7 as that role.
Without that explicit setting the README removes its database. Recipe 1 is an
alternative retained-fixture entry. Run commands from the repository root.

| Recipe | Database and session |
|---|---|
| [1. Try it](01-try-it.md) | Creates and retains disposable `pgjt_cookbook`, owner session |
| [2. Map your tables](02-map-tables.md) | `pgjt_cookbook`, owner; independent schema rolled back |
| [3. Review DDL](03-review-ddl.md) | Separate `generator` with extension and matching source schema |
| [4. Create and verify](04-create-and-verify.md) | Fresh `generator`, empty API schema; alternative to recipe 1's existing mapping |
| [5. Application role](05-application-role.md) | Retained `pgjt_cookbook`, owner provisions role/grants |
| [6. Read and filter](06-read-list-filter.md) | `pgjt_cookbook`, `SET ROLE app_role` |
| [7. ETag writes](07-write-etags.md) | Fresh `psql -X -v ON_ERROR_STOP=1 -d pgjt_cookbook`; `SET ROLE app_role`; rehearsal rolls back |
| [8. No-extension deployment](08-no-extension.md) | Separate same-major `generator` and extension-free `target`, consultant-led |
| [9. Removal](09-remove-and-rollback.md) | Owner of the selected deployment; destructive to generated objects, run only when removal is intended |
| [10. PostgREST proxy](10-postgrest-proxy.md) | Dedicated disposable REST fixture for probes; reviewed actual deployment for network acceptance |
| [11. Schema changes](11-schema-change.md) | `pgjt_cookbook`, owner, no writers; rolls back |
| [12. MERGE](12-merge.md) | `pgjt_cookbook`, application role; rolls back |
| [13. Limits](13-limits.md) | `pgjt_cookbook`, application role; rolls back |
| [14. Pilot acceptance](14-pilot-acceptance.md) | Separate disposable synthetic pilot/restore databases per PostgreSQL build |
| [15. RLS](15-rls.md) | Disposable extension-enabled database, role-creating owner; its own schema/roles roll back |

Recipes 3–4 are not sequential creation commands for recipe 1's existing view.
Prepare their separate generator once: `createdb generator`, enable the installed
extension, load only the synthetic `examples/shop_schema.sql`, and create `api`;
do not load customer material before the [pilot agreement](../PILOT_READINESS.md).
See [generated DDL outline](generated-ddl-outline.md) and
[troubleshooting](troubleshooting.md). Operational rehearsals do not grant customer GO.

## Independent reader trial

Before treating the entry path as accepted, a reader who did not author it must
complete README quick start, recipe 5 and recipe 7 without outside help. Record
date, source SHA, reader, environment, commands/results and any assistance in
PGJT-144 or a linked private record. The current entry path passed an
independent PostgreSQL 18.6 reader trial; its commands, snapshot hashes, initial
failed path and successful stable-snapshot rerun are recorded in PGJT-144. This
does not replace exact-SHA CI or customer acceptance, and an author's smoke
test is not the independent trial.

