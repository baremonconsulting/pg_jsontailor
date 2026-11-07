\set ON_ERROR_STOP on
-- DISPOSABLE DATABASE ONLY: shop_schema.sql destroys shop and its dependants.
-- Supply spec using psql -v spec="$(tr -d '\n' < examples/orders_dv.spec.json)".
CREATE EXTENSION pg_jsontailor;
\ir shop_schema.sql
CREATE SCHEMA api;
SELECT jsontailor.create_duality_view('api.orders_dv', :'spec'::jsonb);

INSERT INTO api.orders_dv(data) VALUES (
 '{"status":"new","created":"2026-11-03","customer":{"customerId":7},
   "items":[{"product":"Adapter","qty":1,"price":12.34}]}'::jsonb)
RETURNING _id AS inserted_id \gset
SELECT data#>>'{_metadata,etag}' AS before_etag
FROM api.orders_dv WHERE _id = :inserted_id \gset
SELECT 'quickstart/read=' || (data->>'status') FROM api.orders_dv
WHERE _id = :inserted_id;

UPDATE api.orders_dv SET data = jsonb_set(data, '{status}', '"processing"'::jsonb)
WHERE _id = :inserted_id AND data#>>'{_metadata,etag}' = :'before_etag';
SELECT data#>>'{_metadata,etag}' AS current_etag,
       (data->>'status' = 'processing'
        AND data#>>'{_metadata,etag}' <> :'before_etag') AS updated
FROM api.orders_dv WHERE _id = :inserted_id \gset
\if :updated
\echo quickstart/update=PASS
\else
\quit 1
\endif

SELECT api.orders_dv__delete_document(:inserted_id, :'current_etag') IS NOT NULL
AS deleted \gset
SELECT NOT EXISTS(SELECT FROM shop.orders WHERE order_id = :inserted_id)
   AND NOT EXISTS(SELECT FROM shop.order_items WHERE order_id = :inserted_id)
   AND :'deleted'::boolean AS cleaned \gset
\if :cleaned
\echo quickstart/delete=PASS
\else
\quit 1
\endif
