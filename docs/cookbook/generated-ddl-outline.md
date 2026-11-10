# Generated DDL outline for the shipped orders mapping

This is an abridged reading aid, not deployable SQL. Generate the authoritative
text with the current candidate and compare it with this structure.

For candidate 0.2.0 the unpadded output of `duality_view_ddl` for the committed
orders specification is 225,910 bytes with SHA-256
`b6816672d208858b61f1b3e87ed8d64c85e5404614241ae50af284c691253882`
on PostgreSQL 17, and 225,964 bytes with SHA-256
`0e2dba7f9fc01794b9a6db5b5c3b2ec53099568f34724c5442f21565e4d62d5a`
on PostgreSQL 18 and 19. PostgreSQL 18 introduced the `conenforced` catalogue
check, which accounts for the intentional difference.
The documentation smoke test regenerates the output on every supported
PostgreSQL major and fails on an object-inventory or byte-level difference.

For `examples/orders_dv.spec.json`, the generator creates:

1. three zero-row binding views for root, object and array relations;
2. the owner-only bounded-array reader;
3. the public document view;
4. one shared trigger-body implementation copied into `__insert`, `__update`
   and `__delete`, branching on `TG_OP`;
5. six triggers: three row-level INSTEAD OF triggers and three statement guards;
6. `__guard`, `__check` and `__delete_document` functions;
7. seven owner-only ACL-revocation blocks;
8. a metadata comment containing the normalised specification and generator
   provenance.

The complete mapping therefore has four views, seven functions and six
triggers. These counts apply only to this complete shipped mapping. Capture
actual deployed state with `examples/orders_deployed_manifest.sql` as shown in
recipe 04: all exact identities, definitions, trigger enabled state, view options,
owners, raw/effective ACLs, comments, metadata and health are retained. The
following older queries are useful partial inspections, not a complete manifest:

```sql
SELECT pg_get_viewdef('api.orders_dv'::regclass, true);
SELECT pg_get_functiondef(p.oid)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE p.oid IN ('api.orders_dv__insert()'::regprocedure,
 'api.orders_dv__update()'::regprocedure,'api.orders_dv__delete()'::regprocedure,
 'api.orders_dv__guard()'::regprocedure,'api.orders_dv__check()'::regprocedure,
 'api.orders_dv__bounded_array(bigint)'::regprocedure,
 'api.orders_dv__delete_document(bigint,text)'::regprocedure)
ORDER BY p.proname,pg_get_function_identity_arguments(p.oid);
SELECT event_object_schema,event_object_table,trigger_name,event_manipulation
FROM information_schema.triggers
WHERE event_object_schema='api' AND event_object_table='orders_dv'
ORDER BY trigger_name,event_manipulation;
```

For an existing view, recover the stored normalised specification and regenerate
inspection output:

```sql
SELECT jsontailor.duality_view_ddl(
  'api.orders_dv',
  (substr(obj_description('api.orders_dv'::regclass,'pg_class'),
          length('pg_jsontailor metadata v1: ')+1)::jsonb)->'spec');
```

This regenerates desired state, not actual deployed state or proof of no drift.
Raw output is inspection-only. Extension mode uses reviewed generation followed
by `create_duality_view()`; no-extension mode deploys the verified exporter
bundle. Create executes immediately and displays no DDL. It does not consume
the saved review file. See recipes 03–04 for exact-byte hashes and evidence.

## Annotated abridged DDL

The generated text follows this shape (comments replace repeated projections
and the shared trigger body; this reading aid is intentionally not deployable):

```sql
-- [1] Zero-row binding view: pins relation/column dependencies in the catalogue.
CREATE VIEW api.orders_dv__root_binding AS
SELECT order_id, customer_id, status, created_at
FROM shop.orders WHERE false;

-- [2] Public invoker view: assembles the root, object, array and ETag document.
CREATE VIEW api.orders_dv WITH (security_invoker=true) AS
 SELECT r.order_id AS _id,
    d.payload || jsonb_build_object('_metadata', jsonb_build_object('etag', upper(encode(substr(sha256(convert_to(jsonb_set(d.payload, ARRAY['customer'::text],
        CASE
            WHEN jsonb_typeof(d.payload -> 'customer'::text) = 'object'::text THEN jsonb_build_object('customerId', d.payload #> ARRAY['customer'::text, 'customerId'::text])
            ELSE 'null'::jsonb
        END)::text, 'UTF8'::name)), 1, 16), 'hex'::text)))) AS data
   FROM shop.orders r
     CROSS JOIN LATERAL ( SELECT (jsonb_build_object('_id', r.order_id, 'status', r.status, 'created', r.created_at) || jsonb_build_object('customer', ( SELECT jsonb_build_object('name', o.name, 'customerId', o.customer_id) AS jsonb_build_object
                   FROM shop.customers o
                  WHERE o.customer_id = r.customer_id))) || jsonb_build_object('items',
                CASE
                    WHEN api.orders_dv__bounded_array(( SELECT count(*) AS count
                       FROM ( SELECT 1 AS "?column?"
                               FROM shop.order_items a
                              WHERE a.order_id = r.order_id
                             LIMIT 10001) bounded)) THEN COALESCE(( SELECT jsonb_agg(jsonb_build_object('qty', a.quantity, 'price', a.unit_price, 'itemId', a.item_id, 'product', a.product) ORDER BY a.item_id) AS jsonb_agg
                       FROM shop.order_items a
                      WHERE a.order_id = r.order_id), '[]'::jsonb)
                    ELSE NULL::jsonb
                END) AS payload
         OFFSET 0) d;

-- [3] Owner-only bounded helper: checks cardinality before array aggregation.
CREATE FUNCTION api.orders_dv__bounded_array(bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp
AS $body$ BEGIN /* generated LIMIT N+1 cardinality gate */ RETURN true; END $body$;

-- [4] Shared trigger body branches on TG_OP; wrappers copy this reviewed body.
CREATE FUNCTION api.orders_dv__update() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp
AS $body$ BEGIN /* validate, lock, re-read ETag, reconcile */ RETURN NEW; END $body$;

-- [5] Row trigger plus statement guard; INSERT and DELETE have matching pairs.
CREATE TRIGGER orders_dv__update INSTEAD OF UPDATE ON api.orders_dv
FOR EACH ROW EXECUTE FUNCTION api.orders_dv__update();
CREATE TRIGGER orders_dv__guard_update BEFORE UPDATE ON api.orders_dv
FOR EACH STATEMENT EXECUTE FUNCTION api.orders_dv__guard();

-- [6] Final DDL revokes helper execution, then records specification/provenance.
REVOKE ALL ON FUNCTION api.orders_dv__update() FROM PUBLIC;
COMMENT ON VIEW api.orders_dv IS 'pg_jsontailor metadata v1: {...}';
```
