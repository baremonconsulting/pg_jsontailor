\set ON_ERROR_STOP on

BEGIN;

DO $verify$
DECLARE
    total integer;
    valid integer;
BEGIN
    SELECT count(*),count(*) FILTER (WHERE
               (p.proname='create_duality_view'
                AND p.pronargs=2
                AND p.proargtypes[0]='pg_catalog.text'::regtype::oid
                AND p.proargtypes[1]='pg_catalog.jsonb'::regtype::oid
                AND p.prorettype='pg_catalog.void'::regtype::oid
                AND p.provolatile='v')
            OR (p.proname='duality_view_ddl'
                AND p.pronargs=2
                AND p.proargtypes[0]='pg_catalog.text'::regtype::oid
                AND p.proargtypes[1]='pg_catalog.jsonb'::regtype::oid
                AND p.prorettype='pg_catalog.text'::regtype::oid
                AND p.provolatile='s')
            OR (p.proname='duality_view_inventory'
                AND p.pronargs=0
                AND p.prorettype='pg_catalog.record'::regtype::oid
                AND p.proretset
                AND p.provolatile='v')
            OR (p.proname='regenerate_duality_view'
                AND p.pronargs=1
                AND p.proargtypes[0]='pg_catalog.text'::regtype::oid
                AND p.prorettype='pg_catalog.void'::regtype::oid
                AND p.provolatile='v'))
      INTO total,valid
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_proc'::regclass
                                    AND d.objid=p.oid AND d.deptype='e'
      JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
     WHERE n.nspname='jsontailor' AND e.extname='pg_jsontailor';

    IF total<>4 OR valid<>4 THEN
        RAISE EXCEPTION 'extension function type contract mismatch';
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
INSERT INTO installation_check.roots VALUES(1,'original');

CREATE SCHEMA disruptive;
CREATE FUNCTION disruptive.parse_ident(text,boolean)
RETURNS text[] LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'hostile search_path function was resolved'; END
$$;
SET search_path=disruptive,pg_catalog;
SELECT jsontailor.create_duality_view(
    'installation_check.documents',
    '{"root":{"table":"installation_check.roots","key":"id","fields":{"value":"value"},"update":true}}'::jsonb
);
RESET search_path;

DO $smoke$
DECLARE
    observed jsonb;
    trigger_count integer;
BEGIN
    IF to_regclass('installation_check.documents') IS NULL THEN
        RAISE EXCEPTION 'generator did not create the requested view';
    END IF;
    SELECT data INTO observed FROM installation_check.documents WHERE _id=1;
    IF observed->>'_id'<>'1' OR observed->>'value'<>'original'
       OR observed#>>'{_metadata,etag}' IS NULL THEN
        RAISE EXCEPTION 'generated view returned an invalid document: %',observed;
    END IF;
    SELECT count(*) INTO trigger_count
      FROM pg_catalog.pg_trigger
     WHERE tgrelid='installation_check.documents'::regclass AND NOT tgisinternal;
    IF trigger_count<>3
       OR to_regprocedure('installation_check.documents__delete_document(integer,text)') IS NULL THEN
        RAISE EXCEPTION 'generated write contract is incomplete';
    END IF;
    UPDATE installation_check.documents
       SET data=jsonb_set(data,'{value}','"updated"')
     WHERE _id=1;
    IF (SELECT value FROM installation_check.roots WHERE id=1)<>'updated' THEN
        RAISE EXCEPTION 'generated update path failed semantic read-back';
    END IF;
END
$smoke$;

ROLLBACK;
