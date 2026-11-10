#!/usr/bin/env python3
"""Execute the quick start and PGJT-122 documentation examples in disposable databases."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid

from dump_restore import Commands, literal

ROOT = Path(__file__).resolve().parents[1]
RESULT_IDS = ("PGJT-65/readme-quickstart", "PGJT-116/no-extension-runbook",
              "PGJT-117/task-cookbook", "PGJT-122/documentation-examples-executed",
              "PGJT-126/deployment-paths", "PGJT-127/rls-cookbook",
              "PGJT-128/literal-cookbook", "PGJT-129/socket-cookbook",
              "PGJT-130/deployed-manifest")
ORDERS_DDL_SHA256 = {
    "17": "b6816672d208858b61f1b3e87ed8d64c85e5404614241ae50af284c691253882",
    "18": "0e2dba7f9fc01794b9a6db5b5c3b2ec53099568f34724c5442f21565e4d62d5a",
    "19": "0e2dba7f9fc01794b9a6db5b5c3b2ec53099568f34724c5442f21565e4d62d5a",
}


def walkthrough(readme: str) -> str:
    section = readme.split("<!-- quickstart-smoke:start -->", 1)[1].split(
        "<!-- quickstart-smoke:end -->", 1)[0]
    return section.split("```sh\n", 1)[1].split("```", 1)[0]


def blocks(path: str, language: str) -> list[str]:
    return re.findall(r"^```" + re.escape(language) + r"\n(.*?)^```[ \t]*$",
                      (ROOT / path).read_text(), re.MULTILINE | re.DOTALL)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def expected_failure(commands: Commands, database: str, sql: str,
                     state: str, message: str = "") -> None:
    result = subprocess.run(
        [str(commands.pg_bin / "psql"), "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database],
        input="\\set VERBOSITY verbose\n" + sql, text=True,
        capture_output=True, timeout=60)
    require(result.returncode != 0
            and re.search(r"ERROR:\s+" + state + ":", result.stderr) is not None
            and message in result.stderr,
            f"expected {state}/{message}, got {result.stderr}")


def documentation_examples(pg_bin: Path, maintenance: str) -> dict:
    """Execute PGJT-122 items 1/3/4/5/7, retaining each independent outcome."""
    commands = Commands(pg_bin.resolve(), maintenance)
    database = "pgjt122_" + uuid.uuid4().hex[:12]
    outcomes = {}
    created = False

    def check(name, callback):
        try:
            callback()
        except Exception as error:
            outcomes[name] = {"status": "FAIL", "diagnostics": str(error)}
        else:
            outcomes[name] = {"status": "PASS"}

    try:
        commands.create(database)
        created = True
        commands.sql(database, "CREATE EXTENSION pg_jsontailor;")
        source = (ROOT / "sql/pg_jsontailor--0.2.0.sql").read_text().split(
            "AS $generator$\n", 1)[1].split("\n$generator$;", 1)[0].strip()
        installed = commands.sql(database, "SELECT prosrc FROM pg_proc WHERE oid="
                                 "'jsontailor.duality_view_ddl(text,jsonb)'::regprocedure;")
        require(installed == source, "documentation test installed generator differs from candidate")
        commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database,
                     "-f", str(ROOT / "examples/shop_schema.sql"))
        spec = json.loads((ROOT / "examples/orders_dv.spec.json").read_text())
        commands.sql(database, "CREATE SCHEMA api; SELECT jsontailor.create_duality_view("
                     "'api.orders_dv'," + literal(json.dumps(spec)) + "::jsonb);")

        def readme_real_document():
            readme = (ROOT / "README.md").read_text()
            section = readme.split("<!-- real-document:start -->", 1)[1].split(
                "<!-- real-document:end -->", 1)[0]
            documented = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
            actual = json.loads(commands.sql(database,
                "SELECT data FROM api.orders_dv WHERE _id=42;"))
            require(documented == actual, "README document differs from the shipped fixture")

        check("119/readme-real-document", readme_real_document)

        def performance_example():
            examples = blocks("docs/PERFORMANCE_QUALIFICATION.md", "sql")
            require(len(examples) == 1, "performance SQL block inventory changed")
            commands.sql(database, "INSERT INTO shop.orders(order_id,customer_id,status,created_at) "
                         "VALUES(43,7,'approved','2026-11-03');")
            output = commands.sql(database, examples[0])
            lines = output.splitlines()
            require(len(lines) == 1, "documented prefilter did not return exactly the selected root")
            identity, raw = lines[0].split("|", 1)
            document = json.loads(raw)
            require(identity == "43" and document["_id"] == 43
                    and document["status"] == "approved"
                    and document["customer"] == {"customerId": 7, "name": "ACME"}
                    and document["items"] == [], "documented prefilter returned the wrong document")

        check("1/performance-example", performance_example)
        expected_ddl = commands.sql(database, "SELECT jsontailor.duality_view_ddl("
                                    "'api.orders_dv'," + literal(json.dumps(spec)) + "::jsonb);")
        expected_bytes = bytes.fromhex(commands.sql(database,
            "SELECT encode(convert_to(jsontailor.duality_view_ddl('api.orders_dv',"
            + literal(json.dumps(spec)) + "::jsonb),'UTF8'),'hex');"))
        major = commands.sql(database, "SHOW server_version_num;")[:2]
        require(major in ORDERS_DDL_SHA256, "unreviewed PostgreSQL major")
        require(hashlib.sha256(expected_bytes).hexdigest() == ORDERS_DDL_SHA256[major],
                "orders DDL differs from the independently reviewed candidate artefact")

        def unpadded_ddl_command():
            examples = blocks("docs/cookbook/03-review-ddl.md", "sh")
            require(len(examples) == 1, "DDL command block inventory changed")
            # Bind the documented generator database placeholder; do not repair
            # SQL, psql options or quoting in the command being tested.
            script, bindings = re.subn(r"(?<!\S)-d[ \t]+generator(?=\s|$)",
                                       "-d " + database, examples[0])
            require(bindings == 2, "DDL command requires two generator database placeholders")
            env = os.environ.copy()
            env["PATH"] = str(commands.pg_bin) + os.pathsep + env["PATH"]
            env["spec"] = json.dumps(spec)
            with tempfile.TemporaryDirectory(prefix="pgjt122-ddl-") as temporary:
                (Path(temporary) / "examples").symlink_to(ROOT / "examples", target_is_directory=True)
                (Path(temporary) / ".git").symlink_to(ROOT / ".git", target_is_directory=True)
                result = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                                        cwd=temporary, env=env, text=True,
                                        capture_output=True, timeout=60)
                require(result.returncode == 0, "documented DDL command failed: " + result.stderr)
                generated = Path(temporary) / "generated.raw.sql"
                require(generated.is_file(), "documented DDL command did not create generated.raw.sql")
                require(generated.read_bytes() == expected_bytes,
                        "documented DDL output differs or includes psql padding/headers")

        check("3/unpadded-ddl-command", unpadded_ddl_command)

        def ddl_artefact():
            examples = blocks("docs/cookbook/generated-ddl-outline.md", "sql")
            require(len(examples) == 3, "DDL artefact SQL block inventory changed")
            require(bool(commands.sql(database, examples[0])), "DDL catalogue inspection returned nothing")
            require(commands.sql(database, examples[1]) == expected_ddl,
                    "DDL regenerated from the documented metadata query is not byte-identical")
            counts = commands.sql(database, """SELECT
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='api' AND c.relkind='v')||':'||
 (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='api')||':'||
 (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal
  AND tgrelid='api.orders_dv'::regclass);""")
            require(counts == "4:7:6", "shipped DDL artefact object counts differ: " + counts)
            outline = examples[2]
            for marker in ("CREATE VIEW", "CREATE FUNCTION", "CREATE TRIGGER",
                           "REVOKE ALL", "COMMENT ON VIEW"):
                require(marker in outline, "annotated DDL outline lacks " + marker)

        check("3/ddl-artefact", ddl_artefact)

        def deployed_manifest():
            script = (ROOT / "examples/orders_deployed_manifest.sql").read_text()

            def capture():
                result = commands.run("psql", "-XqAt", "-v", "ON_ERROR_STOP=1",
                                      "-d", database, input_text=script)
                return json.loads(result.stdout)

            before = capture()
            require(before["observed_counts"] == {"views": 4, "functions": 7, "triggers": 6},
                    "deployed manifest object inventory differs")
            require({v["identity"] for v in before["views"]} == {
                "api.orders_dv", "api.orders_dv__root_binding",
                "api.orders_dv__object_binding", "api.orders_dv__array_binding"},
                "manifest view identities differ")
            require({f["identity"] for f in before["functions"]} == {
                "api.orders_dv__bounded_array(bigint)", "api.orders_dv__insert()",
                "api.orders_dv__update()", "api.orders_dv__delete()", "api.orders_dv__guard()",
                "api.orders_dv__check()", "api.orders_dv__delete_document(bigint,text)"},
                "manifest function signatures differ")
            for row in before["views"] + before["functions"]:
                require(bool(row["definition"]) and bool(row["owner"])
                        and bool(row["effective_acl"]) and "raw_acl" in row and "comment" in row,
                        "manifest omits definition/owner/ACL/comment")
            require(all(f["security_definer"] is False and f["config"]
                        for f in before["functions"]), "manifest function security differs")
            require(all(t["enabled"] == "O" and "CREATE TRIGGER" in t["definition"]
                        for t in before["triggers"]), "manifest trigger state differs")
            require({t["name"] for t in before["triggers"]} == {
                "orders_dv__guard_insert", "orders_dv__guard_update", "orders_dv__guard_delete",
                "orders_dv__insert", "orders_dv__update", "orders_dv__delete"},
                "manifest trigger identities differ")
            main_view = next(v for v in before["views"] if v["identity"] == "api.orders_dv")
            require("security_invoker=true" in main_view["options"]
                    and main_view["comment"].startswith("pg_jsontailor metadata v1: ")
                    and before["health"]["status"] == "OK",
                    "manifest omitted invoker state, metadata or healthy check")
            require({s["identity"] for s in before["sources"]} == {
                "shop.orders", "shop.customers", "shop.order_items"},
                "manifest source inventory differs")
            commands.sql(database, "ALTER TRIGGER orders_dv__guard_insert ON api.orders_dv "
                         "RENAME TO orders_dv__guard_insert_probe;")
            try:
                changed = capture()
                require(changed["triggers"] != before["triggers"],
                        "manifest did not expose trigger-definition change")
            finally:
                commands.sql(database, "ALTER TRIGGER orders_dv__guard_insert_probe ON api.orders_dv "
                             "RENAME TO orders_dv__guard_insert;")
            commands.sql(database, "GRANT SELECT(_id) ON api.orders_dv TO PUBLIC;")
            try:
                require(capture()["views"] != before["views"],
                        "manifest did not expose column ACL change")
                require(commands.sql(database, "SELECT jsontailor.duality_view_ddl('api.orders_dv',"
                        + literal(json.dumps(spec)) + "::jsonb);") == expected_ddl,
                        "desired-state generator unexpectedly changed after catalogue drift")
            finally:
                commands.sql(database, "REVOKE SELECT(_id) ON api.orders_dv FROM PUBLIC;")
            require(capture() == before, "manifest did not return to initial state")

        check("130/deployed-manifest", deployed_manifest)

        def no_extension_runbook():
            with tempfile.TemporaryDirectory(prefix="pgjt116-runbook-") as temporary:
                bundle = Path(temporary) / "standalone-orders"
                prefix = "pgjt116_" + uuid.uuid4().hex[:10]
                target, owner, app = prefix + "_db", prefix + "_owner", prefix + "_app"
                exported = subprocess.run(
                    [sys.executable, str(ROOT / "tools/export_standalone.py"),
                     "--psql", str(commands.pg_bin / "psql"), "--database", database,
                     "--view", "api.orders_dv", "--spec",
                     str(ROOT / "examples/orders_dv.spec.json"), "--output", str(bundle)],
                    text=True, capture_output=True, timeout=60)
                require(exported.returncode == 0,
                        "documented standalone export failed: " + exported.stderr)
                verified = subprocess.run(
                    [sys.executable, str(ROOT / "tools/verify_generated_sql_notice.py"),
                     "--bundle", str(bundle)], text=True, capture_output=True, timeout=60)
                require(verified.returncode == 0,
                        "documented standalone verification failed: " + verified.stderr)
                require({path.name for path in bundle.iterdir()}
                        == {"generated.sql", "GENERATED_SQL_NOTICE"},
                        "documented standalone bundle inventory differs")
                commands.sql(commands.maintenance,
                             "CREATE ROLE " + owner + " NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;"
                             "CREATE ROLE " + app + " NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;")
                target_created = False
                try:
                    commands.create(target)
                    target_created = True
                    commands.sql(commands.maintenance,
                                 "ALTER DATABASE " + target + " OWNER TO " + owner + ";")
                    commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", target,
                                 "-c", "SET ROLE " + owner + ";",
                                 "-f", str(ROOT / "examples/shop_schema.sql"))
                    commands.sql(target, "SET ROLE " + owner + ";CREATE SCHEMA api;")
                    applied = subprocess.run(
                        [str(commands.pg_bin / "psql"), "-X", "-v", "ON_ERROR_STOP=1",
                         "-d", target, "-c", "SET ROLE " + owner + ";",
                         "-f", str(bundle / "generated.sql")], text=True,
                        capture_output=True, timeout=120)
                    require(applied.returncode == 0,
                            "documented standalone apply failed: " + applied.stderr)
                    grants = blocks("docs/cookbook/08-no-extension.md", "sql")[0]
                    commands.sql(target, "SET ROLE " + owner + ";" +
                                 grants.replace("application_role", app))
                    standalone_read = commands.sql(target, "SET ROLE " + app + ";SELECT "
                        "NOT EXISTS(SELECT FROM pg_extension WHERE extname='pg_jsontailor')"
                        " AND count(*)>0 FROM api.orders_dv;")
                    require(standalone_read.splitlines()[-1] == "t",
                            "standalone application-role read failed: " + standalone_read)
                    document = {"_id": 900, "status": "runbook", "created": "2026-11-03",
                                "customer": {"customerId": 7}, "items": []}
                    commands.sql(target, "SET ROLE " + app + ";INSERT INTO api.orders_dv(data) VALUES("
                                 + literal(json.dumps(document)) + "::jsonb);")
                    current = json.loads(commands.sql(target, "SET ROLE " + app +
                        ";SELECT data FROM api.orders_dv WHERE _id=900;").splitlines()[-1])
                    current["status"] = "runbook-updated"
                    commands.sql(target, "SET ROLE " + app + ";UPDATE api.orders_dv SET data="
                                 + literal(json.dumps(current)) + "::jsonb WHERE _id=900;")
                    commands.sql(target, "SET ROLE " + app + ";DELETE FROM api.orders_dv WHERE _id=900 "
                                 "AND data#>>'{_metadata,etag}'=(SELECT data#>>'{_metadata,etag}' "
                                 "FROM api.orders_dv WHERE _id=900);")
                    removal = blocks("docs/cookbook/09-remove-and-rollback.md", "sql")[0]
                    commands.sql(target, "SET ROLE " + owner + ";" + removal)
                    reapplied = subprocess.run(
                        [str(commands.pg_bin / "psql"), "-X", "-v", "ON_ERROR_STOP=1",
                         "-d", target, "-c", "SET ROLE " + owner + ";",
                         "-f", str(bundle / "generated.sql")], text=True,
                        capture_output=True, timeout=120)
                    restored_health = commands.sql(target, "SET ROLE " + owner +
                                                   ";SELECT api.orders_dv__check()->>'status';")
                    require(reapplied.returncode == 0 and restored_health.splitlines()[-1] == "OK",
                            "standalone removal/restore failed: " + reapplied.stderr)
                    commands.sql(target, "SET ROLE " + owner + ";" +
                                 grants.replace("application_role", app))
                    restored_document = {"_id": 901, "status": "restored",
                                         "created": "2026-11-03",
                                         "customer": {"customerId": 7}, "items": []}
                    commands.sql(target, "SET ROLE " + app + ";INSERT INTO api.orders_dv(data) VALUES("
                                 + literal(json.dumps(restored_document)) + "::jsonb);")
                    require(commands.sql(target, "SET ROLE " + app +
                            ";SELECT data->>'status' FROM api.orders_dv WHERE _id=901;").splitlines()[-1]
                            == "restored", "standalone restored grants do not permit application reads")
                    commands.sql(target, "SET ROLE " + app + ";DELETE FROM api.orders_dv WHERE _id=901 "
                                 "AND data#>>'{_metadata,etag}'=(SELECT data#>>'{_metadata,etag}' "
                                 "FROM api.orders_dv WHERE _id=901);")
                    require(commands.sql(target, "SELECT NOT EXISTS(SELECT FROM shop.orders "
                                         "WHERE order_id=901);") == "t",
                            "standalone post-restore application smoke did not clean up")
                finally:
                    if target_created:
                        commands.drop(target)
                    commands.sql(commands.maintenance, "DROP ROLE IF EXISTS " + app + ";"
                                 "DROP ROLE IF EXISTS " + owner + ";")

        check("116/no-extension-runbook", no_extension_runbook)

        def object_semantics():
            checked = json.loads(json.dumps(spec))
            checked["object"]["etag"] = True
            commands.sql(database, "SELECT jsontailor.create_duality_view('api.checked_orders',"
                         + literal(json.dumps(checked)) + "::jsonb);")
            for identity, view, enabled in ((201, "orders_dv", False),
                                             (202, "checked_orders", True)):
                candidate = {"_id": identity, "status": "inserted", "created": "2026-11-03",
                             "customer": {"customerId": 7, "name": "not-the-stored-name"},
                             "items": []}
                commands.sql(database, f"INSERT INTO api.{view}(data) VALUES("
                             + literal(json.dumps(candidate)) + "::jsonb);")
                require(commands.sql(database, f"SELECT data#>>'{{customer,name}}' FROM api.{view} "
                                     f"WHERE _id={identity};") == "ACME",
                        "INSERT did not ignore non-key object fields")
                update = (f"UPDATE api.{view} SET data=jsonb_set("
                          "jsonb_set(data,'{status}','\"replaced\"'),"
                          "'{customer,name}','\"edited-name\"') "
                          f"WHERE _id={identity};")
                before = commands.sql(database, f"SELECT data FROM api.{view} WHERE _id={identity};")
                if enabled:
                    expected_failure(commands, database, update, "PT422",
                                     "object fields do not match referenced row")
                    require(commands.sql(database, f"SELECT data FROM api.{view} WHERE _id={identity};")
                            == before, "checked-object rejection changed the document")
                else:
                    commands.sql(database, update)
                    require(commands.sql(database, f"SELECT (data->>'status')||':'||"
                                         f"(data#>>'{{customer,name}}') FROM api.{view} "
                                         f"WHERE _id={identity};") == "replaced:ACME",
                            "unchecked replacement did not ignore edited non-key fields")
                commands.sql(database, f"UPDATE api.{view} SET data=jsonb_set(data,'{{customer}}',"
                             "'{\"customerId\":8,\"name\":\"Globex\"}'::jsonb) "
                             f"WHERE _id={identity};")
                require(commands.sql(database, f"SELECT customer_id FROM shop.orders "
                                     f"WHERE order_id={identity};") == "8",
                        "changing the object key did not change the reference")
            require(commands.sql(database, "SELECT string_agg(name,',' ORDER BY customer_id) "
                                 "FROM shop.customers;") == "ACME,Globex",
                    "document operations wrote referenced object fields")

        check("4/object-field-semantics", object_semantics)

        def object_wording():
            semantics = " ".join((ROOT / "docs/SEMANTICS.md").read_text().split()).lower()
            require("on insert, non-key object fields are checked against the referenced row" not in semantics,
                    "SEMANTICS still claims INSERT checks non-key object fields; executed behaviour ignores them")

        check("4/object-field-wording", object_wording)

        def array_fixture(schema, delete_children):
            commands.sql(database, f"CREATE SCHEMA {schema}; "
                         f"CREATE TABLE {schema}.root(id bigint PRIMARY KEY,label text NOT NULL); "
                         f"CREATE TABLE {schema}.child(id bigint PRIMARY KEY,"
                         f"parent bigint NOT NULL REFERENCES {schema}.root(id),label text NOT NULL); "
                         f"INSERT INTO {schema}.root VALUES(1,'root'); "
                         f"INSERT INTO {schema}.child VALUES(10,1,'child');")
            mapping = {"root": {"table": f"{schema}.root", "key": "id",
                                "fields": {"label": "label"}, "delete": True},
                       "array": {"name": "items", "table": f"{schema}.child", "key": "id",
                                 "parent_fk": "parent", "fields": {"id": "id", "label": "label"},
                                 "delete": delete_children}}
            commands.sql(database, f"SELECT jsontailor.create_duality_view('{schema}.documents',"
                         + literal(json.dumps(mapping)) + "::jsonb);")

        def delete_without_nullable_parent():
            schema = "doc122_unlink"
            array_fixture(schema, False)
            commands.sql(database, f"CREATE INDEX child_parent_key ON {schema}.child(parent,id);")
            before = commands.sql(database, f"SELECT data FROM {schema}.documents WHERE _id=1;")
            expected_failure(commands, database,
                             f"DELETE FROM {schema}.documents WHERE _id=1;", "23502")
            require(commands.sql(database, f"SELECT data FROM {schema}.documents WHERE _id=1;") == before,
                    "native NOT NULL rejection changed the root or children")

        check("5/unlink-not-null", delete_without_nullable_parent)

        def missing_child_index():
            schema = "doc122_index"
            array_fixture(schema, True)
            expected_failure(commands, database, f"SELECT {schema}.documents__check();", "PT422",
                             "valid child parent-FK/order/key index required")
            commands.sql(database, f"CREATE INDEX child_parent_key ON {schema}.child(parent,id);")
            require(commands.sql(database, f"SELECT {schema}.documents__check()->>'status';") == "OK",
                    "health check did not recover after adding the required child index")

        check("7/missing-child-index", missing_child_index)

        def p0_sql_blocks():
            """Execute every SQL fence in the P0 cookbook with its documented bindings."""
            expected_blocks = {
                "01-try-it.md": 1, "02-map-tables.md": 1,
                "04-create-and-verify.md": 1, "05-application-role.md": 2,
                "06-read-list-filter.md": 1, "07-write-etags.md": 1,
                "08-no-extension.md": 2, "09-remove-and-rollback.md": 1,
            }
            sql = {name: blocks("docs/cookbook/" + name, "sql")
                   for name in expected_blocks}
            require({name: len(value) for name, value in sql.items()} == expected_blocks,
                    "P0 SQL block inventory changed")
            commands.sql(database, sql["01-try-it.md"][0])
            commands.sql(database, sql["02-map-tables.md"][0])
            create = sql["04-create-and-verify.md"][0].replace(
                "api.orders_dv", "api.cookbook_orders").replace(
                ":'spec'::jsonb", literal(json.dumps(spec)) + "::jsonb")
            commands.sql(database, create)
            require(commands.sql(database,
                    "SELECT api.cookbook_orders__check()->>'status';") == "OK",
                    "cookbook create/health block failed")
            role = "pgjt_cookbook_app"
            commands.sql(database, "CREATE ROLE " + role + " NOLOGIN;")
            try:
                for grant_block in sql["05-application-role.md"]:
                    commands.sql(database, grant_block.replace("app_role", role))
                acl = commands.sql(database, "SELECT "
                    "has_table_privilege('" + role + "','api.orders_dv','SELECT')"
                    " AND has_table_privilege('" + role + "','api.orders_dv','INSERT')"
                    " AND has_table_privilege('" + role + "','api.orders_dv','UPDATE')"
                    " AND has_table_privilege('" + role + "','api.orders_dv','DELETE')"
                    " AND has_table_privilege('" + role + "','shop.customers','SELECT')"
                    " AND NOT has_table_privilege('" + role + "','shop.customers','INSERT')"
                    " AND NOT has_table_privilege('" + role + "','shop.customers','UPDATE')"
                    " AND NOT has_table_privilege('" + role + "','shop.customers','DELETE')"
                    " AND has_function_privilege('" + role + "',"
                    "'api.orders_dv__bounded_array(bigint)','EXECUTE')"
                    " AND has_function_privilege('" + role + "',"
                    "'api.orders_dv__delete_document(bigint,text)','EXECUTE');")
                require(acl == "t", "cookbook grant matrix is not least privilege")
                commands.sql(database, "SET ROLE " + role + ";" + sql["06-read-list-filter.md"][0])
                before = commands.sql(database, "SELECT data FROM api.orders_dv WHERE _id=42;")
                result = commands.run("psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database,
                    input_text="SET ROLE " + role + ";\n" + sql["07-write-etags.md"][0])
                lines = result.stdout.splitlines()
                require(lines == ["SET", "BEGIN", "SET", "INSERT 0 1", "UPDATE 1", "UPDATE 0",
                                  "t", "UPDATE 1", "DELETE 0", "t", "DELETE 1", "t", "ROLLBACK"],
                        "literal clean-session ETag recipe results differ: " + repr(lines))
                require(commands.sql(database, "SELECT data FROM api.orders_dv WHERE _id=42;") == before,
                        "cookbook changed seeded order 42")
                require(commands.sql(database, "SELECT NOT EXISTS(SELECT FROM shop.orders "
                                     "WHERE order_id=700);") == "t",
                        "cookbook ETag delete did not remove the inserted document")
                for standalone_block in sql["08-no-extension.md"]:
                    commands.sql(database, standalone_block.replace("application_role", role))
                require(commands.sql(database,
                    "SELECT api.orders_dv__check()->>'status';") == "OK",
                    "standalone grant/health cookbook block failed")
            finally:
                commands.sql(database, "DROP OWNED BY " + role + ";DROP ROLE " + role + ";")
            commands.sql(database, sql["09-remove-and-rollback.md"][0])
            require(commands.sql(database, "SELECT to_regclass('api.orders_dv') IS NULL"
                                 " AND to_regclass('shop.orders') IS NOT NULL;") == "t",
                    "cookbook removal did not preserve source tables")

        check("117/p0-sql-blocks", p0_sql_blocks)

        def operational_recipes():
            # Each published recipe gets its own fresh psql session. Their
            # explicit transaction/ROLLBACK prevents fixture-order dependence.
            expected_markers = {
                "11-schema-change.md": ("ALTER TABLE", "OK", "new", "t", "ROLLBACK"),
                "12-merge.md": ("UPDATE|42", "MERGE 0", "DELETE|42", "t", "ROLLBACK"),
                "13-limits.md": ("5s", "1s", "t", "DO", "ROLLBACK"),
            }
            for path in ("11-schema-change.md", "12-merge.md", "13-limits.md"):
                snippets = blocks("docs/cookbook/" + path, "sql")
                require(len(snippets) == 1, "operational recipe SQL inventory changed: " + path)
                output = commands.sql(database, snippets[0])
                lines = output.splitlines()
                require("f" not in lines and all(marker in lines for marker in expected_markers[path]),
                        "recipe expected output inventory differs: " + path + ": " + repr(lines))
                require(commands.sql(database, "SELECT api.orders_dv__check()->>'status';") == "OK",
                        "operational recipe failed post-rollback health: " + path)

        # The removal recipe above deliberately removes orders_dv; recreate the
        # documented prerequisite without rewriting any published recipe SQL.
        commands.sql(database, "SELECT jsontailor.create_duality_view('api.orders_dv',"
                     + literal(json.dumps(spec)) + "::jsonb);")
        check("128/literal-cookbook", operational_recipes)

        def rls_recipe():
            snippets = blocks("docs/cookbook/15-rls.md", "sql")
            require(len(snippets) == 1, "RLS recipe SQL inventory changed")
            output = commands.sql(database, snippets[0])
            lines = output.splitlines()
            require("f" not in lines and lines.count("t") >= 8,
                    "RLS cookbook positive/negative controls differ: " + output)
            require(lines.count("UPDATE 0") == 2 and lines.count("DELETE 0") == 2
                    and lines.count("UPDATE 1") == 2 and "INSERT 0 1" in lines,
                    "RLS cookbook did not execute both tenants and CRUD controls")
            require(commands.sql(database, "SELECT NOT EXISTS(SELECT FROM pg_roles WHERE "
                                 "rolname IN ('pgjt_recipe_tenant_a','pgjt_recipe_tenant_b')) "
                                 "AND to_regnamespace('recipe_rls') IS NULL;") == "t",
                    "RLS cookbook rollback did not remove fixture and roles")

        check("127/rls-cookbook", rls_recipe)

        def deployment_paths():
            sys.path.insert(0, str(ROOT / "tests"))
            from documentation_regressions import pilot_closure_negatives
            pilot_closure_negatives()
            require(outcomes["3/unpadded-ddl-command"]["status"] == "PASS"
                    and outcomes["116/no-extension-runbook"]["status"] == "PASS"
                    and outcomes["117/p0-sql-blocks"]["status"] == "PASS",
                    "one of the two documented deployment paths failed")

        check("126/deployment-paths", deployment_paths)

        def socket_cookbook():
            snippet = next(block for block in blocks("docs/cookbook/10-postgrest-proxy.md", "sh")
                           if "tools/http_boundary.py" in block)
            argv = shlex.split(snippet.replace("\\\n", " "))
            require(argv[:2] == ["python3", "tools/http_boundary.py"],
                    "published boundary command prefix differs")
            # Execute the exact argument vector through the actual parser,
            # replacing only its deployment-specific socket path with an
            # explicitly absent local fixture. Unsafe/missing upstream must
            # fail before any listener starts; unknown CLI options also fail.
            with tempfile.TemporaryDirectory(prefix="pgjt129-socket-") as temporary:
                argv[argv.index("--upstream-socket") + 1] = temporary + "/absent.sock"
                result = subprocess.run([sys.executable, *argv[1:]], cwd=ROOT,
                                        text=True, capture_output=True, timeout=20)
            require(result.returncode != 0 and "unrecognized arguments" not in result.stderr
                    and ("FileNotFoundError" in result.stderr or "ValueError" in result.stderr),
                    "published socket CLI did not parse/fail closed: " + result.stderr)
            regression = subprocess.run([sys.executable, str(ROOT / "tests/http_boundary_regressions.py")],
                                        cwd=ROOT, text=True, capture_output=True, timeout=90)
            require(regression.returncode == 0,
                    "private-socket positive/negative regression failed: " + regression.stderr)

        check("129/socket-cookbook", socket_cookbook)
    except Exception as error:
        outcomes["fixture"] = {"status": "FAIL", "diagnostics": str(error)}
    finally:
        if created:
            commands.drop(database)
    expected = {"1/performance-example", "119/readme-real-document",
                "3/unpadded-ddl-command", "3/ddl-artefact",
                "116/no-extension-runbook", "117/p0-sql-blocks",
                "4/object-field-semantics", "4/object-field-wording", "5/unlink-not-null",
                "7/missing-child-index", "130/deployed-manifest", "128/literal-cookbook",
                "127/rls-cookbook", "126/deployment-paths", "129/socket-cookbook"}
    passed = set(outcomes) == expected and all(row["status"] == "PASS" for row in outcomes.values())
    return {"name": RESULT_IDS[3], "status": "PASS" if passed else "FAIL", "observed": outcomes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    env = os.environ.copy()
    env["PATH"] = str(args.pg_bin.resolve()) + os.pathsep + env["PATH"]
    env["PGDATABASE"] = args.maintenance_database
    result = subprocess.run(["bash", "-c", walkthrough((ROOT / "README.md").read_text())],
                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=90)
    expected = ("quickstart/read=new", "quickstart/update=PASS", "quickstart/delete=PASS",
                "quickstart/cleanup=PASS")
    passed = result.returncode == 0 and all(value in result.stdout for value in expected)
    row = {"name": RESULT_IDS[0], "status": "PASS" if passed else "FAIL",
           "observed": result.stdout, "diagnostics": result.stderr}
    documented = documentation_examples(args.pg_bin, args.maintenance_database)

    def promoted(name: str, key: str) -> dict:
        outcome = documented.get("observed", {}).get(key, {"status": "FAIL"})
        return {"name": name, "status": outcome.get("status", "FAIL"), "observed": outcome}

    rows = [row,
            promoted(RESULT_IDS[1], "116/no-extension-runbook"),
            promoted(RESULT_IDS[2], "117/p0-sql-blocks"), documented,
            promoted(RESULT_IDS[4], "126/deployment-paths"),
            promoted(RESULT_IDS[5], "127/rls-cookbook"),
            promoted(RESULT_IDS[6], "128/literal-cookbook"),
            promoted(RESULT_IDS[7], "129/socket-cookbook"),
            promoted(RESULT_IDS[8], "130/deployed-manifest")]
    if args.output:
        args.output.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in rows))
    for item in rows:
        print(item["name"] + ": " + item["status"])
        if item["status"] != "PASS":
            print(json.dumps(item, sort_keys=True))
    return 0 if all(item["status"] == "PASS" for item in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
