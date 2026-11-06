#!/usr/bin/env python3
"""PGJT-49 dump/restore matrix for shapes, ACLs and regeneration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from dump_restore import Commands, identifier, literal


ROLE_GRANTOR = "jt49_grantor"
ROLE_GRANTEE = "jt49_grantee"


def failure_state(commands: Commands, database: str, sql: str) -> str | None:
    completed = subprocess.run(
        [str(commands.pg_bin / "psql"), "-XAt", "-v", "ON_ERROR_STOP=1",
         "-d", database], input="\\set VERBOSITY verbose\n" + sql,
        text=True, capture_output=True,
        env=os.environ.copy())
    match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", completed.stderr)
    return match.group(1) if match else None


def acl_snapshot(commands: Commands, database: str, grantor: str | None) -> str:
    grantor_oid = ("(SELECT oid FROM pg_roles WHERE rolname=" + literal(grantor) + ")"
                   if grantor else "(SELECT oid FROM pg_roles WHERE rolname=current_user)")
    downstream = commands.sql(database, f"""
SELECT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl)x
 WHERE c.oid='api.full_dv'::regclass AND x.grantee=(SELECT oid FROM pg_roles WHERE rolname='{ROLE_GRANTEE}')
 AND x.grantor={grantor_oid} AND x.privilege_type='SELECT' AND NOT x.is_grantable)::text||':'||
 EXISTS(SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl)x
 WHERE a.attrelid='api.full_dv'::regclass AND a.attname='data'
 AND x.grantee=(SELECT oid FROM pg_roles WHERE rolname='{ROLE_GRANTEE}')
 AND x.grantor={grantor_oid} AND x.privilege_type='SELECT' AND NOT x.is_grantable)::text||':'||
 EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl)x
 WHERE p.oid='api.full_dv__check()'::regprocedure
 AND x.grantee=(SELECT oid FROM pg_roles WHERE rolname='{ROLE_GRANTEE}')
 AND x.grantor={grantor_oid} AND x.privilege_type='EXECUTE' AND NOT x.is_grantable)::text;
""")
    if grantor is None:
        return downstream
    upstream = commands.sql(database, f"""
SELECT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl)x
 WHERE c.oid='api.full_dv'::regclass AND x.grantee={grantor_oid}
 AND x.privilege_type='SELECT' AND x.is_grantable)::text||':'||
 EXISTS(SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl)x
 WHERE a.attrelid='api.full_dv'::regclass AND a.attname='data' AND x.grantee={grantor_oid}
 AND x.privilege_type='SELECT' AND x.is_grantable)::text||':'||
 EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl)x
 WHERE p.oid='api.full_dv__check()'::regprocedure AND x.grantee={grantor_oid}
 AND x.privilege_type='EXECUTE' AND x.is_grantable)::text;
""")
    return downstream + ":" + upstream


def cleanup(bootstrap: Commands, databases: tuple[str, ...], bootstrap_database: str,
            active_error: bool) -> None:
    errors: list[str] = []
    for database in databases:
        try:
            bootstrap.drop(database)
        except Exception as error:
            errors.append(f"{database}: {error}")
    try:
        bootstrap.sql(bootstrap_database,
                      f"DROP ROLE IF EXISTS {ROLE_GRANTEE}; "
                      f"DROP ROLE IF EXISTS {ROLE_GRANTOR};")
    except Exception as error:
        errors.append(f"roles: {error}")
    if errors and not active_error:
        raise RuntimeError("cleanup failed: " + " | ".join(errors))


def setup_sql(specs: dict[str, dict]) -> str:
    create = "\n".join(
        "SELECT jsontailor.create_duality_view(" + literal("api." + name)
        + "," + literal(json.dumps(spec, separators=(",", ":"))) + "::jsonb);"
        for name, spec in specs.items())
    return f"""
CREATE EXTENSION pg_jsontailor;
CREATE SCHEMA jt49; CREATE SCHEMA api;
CREATE TABLE jt49.root(id integer PRIMARY KEY,label text NOT NULL,
 detail_id integer);
CREATE TABLE jt49.detail(id integer PRIMARY KEY,label text NOT NULL);
ALTER TABLE jt49.root ADD CONSTRAINT root_detail_fk
 FOREIGN KEY(detail_id) REFERENCES jt49.detail(id);
CREATE TABLE jt49.item(id integer PRIMARY KEY,parent integer NOT NULL
 REFERENCES jt49.root(id),label text NOT NULL);
INSERT INTO jt49.detail VALUES(7,'detail');
INSERT INTO jt49.root VALUES(1,'root',7),(2,'second',NULL),(3,'third',7),(4,'fourth',NULL);
INSERT INTO jt49.item VALUES(1,3,'array'),(2,4,'array-only');
CREATE VIEW api.root_only_dv__object_binding AS SELECT 'object-decoy'::text marker;
CREATE VIEW api.root_only_dv__array_binding AS SELECT 'array-decoy'::text marker;
CREATE VIEW api.root_object_dv__array_binding AS SELECT 'array-decoy'::text marker;
CREATE VIEW api.root_array_dv__object_binding AS SELECT 'object-decoy'::text marker;
{create}
COMMENT ON VIEW api.root_only_dv__object_binding IS 'optional object decoy';
COMMENT ON VIEW api.root_only_dv__array_binding IS 'optional array decoy';
COMMENT ON VIEW api.root_object_dv__array_binding IS 'optional array decoy';
COMMENT ON VIEW api.root_array_dv__object_binding IS 'optional object decoy';
COMMENT ON FUNCTION api.full_dv__check() IS 'PGJT-49 health comment';
COMMENT ON FUNCTION api.full_dv__delete_document(integer,text)
 IS 'PGJT-49 delete comment';
GRANT USAGE ON SCHEMA api TO {ROLE_GRANTOR},{ROLE_GRANTEE};
GRANT SELECT ON api.full_dv TO {ROLE_GRANTOR} WITH GRANT OPTION;
SET ROLE {ROLE_GRANTOR}; GRANT SELECT ON api.full_dv TO {ROLE_GRANTEE}; RESET ROLE;
GRANT SELECT(data) ON api.full_dv TO {ROLE_GRANTOR} WITH GRANT OPTION;
SET ROLE {ROLE_GRANTOR}; GRANT SELECT(data) ON api.full_dv TO {ROLE_GRANTEE}; RESET ROLE;
REVOKE EXECUTE ON FUNCTION api.full_dv__check() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION api.full_dv__check() TO {ROLE_GRANTOR} WITH GRANT OPTION;
SET ROLE {ROLE_GRANTOR};
GRANT EXECUTE ON FUNCTION api.full_dv__check() TO {ROLE_GRANTEE}; RESET ROLE;
REVOKE SELECT ON api.root_only_dv FROM CURRENT_USER;
"""


def verify(commands: Commands, database: str, restore_format: str) -> list[dict]:
    observed = commands.sql(database, f"""
SELECT count(*)||':'||count(*) FILTER(WHERE health_status='OK' AND
 generator_hash=current_generator_hash) FROM jsontailor.duality_view_inventory();
SELECT string_agg(view_name||'='||health_status,',' ORDER BY view_name)
 FROM jsontailor.duality_view_inventory();
SELECT string_agg(c.relname||'='||coalesce(obj_description(c.oid,'pg_class'),''),','
 ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='api' AND c.relname IN('root_only_dv__object_binding',
 'root_only_dv__array_binding','root_object_dv__array_binding',
 'root_array_dv__object_binding');
SELECT 'acl-catalog';
SELECT coalesce(obj_description('api.full_dv__check()'::regprocedure,'pg_proc'),'')||':'||
 coalesce(obj_description('api.full_dv__delete_document(integer,text)'::regprocedure,'pg_proc'),'');
SELECT 'api.full_dv'::regclass::oid::text;
""").splitlines()
    expected_inventory = (
        "api.full_dv=OK,api.root_array_dv=OK,api.root_object_dv=OK,"
        "api.root_only_dv=OK")
    decoys = ("root_array_dv__object_binding=optional object decoy,"
              "root_object_dv__array_binding=optional array decoy,"
              "root_only_dv__array_binding=optional array decoy,"
              "root_only_dv__object_binding=optional object decoy")
    if observed[:5] != ["4:4", expected_inventory, decoys, "acl-catalog",
                        "PGJT-49 health comment:PGJT-49 delete comment"]:
        raise RuntimeError(f"PGJT-49 restored metadata differs: {observed}")
    oid = observed[5]

    if acl_snapshot(commands, database, ROLE_GRANTOR) != "true:true:true:true:true:true":
        raise RuntimeError("delegated ACL catalogue state was not restored exactly")
    pre_crud = commands.sql(database, """
UPDATE api.root_only_dv SET data=jsonb_set(data,'{label}','"pre-root"') WHERE _id=1;
UPDATE api.root_object_dv SET data=jsonb_set(data,'{label}','"pre-object"') WHERE _id=2;
UPDATE api.root_array_dv SET data=jsonb_set(data,'{label}','"pre-array"') WHERE _id=4;
UPDATE api.full_dv SET data=jsonb_set(data,'{label}','"pre-full"') WHERE _id=3;
SELECT string_agg(id||'='||label,',' ORDER BY id) FROM jt49.root;
""").splitlines()
    if pre_crud != ["UPDATE 1", "UPDATE 1", "UPDATE 1", "UPDATE 1",
                    "1=pre-root,2=pre-object,3=pre-full,4=pre-array"]:
        raise RuntimeError(f"pre-regeneration CRUD differs: {pre_crud}")

    # Isolate each delegated chain so one ACL class cannot mask another.
    commands.sql(database, f"""REVOKE SELECT(data) ON api.full_dv FROM {ROLE_GRANTOR} CASCADE;
REVOKE EXECUTE ON FUNCTION api.full_dv__check() FROM {ROLE_GRANTOR} CASCADE;""")
    if failure_state(commands, database, "SELECT jsontailor.regenerate_duality_view('api.full_dv');") != "2BP01" \
            or acl_snapshot(commands, database, ROLE_GRANTOR) != "true:false:false:true:false:false":
        raise RuntimeError("delegated table ACL guard failed")
    commands.sql(database, f"""REVOKE SELECT ON api.full_dv FROM {ROLE_GRANTOR} CASCADE;
GRANT SELECT(data) ON api.full_dv TO {ROLE_GRANTOR} WITH GRANT OPTION;
SET ROLE {ROLE_GRANTOR}; GRANT SELECT(data) ON api.full_dv TO {ROLE_GRANTEE}; RESET ROLE;""")
    if failure_state(commands, database, "SELECT jsontailor.regenerate_duality_view('api.full_dv');") != "2BP01" \
            or acl_snapshot(commands, database, ROLE_GRANTOR) != "false:true:false:false:true:false":
        raise RuntimeError("delegated column ACL guard failed")
    commands.sql(database, f"""REVOKE SELECT(data) ON api.full_dv FROM {ROLE_GRANTOR} CASCADE;
GRANT EXECUTE ON FUNCTION api.full_dv__check() TO {ROLE_GRANTOR} WITH GRANT OPTION;
SET ROLE {ROLE_GRANTOR}; GRANT EXECUTE ON FUNCTION api.full_dv__check() TO {ROLE_GRANTEE}; RESET ROLE;""")
    if failure_state(commands, database, "SELECT jsontailor.regenerate_duality_view('api.full_dv');") != "2BP01" \
            or acl_snapshot(commands, database, ROLE_GRANTOR) != "false:false:true:false:false:true":
        raise RuntimeError("delegated function ACL guard failed")
    commands.sql(database, f"""REVOKE EXECUTE ON FUNCTION api.full_dv__check() FROM {ROLE_GRANTOR} CASCADE;
GRANT EXECUTE ON FUNCTION api.full_dv__check() TO CURRENT_USER;""")

    owner_restricted = commands.sql(database, """
SELECT 'api.root_only_dv'::regclass::oid::text||':'||
 coalesce((SELECT relacl::text FROM pg_class WHERE oid='api.root_only_dv'::regclass),'NULL');
""")
    owner_block = failure_state(
        commands, database,
        "SELECT jsontailor.regenerate_duality_view('api.root_only_dv');")
    if owner_block != "2BP01":
        raise RuntimeError("owner ACL regeneration did not fail closed")
    owner_after = commands.sql(database, """
SELECT 'api.root_only_dv'::regclass::oid::text||':'||
 coalesce((SELECT relacl::text FROM pg_class WHERE oid='api.root_only_dv'::regclass),'NULL');
""")
    if owner_after != owner_restricted:
        raise RuntimeError("owner ACL rejection was not atomic")
    commands.sql(database, """
GRANT SELECT ON api.root_only_dv TO CURRENT_USER;
GRANT SELECT ON api.full_dv TO jt49_grantee;
GRANT SELECT(data) ON api.full_dv TO jt49_grantee;
GRANT EXECUTE ON FUNCTION api.full_dv__check() TO jt49_grantee;
SELECT jsontailor.regenerate_duality_view('api.root_only_dv');
SELECT jsontailor.regenerate_duality_view('api.root_object_dv');
SELECT jsontailor.regenerate_duality_view('api.root_array_dv');
SELECT jsontailor.regenerate_duality_view('api.full_dv');
""")
    if acl_snapshot(commands, database, None) != "true:true:true":
        raise RuntimeError("direct ACLs were not preserved by regeneration")

    post_regeneration = commands.sql(database, """
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='api' AND c.relname IN('root_only_dv__object_binding',
 'root_only_dv__array_binding','root_object_dv__array_binding',
 'root_array_dv__object_binding') AND obj_description(c.oid,'pg_class') LIKE 'optional % decoy';
SELECT coalesce(obj_description('api.full_dv__check()'::regprocedure,'pg_proc'),'')||':'||
 coalesce(obj_description('api.full_dv__delete_document(integer,text)'::regprocedure,'pg_proc'),'');
""").splitlines()
    if post_regeneration != ["4", "PGJT-49 health comment:PGJT-49 delete comment"]:
        raise RuntimeError(f"post-regeneration metadata differs: {post_regeneration}")

    crud = commands.sql(database, """
UPDATE api.root_only_dv SET data=jsonb_set(data,'{label}','"root-updated"') WHERE _id=1;
INSERT INTO api.root_only_dv(data) VALUES('{"_id":10,"label":"root-insert"}');
DELETE FROM api.root_only_dv WHERE _id=10;
UPDATE api.root_object_dv SET data=jsonb_set(data,'{label}','"object-updated"') WHERE _id=2;
UPDATE api.root_array_dv SET data=jsonb_set(data,'{label}','"array-updated"') WHERE _id=4;
UPDATE api.full_dv SET data=jsonb_set(data,'{label}','"full-updated"') WHERE _id=3;
SELECT (SELECT label FROM jt49.root WHERE id=1)||':'||
 (SELECT label FROM jt49.root WHERE id=2)||':'||
 (SELECT label FROM jt49.root WHERE id=4)||':'||
 (SELECT label FROM jt49.root WHERE id=3)||':'||
 (SELECT count(*) FROM jt49.root WHERE id=10);
SELECT count(*)||':'||count(*) FILTER(WHERE health_status='OK' AND
 generator_hash=current_generator_hash) FROM jsontailor.duality_view_inventory();
""").splitlines()
    if crud != ["UPDATE 1", "INSERT 0 1", "DELETE 1", "UPDATE 1", "UPDATE 1",
                "UPDATE 1", "root-updated:object-updated:array-updated:full-updated:0",
                "4:4"]:
        raise RuntimeError(f"PGJT-49 restored CRUD differs: {crud}")
    return [
        {"name": f"PGJT-49/{restore_format}-shape-inventory", "status": "PASS"},
        {"name": f"PGJT-49/{restore_format}-optional-bindings", "status": "PASS"},
        {"name": f"PGJT-49/{restore_format}-acl-comments", "status": "PASS"},
        {"name": f"PGJT-49/{restore_format}-regeneration-guards", "status": "PASS"},
        {"name": f"PGJT-49/{restore_format}-post-restore-crud", "status": "PASS"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--bootstrap-database", default="postgres")
    parser.add_argument("--database-prefix", default="jsontailor_pgjt49")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prefix = identifier(args.database_prefix)
    maintenance = identifier(prefix + "_maintenance")
    source = identifier(prefix + "_source")
    custom = identifier(prefix + "_custom")
    plain = identifier(prefix + "_plain")
    bootstrap = Commands(args.pg_bin, args.bootstrap_database)
    commands = Commands(args.pg_bin, maintenance)
    root = Path(__file__).resolve().parents[1]
    root_spec = {"table": "jt49.root", "key": "id", "fields": {"label": "label"},
                 "insert": True, "update": True, "delete": True}
    object_spec = {"name": "detail", "table": "jt49.detail", "key": "id",
                   "root_fk": "detail_id", "fields": {"id": "id", "label": "label"}}
    array_spec = {"name": "items", "table": "jt49.item", "key": "id",
                  "parent_fk": "parent", "fields": {"id": "id", "label": "label"},
                  "insert": True, "update": True, "delete": True}
    specs = {
        "root_only_dv": {"root": root_spec},
        "root_object_dv": {"root": root_spec, "object": object_spec},
        "root_array_dv": {"root": root_spec, "array": array_spec},
        "full_dv": {"root": root_spec, "object": object_spec, "array": array_spec}}
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="pgjt49-") as temporary:
        archive = Path(temporary) / "database.dump"
        plain_file = Path(temporary) / "database.sql"
        try:
            for database in (plain, custom, source, maintenance):
                bootstrap.drop(database)
            bootstrap.create(maintenance)
            commands.create(source)
            bootstrap.sql(args.bootstrap_database,
                          f"DROP ROLE IF EXISTS {ROLE_GRANTEE}; DROP ROLE IF EXISTS {ROLE_GRANTOR};"
                          f" CREATE ROLE {ROLE_GRANTOR} NOLOGIN; CREATE ROLE {ROLE_GRANTEE} NOLOGIN;")
            commands.sql(source, setup_sql(specs))
            commands.run("pg_dump", "--format=custom", "--file", str(archive), source)
            commands.run("pg_dump", "--format=plain", "--file", str(plain_file), source)
            commands.create(custom)
            commands.run("pg_restore", "--exit-on-error", "--dbname", custom, str(archive))
            results += verify(commands, custom, "custom")
            commands.create(plain)
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", plain,
                         "-f", str(plain_file))
            results += verify(commands, plain, "plain")
            results.append({"name": "PGJT-49/maintenance-database-integration",
                            "status": "PASS", "observed": maintenance})
        finally:
            active_error = sys.exc_info()[0] is not None
            cleanup(bootstrap, (plain, custom, source, maintenance),
                    args.bootstrap_database, active_error)
    if args.output:
        args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results))
    print("PGJT-49 dump/restore matrix: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
