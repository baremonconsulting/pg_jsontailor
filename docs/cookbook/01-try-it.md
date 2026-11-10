# 1. Try it and look around

## Goal

Run and inspect the shipped mapping in a disposable database.

## Prerequisites

PostgreSQL client tools, the installed extension and database-creation rights.

## Steps

Create a disposable database and run the SQL walkthrough without the README
shell wrapper, whose cleanup intentionally removes its database:

```sh
createdb pgjt_cookbook
psql -X -v ON_ERROR_STOP=1 -d pgjt_cookbook \
  -v spec="$(tr -d '\n' < examples/orders_dv.spec.json)" \
  -f examples/quickstart.sql
```

Expected markers: `quickstart/read=new`, `quickstart/update=PASS` and
`quickstart/delete=PASS`. Then inspect a real document:

```sql
SELECT jsonb_pretty(data) FROM api.orders_dv WHERE _id=42;
SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='api' AND c.relname LIKE 'orders_dv%';
```

Expected: one document and four views; the full mapping also creates seven
functions and six triggers. Remove the disposable database with
`dropdb pgjt_cookbook` when finished.

## Expected result, common errors and reference

Require all documented PASS markers and one readable document. Missing
extension/database rights stop the recipe. See the [API](../API.md).

