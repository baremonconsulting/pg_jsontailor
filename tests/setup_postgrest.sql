\set ON_ERROR_STOP on

\ir ../examples/shop_schema.sql
CREATE SCHEMA api;
SELECT jsontailor.create_duality_view('api.orders_dv', :'spec'::jsonb);
SELECT jsontailor.create_duality_view('api.fresh_acl_dv', :'spec'::jsonb);
SELECT jsontailor.create_duality_view('api.concurrent_dv',
  jsonb_set(:'spec'::jsonb,'{object,etag}','true'::jsonb));

DO $setup$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jt_postgrest_anon') THEN
    CREATE ROLE jt_postgrest_anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jt_postgrest_authenticator') THEN
    CREATE ROLE jt_postgrest_authenticator LOGIN;
  END IF;
END
$setup$;

GRANT jt_postgrest_anon TO jt_postgrest_authenticator;
GRANT USAGE ON SCHEMA api, shop TO jt_postgrest_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON api.orders_dv TO jt_postgrest_anon;
GRANT SELECT, DELETE ON api.fresh_acl_dv TO jt_postgrest_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON api.concurrent_dv TO jt_postgrest_anon;
SELECT jsontailor.grant_delete_rpc('api.orders_dv','jt_postgrest_anon',false);
SELECT jsontailor.grant_array_reader('api.orders_dv','jt_postgrest_anon',false);
SELECT jsontailor.grant_array_reader('api.fresh_acl_dv','jt_postgrest_anon',false);
SELECT jsontailor.grant_delete_rpc('api.concurrent_dv','jt_postgrest_anon',false);
SELECT jsontailor.grant_array_reader('api.concurrent_dv','jt_postgrest_anon',false);
GRANT SELECT, INSERT, UPDATE, DELETE ON shop.orders, shop.order_items
  TO jt_postgrest_anon;
GRANT SELECT ON shop.customers TO jt_postgrest_anon;
GRANT UPDATE(name) ON shop.customers TO jt_postgrest_anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shop TO jt_postgrest_anon;
