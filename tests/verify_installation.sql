\set ON_ERROR_STOP on

BEGIN;

DO $verify$
DECLARE
    actual jsonb;
    expected constant jsonb := '[
      {"name":"create_duality_view","arguments":"text, jsonb","result":"void","volatility":"v"},
      {"name":"duality_view_ddl","arguments":"text, jsonb","result":"text","volatility":"s"}
    ]'::jsonb;
BEGIN
    SELECT jsonb_agg(jsonb_build_object(
               'name',p.proname,
               'arguments',pg_catalog.oidvectortypes(p.proargtypes),
               'result',pg_catalog.pg_get_function_result(p.oid),
               'volatility',p.provolatile
           ) ORDER BY p.proname)
      INTO actual
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_proc'::regclass
                                    AND d.objid=p.oid AND d.deptype='e'
      JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
     WHERE n.nspname='jsontailor' AND e.extname='pg_jsontailor';

    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'extension function contract mismatch: %',actual;
    END IF;
    IF EXISTS(
        SELECT 1
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_proc'::regclass
                                        AND d.objid=p.oid AND d.deptype='e'
          JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
         WHERE n.nspname='jsontailor' AND e.extname='pg_jsontailor'
           AND (p.prosecdef OR p.proisstrict OR p.proparallel<>'u'
                OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, pg_temp'])
    ) THEN
        RAISE EXCEPTION 'extension function safety attributes differ from the contract';
    END IF;
END
$verify$;

CREATE SCHEMA installation_check;
CREATE TABLE installation_check.roots(id integer PRIMARY KEY,value text);

CREATE SCHEMA disruptive;
CREATE FUNCTION disruptive.parse_ident(text,boolean)
RETURNS text[] LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'hostile search_path function was resolved'; END
$$;
SET search_path=disruptive,pg_catalog;
SELECT pg_catalog.strpos(
           jsontailor.duality_view_ddl(
             'installation_check.documents',
             '{"root":{"table":"installation_check.roots","key":"id","fields":{"value":"value"}}}'::jsonb
           ),
           'CREATE VIEW installation_check.documents'
       )>0 AS hostile_search_path_passed;
RESET search_path;

ROLLBACK;
