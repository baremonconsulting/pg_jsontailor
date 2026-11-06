\set ON_ERROR_STOP on

DROP TABLE IF EXISTS bench_inputs;
CREATE TABLE bench_inputs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile text NOT NULL,
    target jsonb,
    patch jsonb
);

-- Deterministic synthetic rows. Sizes are measured after insertion and reported;
-- the labels are targets, not claims of exact serialised size.
INSERT INTO bench_inputs(profile, target, patch)
SELECT 'portable_1k',
       pg_catalog.jsonb_build_object(
           'payload', pg_catalog.repeat('x', 900),
           'nested', pg_catalog.jsonb_build_object('keep', g, 'change', g),
           'remove', g),
       CASE WHEN g % 2 = 0
            THEN '{"nested":{"change":999},"remove":null,"added":true}'::jsonb
            ELSE '{"nested":{"change":999},"added":true}'::jsonb END
FROM pg_catalog.generate_series(1, 400) AS series(g);

INSERT INTO bench_inputs(profile, target, patch)
SELECT 'portable_16k',
       pg_catalog.jsonb_build_object(
           'payload', pg_catalog.repeat('x', 15800),
           'nested', pg_catalog.jsonb_build_object('keep', g, 'change', g),
           'remove', g),
       pg_catalog.jsonb_build_object(
           'nested', pg_catalog.jsonb_build_object('change', g + 1),
           'remove', null,
           'added', pg_catalog.repeat('y', CASE WHEN g % 2 = 0 THEN 32 ELSE 512 END))
FROM pg_catalog.generate_series(1, 100) AS series(g);

INSERT INTO bench_inputs(profile, target, patch)
SELECT 'stress_256k',
       pg_catalog.jsonb_build_object(
           'payload', pg_catalog.repeat('x', 261800),
           'nested', pg_catalog.jsonb_build_object('keep', g, 'change', g),
           'remove', g),
       pg_catalog.jsonb_build_object(
           'nested', pg_catalog.jsonb_build_object('change', g + 1),
           'remove', null,
           'added', pg_catalog.repeat('z', CASE WHEN g % 2 = 0 THEN 1024 ELSE 16384 END))
FROM pg_catalog.generate_series(1, 20) AS series(g);

ANALYZE bench_inputs;

SELECT profile, count(*) AS rows,
       min(pg_catalog.octet_length(target::text)) AS min_target_json_bytes,
       max(pg_catalog.octet_length(target::text)) AS max_target_json_bytes,
       min(pg_catalog.octet_length(patch::text)) AS min_patch_json_bytes,
       max(pg_catalog.octet_length(patch::text)) AS max_patch_json_bytes,
       min(pg_catalog.pg_column_size(target)) AS min_target_storage_bytes,
       max(pg_catalog.pg_column_size(target)) AS max_target_storage_bytes,
       min(pg_catalog.pg_column_size(patch)) AS min_patch_storage_bytes,
       max(pg_catalog.pg_column_size(patch)) AS max_patch_storage_bytes
FROM bench_inputs
GROUP BY profile
ORDER BY profile;

