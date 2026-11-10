# 3. Review generated DDL

## Goal

Save and approve exact UTF-8 generator bytes before deployment.

## Prerequisites

A disposable generator database with matching extension, PostgreSQL major and
source schema; Bash, Python 3 and psql. Run from the repository root.
Keep SQL/schema evidence private, not public Actions logs.

## Steps

Raw `duality_view_ddl` output is for inspection. Hex transport removes psql's
record separator without stripping SQL bytes:

```sh
set -euo pipefail
umask 077
spec="$(tr -d '\n' < examples/orders_dv.spec.json)"
printf '%s\n' "SELECT encode(convert_to(jsontailor.duality_view_ddl('api.orders_dv', :'spec'::jsonb),'UTF8'),'hex');" | \
  psql -XAt -v ON_ERROR_STOP=1 -v spec="$spec" -d generator | \
  python3 -c 'import sys;sys.stdout.buffer.write(bytes.fromhex(sys.stdin.read()))' \
  > generated.raw.sql
test -s generated.raw.sql
sha256sum generated.raw.sql examples/orders_dv.spec.json > reviewed.sha256
git rev-parse HEAD > reviewed.commit
psql -XAt -v ON_ERROR_STOP=1 -d generator -c 'SELECT version();' > reviewed.postgresql
```

Review views, functions, triggers, ACL blocks and metadata. Record approver,
timestamp and schema/generator context in the deployment record. Hash exactly
the saved bytes, including any generator-owned final newline, not a trimmed
string or psql display framing.

Extension installed: deploy through `create_duality_view()` in recipe 04.
No-extension target: use `tools/export_standalone.py`, verify the bundle and
apply `generated.sql`. That bundle adds transaction, major guard, fixed search
path and notice; its hash differs from the raw payload hash.

## Expected result

Non-empty SQL, actual-byte checksum, specification checksum, commit,
PostgreSQL build and review record. See [DDL outline](generated-ddl-outline.md).

## Stop, common errors and recovery

Do not execute `generated.raw.sql` as deployment procedure. Any change to
specification, source schema or generator invalidates approval: regenerate and
review in a controlled DDL window. Creation does not consume the saved file
or verify its hash automatically.

## Evidence and reference

Retain files privately. Reviewed SQL, regenerated desired state and observed
deployed catalogue state are separate artifacts; none substitutes for another.

