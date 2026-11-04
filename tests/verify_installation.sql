\set ON_ERROR_STOP on

DO $verify$
DECLARE
    attrs record;
BEGIN
    SELECT p.provolatile, p.proisstrict, p.proparallel, p.prosecdef,
           pg_catalog.pg_get_function_result(p.oid) AS result_type
      INTO STRICT attrs
      FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'jsontailor'
       AND p.proname = 'merge_patch'
       AND p.proargtypes = '3802 3802'::oidvector;

    IF attrs.provolatile <> 'i' OR NOT attrs.proisstrict
       OR attrs.proparallel <> 's' OR attrs.prosecdef
       OR attrs.result_type <> 'jsonb' THEN
        RAISE EXCEPTION 'catalog contract mismatch: %', row_to_json(attrs);
    END IF;
END
$verify$;

CREATE SCHEMA disruptive;

CREATE FUNCTION disruptive.fail_jsonb_jsonb(jsonb, jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RAISE EXCEPTION 'disruptive jsonb/jsonb operator was resolved'; END
$$;
CREATE OPERATOR disruptive.= (
    LEFTARG = jsonb, RIGHTARG = jsonb,
    FUNCTION = disruptive.fail_jsonb_jsonb
);

CREATE FUNCTION disruptive.fail_text_text(text, text)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RAISE EXCEPTION 'disruptive text/text operator was resolved'; END
$$;
CREATE OPERATOR disruptive.= (
    LEFTARG = text, RIGHTARG = text,
    FUNCTION = disruptive.fail_text_text
);
CREATE OPERATOR disruptive.<> (
    LEFTARG = text, RIGHTARG = text,
    FUNCTION = disruptive.fail_text_text
);

CREATE FUNCTION disruptive.fail_jsonb_text_jsonb(jsonb, text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RAISE EXCEPTION 'disruptive jsonb/text jsonb operator was resolved'; END
$$;
CREATE OPERATOR disruptive.- (
    LEFTARG = jsonb, RIGHTARG = text,
    FUNCTION = disruptive.fail_jsonb_text_jsonb
);
CREATE OPERATOR disruptive.-> (
    LEFTARG = jsonb, RIGHTARG = text,
    FUNCTION = disruptive.fail_jsonb_text_jsonb
);

CREATE FUNCTION disruptive.fail_jsonb_text_bool(jsonb, text)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RAISE EXCEPTION 'disruptive jsonb/text boolean operator was resolved'; END
$$;
CREATE OPERATOR disruptive.? (
    LEFTARG = jsonb, RIGHTARG = text,
    FUNCTION = disruptive.fail_jsonb_text_bool
);

CREATE FUNCTION disruptive.fail_jsonb_jsonb_result(jsonb, jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RAISE EXCEPTION 'disruptive concatenation operator was resolved'; END
$$;
CREATE OPERATOR disruptive.|| (
    LEFTARG = jsonb, RIGHTARG = jsonb,
    FUNCTION = disruptive.fail_jsonb_jsonb_result
);

CREATE FUNCTION disruptive.jsonb_typeof(jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RAISE EXCEPTION 'disruptive jsonb_typeof was resolved'; END
$$;

SET search_path = disruptive, pg_catalog;
SELECT jsontailor.merge_patch(
    '{"a":{"b":1},"remove":2}'::jsonb,
    '{"a":{"c":3},"remove":null}'::jsonb
) OPERATOR(pg_catalog.=) '{"a":{"b":1,"c":3}}'::jsonb
  AS hostile_search_path_passed;
RESET search_path;

