CREATE EXTENSION pg_jsontailor;
SELECT extversion = '0.2.0' AS version_ok FROM pg_extension WHERE extname='pg_jsontailor';
SELECT count(*) = 2 AS api_ok
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='jsontailor';
SELECT bool_and(NOT p.prosecdef) AND
       bool_and(p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp']) AS security_ok
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='jsontailor';
SELECT has_schema_privilege('public','jsontailor','USAGE') AS public_usage;
DROP EXTENSION pg_jsontailor;
