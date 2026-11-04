# API and specification

`pg_jsontailor` exposes two functions in the `jsontailor` schema.

```sql
jsontailor.duality_view_ddl(view_name text, spec jsonb) RETURNS text
jsontailor.create_duality_view(view_name text, spec jsonb) RETURNS void
```

`duality_view_ddl` validates the specification against PostgreSQL catalogues and returns a complete DDL script. `create_duality_view` executes the same script in the caller's transaction. Neither function is a security definer.

Both the view name and source table names must contain exactly two identifier parts, for example `api.orders_dv` and `shop.orders`.

## Specification shape

```json
{
  "root": {
    "table": "shop.orders",
    "key": "order_id",
    "fields": {"status": "status", "created": "created_at"},
    "insert": true,
    "update": true,
    "delete": true
  },
  "object": {
    "name": "customer",
    "table": "shop.customers",
    "key": "customer_id",
    "root_fk": "customer_id",
    "fields": {"customerId": "customer_id", "name": "name"},
    "etag": false
  },
  "array": {
    "name": "items",
    "table": "shop.order_items",
    "key": "item_id",
    "parent_fk": "order_id",
    "order_by": "item_id",
    "fields": {
      "itemId": "item_id",
      "product": "product",
      "qty": "quantity",
      "price": "unit_price"
    },
    "insert": true,
    "update": true,
    "delete": true
  }
}
```

`root` is required. `object` and `array` are optional. Operation flags default to `false`; `object.etag` defaults to `true`; and `array.order_by` defaults to the array key.

Field names must match `^[A-Za-z][A-Za-z0-9_]{0,62}$`. `_id` and `_metadata` are reserved. Source identifiers are resolved from PostgreSQL catalogues and quoted in generated SQL.

## Generated objects

For a view named `api.orders_dv`, the generator creates:

- `api.orders_dv`;
- `api.orders_dv__insert()` and its `INSTEAD OF INSERT` trigger;
- `api.orders_dv__update()` and its `INSTEAD OF UPDATE` trigger;
- `api.orders_dv__delete()` and its `INSTEAD OF DELETE` trigger;
- a view comment containing the normalised specification.

Generated objects have no runtime dependency on the `jsontailor` schema. Existing objects are not replaced automatically.

## Permissions

The caller needs `CREATE` on the target schema and the relevant privileges on source tables. Runtime users need privileges on the view and source tables for the operations they perform. Reads require `SELECT`; writes additionally require the corresponding `INSERT`, `UPDATE` or `DELETE` privileges. Locking rows with `SELECT FOR UPDATE` also requires `UPDATE`.

## Errors

- `22023`: invalid specification or argument;
- `PT412`: stale ETag;
- `PT428`: required ETag missing;
- `PT422`: invalid document or operation disabled by the specification;
- `42501`: insufficient privilege or RLS coverage failure.

Run DDL returned by `duality_view_ddl` in one transaction with stop-on-error behaviour.
