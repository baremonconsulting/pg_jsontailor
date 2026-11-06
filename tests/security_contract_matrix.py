#!/usr/bin/env python3
"""PGJT-46 systematic privilege and row-level-security contract matrix."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ROLE = "jt46_app"
GROUP = "jt46_group"
MEMBER = "jt46_member"
NOINHERIT = "jt46_noinherit"
OWNER = "jt46_owner"
ROLES = (ROLE, MEMBER, NOINHERIT, GROUP, OWNER)


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Database:
    def __init__(self, psql: str, database: str):
        self.command = [psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database]

    def run(self, sql: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(self.command, input="\\set VERBOSITY verbose\n" + sql,
                                text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(result.stderr)
        return result

    def value(self, sql: str) -> str:
        result = self.run(sql)
        lines = result.stdout.strip().splitlines()
        if not lines:
            raise RuntimeError(f"query returned no value: {sql}\n{result.stderr}")
        return lines[-1]

    @staticmethod
    def state(result: subprocess.CompletedProcess[str]) -> str | None:
        match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", result.stderr)
        return match.group(1) if match else None


def specification(etag: bool = True) -> dict:
    return {
        "root": {"table": "jt46.root", "key": "id",
                 "fields": {"label": "label"}, "insert": True,
                 "update": True, "delete": True},
        "object": {"name": "detail", "table": "jt46.detail", "key": "id",
                   "root_fk": "detail_id", "fields": {"id": "id", "name": "name"},
                   "etag": etag},
        "array": {"name": "items", "table": "jt46.item", "key": "id",
                  "parent_fk": "parent", "fields": {"id": "id", "label": "label"},
                  "insert": True, "update": True, "delete": True}}


def cleanup(db: Database) -> None:
    db.run("DROP SCHEMA IF EXISTS jt46 CASCADE;")
    db.run("REVOKE jt46_group FROM jt46_member,jt46_noinherit;", False)
    for role in ROLES:
        db.run(f"DROP ROLE IF EXISTS {role};", False)


def setup(db: Database) -> None:
    cleanup(db)
    db.run(f"""CREATE ROLE {ROLE} NOLOGIN;
CREATE ROLE {GROUP} NOLOGIN;
CREATE ROLE {MEMBER} NOLOGIN INHERIT;
CREATE ROLE {NOINHERIT} NOLOGIN NOINHERIT;
CREATE ROLE {OWNER} NOLOGIN;
GRANT {GROUP} TO {MEMBER},{NOINHERIT};
CREATE SCHEMA jt46;
CREATE TABLE jt46.detail(id integer PRIMARY KEY,name text NOT NULL);
CREATE TABLE jt46.root(id integer PRIMARY KEY,detail_id integer REFERENCES jt46.detail(id),
 label text NOT NULL DEFAULT 'default');
CREATE TABLE jt46.item(id integer PRIMARY KEY,parent integer NOT NULL REFERENCES jt46.root(id),
 label text NOT NULL DEFAULT 'default');
INSERT INTO jt46.detail VALUES(7,'seven'),(8,'eight');
INSERT INTO jt46.root VALUES(1,7,'one'),(2,8,'two');
INSERT INTO jt46.item VALUES(10,1,'ten'),(20,2,'twenty');
SELECT jsontailor.create_duality_view(
 'jt46.documents',{quote(json.dumps(specification(True)))}::jsonb);
SELECT jsontailor.create_duality_view(
 'jt46.documents_nocheck',{quote(json.dumps(specification(False)))}::jsonb);
REVOKE ALL ON SCHEMA jt46 FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA jt46 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA jt46 FROM PUBLIC;""")
    grant_full(db, ROLE)


def grant_full(db: Database, role: str) -> None:
    db.run(f"""GRANT USAGE ON SCHEMA jt46 TO {role};
GRANT SELECT,INSERT,UPDATE,DELETE ON jt46.documents,jt46.documents_nocheck TO {role};
GRANT EXECUTE ON FUNCTION jt46.documents__delete_document(integer,text),
 jt46.documents_nocheck__delete_document(integer,text) TO {role};
GRANT SELECT,INSERT,UPDATE,DELETE ON jt46.root,jt46.item TO {role};
GRANT SELECT ON jt46.detail TO {role};
GRANT UPDATE(name) ON jt46.detail TO {role};""")


def as_role(db: Database, sql: str, role: str = ROLE) -> subprocess.CompletedProcess[str]:
    return db.run(f"SET ROLE {role};{sql}", False)


def document(db: Database, ident: int = 1, view: str = "documents",
             role: str | None = None) -> dict:
    prefix = f"SET ROLE {role};" if role else ""
    return json.loads(db.value(
        f"{prefix}SELECT data::text FROM jt46.{view} WHERE _id={ident};"))


def snapshot(db: Database) -> str:
    return db.value("""SELECT jsonb_build_object(
 'root',(SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM jt46.root r),
 'detail',(SELECT jsonb_agg(to_jsonb(d) ORDER BY id) FROM jt46.detail d),
 'item',(SELECT jsonb_agg(to_jsonb(i) ORDER BY id) FROM jt46.item i))::text;""")


def update_sql(doc: dict, view: str = "documents", ident: int = 1) -> str:
    return (f"UPDATE jt46.{view} SET data={quote(json.dumps(doc))}::jsonb "
            f"WHERE _id={ident} RETURNING _id;")


def merge_sql(doc: dict, action: str, ident: int = 1,
              view: str = "documents") -> str:
    return (f"MERGE INTO jt46.{view} d USING (VALUES({ident},"
            f"{quote(json.dumps(doc))}::jsonb)) s(id,data) ON d._id=s.id "
            f"{action} RETURNING merge_action(),d._id;")


def delete_sql(db: Database, ident: int = 1, view: str = "documents",
               rpc: bool = False) -> str:
    etag = document(db, ident, view)["_metadata"]["etag"]
    if rpc:
        return f"SELECT jt46.{view}__delete_document({ident},{quote(etag)});"
    return (f"DELETE FROM jt46.{view} WHERE _id={ident} AND "
            f"data#>>'{{_metadata,etag}}'={quote(etag)} RETURNING _id;")


def source_privileges(db: Database, record) -> None:
    outcomes = []

    def allowed(name: str, sql_builder, verify, changes: bool = True) -> None:
        setup(db)
        before = snapshot(db)
        result = as_role(db, sql_builder())
        after = snapshot(db)
        outcomes.append((f"allow-{name}", db.state(result),
                         result.returncode == 0 and ((after != before) == changes)
                         and verify(result)))

    def denied(name: str, revoke: str, sql_builder, expected: str = "42501") -> None:
        setup(db)
        db.run(revoke)
        before = snapshot(db)
        result = as_role(db, sql_builder())
        outcomes.append((name, db.state(result), snapshot(db) == before))
        assert db.state(result) == expected

    allowed("select", lambda: "SELECT count(*) FROM jt46.documents;",
            lambda result: result.stdout.strip().splitlines()[-1] == "2", False)
    allowed("insert", lambda: "INSERT INTO jt46.documents(data) VALUES "
            "('{\"_id\":3,\"detail\":{\"id\":7},\"items\":[]}'::jsonb);",
            lambda _result: db.value("SELECT label||':'||detail_id FROM jt46.root WHERE id=3;")
            == "default:7")
    allowed("update", lambda: update_sql({**document(db), "label": "allowed"}),
            lambda _result: db.value("SELECT label FROM jt46.root WHERE id=1;") == "allowed")
    allowed("direct-delete", lambda: delete_sql(db),
            lambda _result: db.value("SELECT count(*) FROM jt46.root WHERE id=1;") == "0")
    allowed("rpc-delete", lambda: delete_sql(db, rpc=True),
            lambda _result: db.value("SELECT count(*) FROM jt46.root WHERE id=1;") == "0")
    allowed("array-insert", lambda: update_sql(
        {**document(db), "items": document(db)["items"] + [{"id": 11, "label": "new"}]}),
        lambda _result: db.value("SELECT parent||':'||label FROM jt46.item WHERE id=11;")
        == "1:new")
    allowed("array-update", lambda: update_sql(
        {**document(db), "items": [{"id": 10, "label": "changed"}]}),
        lambda _result: db.value("SELECT label FROM jt46.item WHERE id=10;") == "changed")
    allowed("array-delete", lambda: update_sql({**document(db), "items": []}),
            lambda _result: db.value("SELECT count(*) FROM jt46.item WHERE parent=1;") == "0")
    allowed("merge-insert", lambda: merge_sql(
        {"_id": 3, "detail": {"id": 7}, "items": []},
        "WHEN MATCHED THEN DO NOTHING WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data)",
        ident=3), lambda _result: db.value("SELECT label FROM jt46.root WHERE id=3;")
        == "default")
    allowed("merge-update", lambda: merge_sql(
        {**document(db), "label": "merge-allowed"},
        "WHEN MATCHED THEN UPDATE SET data=s.data"),
        lambda _result: db.value("SELECT label FROM jt46.root WHERE id=1;")
        == "merge-allowed")
    allowed("merge-delete", lambda: merge_sql(
        document(db), "WHEN MATCHED AND d.data#>>'{_metadata,etag}'="
        "s.data#>>'{_metadata,etag}' THEN DELETE"),
        lambda _result: db.value("SELECT count(*) FROM jt46.root WHERE id=1;") == "0")

    denied("root-select", f"REVOKE SELECT ON jt46.root FROM {ROLE};",
           lambda: "SELECT count(*) FROM jt46.documents;")
    denied("object-select", f"REVOKE SELECT ON jt46.detail FROM {ROLE};",
           lambda: "SELECT data->'detail' FROM jt46.documents;")
    denied("array-select", f"REVOKE SELECT ON jt46.item FROM {ROLE};",
           lambda: "SELECT data->'items' FROM jt46.documents;")
    denied("root-insert", f"REVOKE INSERT ON jt46.root FROM {ROLE};",
           lambda: "INSERT INTO jt46.documents(data) VALUES "
                   "('{\"_id\":3,\"detail\":{\"id\":7},\"items\":[]}'::jsonb);")
    denied("root-update", f"REVOKE UPDATE ON jt46.root FROM {ROLE};",
           lambda: update_sql({**document(db), "label": "blocked"}))
    denied("root-delete", f"REVOKE DELETE ON jt46.root FROM {ROLE};",
           lambda: delete_sql(db))
    denied("array-insert", f"REVOKE INSERT ON jt46.item FROM {ROLE};",
           lambda: update_sql({**document(db), "items":
                               document(db)["items"] + [{"id": 11, "label": "new"}]}))
    denied("array-update", f"REVOKE UPDATE ON jt46.item FROM {ROLE};",
           lambda: update_sql({**document(db), "items": [{"id": 10, "label": "changed"}]}))
    denied("array-delete", f"REVOKE DELETE ON jt46.item FROM {ROLE};",
           lambda: update_sql({**document(db), "items": []}))

    setup(db)
    merge_doc = document(db)
    merge_doc["label"] = "merge"
    db.run(f"REVOKE UPDATE ON jt46.root FROM {ROLE};")
    before = snapshot(db)
    merge_result = as_role(db, merge_sql(
        merge_doc, "WHEN MATCHED THEN UPDATE SET data=s.data"))
    outcomes.append(("merge-update", db.state(merge_result), snapshot(db) == before))

    setup(db)
    db.run(f"REVOKE INSERT ON jt46.root FROM {ROLE};")
    before = snapshot(db)
    merge_insert = as_role(db, merge_sql(
        {"_id": 3, "detail": {"id": 7}, "items": []},
        "WHEN MATCHED THEN DO NOTHING WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data)",
        ident=3))
    outcomes.append(("merge-insert", db.state(merge_insert), snapshot(db) == before))

    setup(db)
    merge_delete_doc = document(db)
    db.run(f"REVOKE DELETE ON jt46.root FROM {ROLE};")
    before = snapshot(db)
    merge_delete = as_role(db, merge_sql(
        merge_delete_doc,
        "WHEN MATCHED AND d.data#>>'{_metadata,etag}'="
        "s.data#>>'{_metadata,etag}' THEN DELETE"))
    outcomes.append(("merge-delete", db.state(merge_delete), snapshot(db) == before))

    setup(db)
    db.run(f"REVOKE DELETE ON jt46.root FROM {ROLE};")
    before = snapshot(db)
    rpc_result = as_role(db, delete_sql(db, rpc=True))
    outcomes.append(("rpc-delete", db.state(rpc_result), snapshot(db) == before))

    expected_names = ("allow-select", "allow-insert", "allow-update",
                      "allow-direct-delete", "allow-rpc-delete", "allow-array-insert",
                      "allow-array-update", "allow-array-delete", "allow-merge-insert",
                      "allow-merge-update", "allow-merge-delete", "root-select",
                      "object-select", "array-select", "root-insert", "root-update",
                      "root-delete", "array-insert", "array-update", "array-delete",
                      "merge-update", "merge-insert", "merge-delete", "rpc-delete")
    record("PGJT-46/source-privilege-matrix",
           tuple(row[0] for row in outcomes) == expected_names
           and all(row[1:] == (None, True) for row in outcomes[:11])
           and all(row[1:] == ("42501", True) for row in outcomes[11:]),
           repr(outcomes))


def membership_column_grants(db: Database, record) -> None:
    setup(db)
    db.run(f"REVOKE ALL ON jt46.detail FROM {ROLE};"
           f"GRANT SELECT ON jt46.detail TO {ROLE};")
    doc = document(db)
    doc["label"] = "blocked"
    denied = as_role(db, update_sql(doc))
    db.run(f"GRANT UPDATE(name) ON jt46.detail TO {ROLE};")
    allowed = as_role(db, update_sql(doc))
    allowed_state = db.value("SELECT label FROM jt46.root WHERE id=1;")
    shape = db.value(f"SELECT has_table_privilege('{ROLE}','jt46.detail','UPDATE')::text||':'||"
                     f"has_column_privilege('{ROLE}','jt46.detail','id','UPDATE')::text||':'||"
                     f"has_column_privilege('{ROLE}','jt46.detail','name','UPDATE')::text;")

    def narrow() -> None:
        db.run(f"""REVOKE ALL ON jt46.root,jt46.item FROM {ROLE};
GRANT SELECT ON jt46.root,jt46.item TO {ROLE};
GRANT INSERT(id,detail_id,label),UPDATE(label) ON jt46.root TO {ROLE};
GRANT INSERT(id,parent,label),UPDATE(label) ON jt46.item TO {ROLE};
GRANT DELETE ON jt46.item TO {ROLE};""")

    column_cases = []
    setup(db); narrow()
    narrow_insert = as_role(db, "INSERT INTO jt46.documents(data) VALUES "
                            "('{\"_id\":3,\"label\":\"three\","
                            "\"detail\":{\"id\":7},\"items\":["
                            "{\"id\":30,\"label\":\"thirty\"}]}'::jsonb);")
    narrow_doc = document(db)
    narrow_doc["label"] = "root-column"
    narrow_doc["items"][0]["label"] = "array-column"
    narrow_update = as_role(db, update_sql(narrow_doc))
    if narrow_insert.returncode or narrow_update.returncode:
        raise RuntimeError("narrow column grants failed: insert="
                           + narrow_insert.stderr + " update=" + narrow_update.stderr)
    narrow_state = db.value("SELECT (SELECT label FROM jt46.root WHERE id=1)||':'||"
                            "(SELECT label FROM jt46.item WHERE id=10)||':'||"
                            "(SELECT label FROM jt46.root WHERE id=3)||':'||"
                            "(SELECT label FROM jt46.item WHERE id=30);")
    column_cases.append(("narrow-positive", narrow_insert.returncode,
                         narrow_update.returncode, narrow_state))

    for name, revoke, operation in (
        ("root-insert", f"REVOKE INSERT(label) ON jt46.root FROM {ROLE};",
         lambda: "INSERT INTO jt46.documents(data) VALUES "
                 "('{\"_id\":3,\"label\":\"three\",\"detail\":{\"id\":7},"
                 "\"items\":[]}'::jsonb);"),
        ("root-update", f"REVOKE UPDATE(label) ON jt46.root FROM {ROLE};",
         lambda: update_sql({**document(db), "label": "blocked"})),
        ("array-insert", f"REVOKE INSERT(label) ON jt46.item FROM {ROLE};",
         lambda: update_sql({**document(db), "items": document(db)["items"]
                             + [{"id": 11, "label": "blocked"}]})),
        ("array-update", f"REVOKE UPDATE(label) ON jt46.item FROM {ROLE};",
         lambda: update_sql({**document(db), "items":
                             [{"id": 10, "label": "blocked"}]})),
    ):
        setup(db); narrow(); db.run(revoke)
        before = snapshot(db)
        result = as_role(db, operation())
        column_cases.append((name, db.state(result), snapshot(db) == before))

    setup(db)
    grant_full(db, GROUP)
    inherited = as_role(db, "SELECT count(*) FROM jt46.documents;", MEMBER)
    noinherit = as_role(db, "SELECT count(*) FROM jt46.documents;", NOINHERIT)
    explicit = db.run(f"SET SESSION AUTHORIZATION {NOINHERIT};SET ROLE {GROUP};"
                      "SELECT current_user||':'||count(*) FROM jt46.documents;", False)
    db.run(f"REVOKE {GROUP} FROM {NOINHERIT};")
    explicit_denied = db.run(
        f"SET SESSION AUTHORIZATION {NOINHERIT};SET ROLE {GROUP};", False)
    record("PGJT-46/membership-column-grants",
           db.state(denied) == "42501" and allowed.returncode == 0
           and allowed_state == "blocked"
           and shape == "false:false:true" and inherited.returncode == 0
           and inherited.stdout.strip().splitlines()[-1] == "2"
           and db.state(noinherit) == "42501" and explicit.returncode == 0
           and f"{GROUP}:2" in explicit.stdout.splitlines()
           and db.state(explicit_denied) == "42501"
           and column_cases[0] == ("narrow-positive", 0, 0,
                                   "root-column:array-column:three:thirty")
           and all(row[1:] == ("42501", True) for row in column_cases[1:]),
           f"column={db.state(denied)}/{allowed.returncode}/{allowed_state}/{shape},"
           f"membership={inherited.returncode}/{db.state(noinherit)}/"
           f"{explicit.returncode}/{db.state(explicit_denied)},"
           f"root_array_columns={column_cases}")


def owner_force_rls(db: Database, record) -> None:
    setup(db)
    grant_full(db, OWNER)
    db.run(f"ALTER TABLE jt46.root OWNER TO {OWNER};"
           "ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           "CREATE POLICY deny_owner_select ON jt46.root FOR SELECT USING(false);")
    owner_bypass = db.value(f"SET ROLE {OWNER};SELECT count(*) FROM jt46.documents;")
    nonowner = db.value(f"SET ROLE {ROLE};SELECT count(*) FROM jt46.documents;")
    db.run("ALTER TABLE jt46.root FORCE ROW LEVEL SECURITY;")
    owner_forced = db.value(f"SET ROLE {OWNER};SELECT count(*) FROM jt46.documents;")
    nonowner_forced = db.value(f"SET ROLE {ROLE};SELECT count(*) FROM jt46.documents;")
    role_shape = db.value(
        f"SELECT bool_and(NOT rolsuper AND NOT rolbypassrls)::text FROM pg_roles "
        f"WHERE rolname IN({','.join(quote(role) for role in ROLES)});")
    record("PGJT-46/owner-force-rls",
           (owner_bypass, nonowner, owner_forced, nonowner_forced)
           == ("2", "0", "0", "0") and role_shape == "true",
           f"owner={owner_bypass}/{owner_forced},"
           f"nonowner={nonowner}/{nonowner_forced},roles={role_shape}")


def root_rls(db: Database, record) -> None:
    outcomes = []
    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY allow_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY allow_insert ON jt46.root FOR INSERT TO {ROLE} WITH CHECK(true);"
           f"CREATE POLICY allow_update ON jt46.root FOR UPDATE TO {ROLE} "
           "USING(true) WITH CHECK(true);"
           f"CREATE POLICY allow_delete ON jt46.root FOR DELETE TO {ROLE} USING(true);")
    allow_insert = as_role(db, "INSERT INTO jt46.documents(data) VALUES "
                           "('{\"_id\":3,\"detail\":{\"id\":7},\"items\":[]}'::jsonb);")
    allow_insert_state = db.value("SELECT label FROM jt46.root WHERE id=3;")
    allow_doc = document(db); allow_doc["label"] = "rls-allowed"
    allow_update = as_role(db, update_sql(allow_doc))
    allow_merge = as_role(db, merge_sql(
        {"_id": 4, "detail": {"id": 7}, "items": []},
        "WHEN MATCHED THEN DO NOTHING WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data)",
        ident=4))
    allow_merge_state = db.value("SELECT label FROM jt46.root WHERE id=4;")
    allow_delete = as_role(db, delete_sql(db, 3, rpc=True))
    allow_state = db.value("SELECT (SELECT label FROM jt46.root WHERE id=1)||':'||"
                           "(SELECT count(*) FROM jt46.root WHERE id=3)||':'||"
                           "(SELECT count(*) FROM jt46.root WHERE id=4);")
    outcomes.append(("allow-active", allow_insert.returncode, allow_insert_state,
                     allow_update.returncode, allow_merge.returncode, allow_merge_state,
                     allow_delete.returncode, allow_state))

    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY root_hidden ON jt46.root FOR SELECT TO {ROLE} USING(false);")
    outcomes.append(("select-hidden", db.value(
        f"SET ROLE {ROLE};SELECT count(*) FROM jt46.documents;")))

    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY root_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY root_insert ON jt46.root FOR INSERT TO {ROLE} WITH CHECK(false);")
    inserted = as_role(db, "INSERT INTO jt46.documents(data) VALUES "
                       "('{\"_id\":3,\"detail\":{\"id\":7},\"items\":[]}'::jsonb);")
    outcomes.append(("insert-check", db.state(inserted)))

    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY root_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY root_update ON jt46.root FOR UPDATE TO {ROLE} USING(false);")
    doc = document(db)
    doc["label"] = "blocked"
    updated = as_role(db, update_sql(doc))
    merged = as_role(db, merge_sql(doc, "WHEN MATCHED THEN UPDATE SET data=s.data"))
    outcomes.extend((("update-using", db.state(updated)), ("merge-update", db.state(merged))))

    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY root_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY root_update ON jt46.root FOR UPDATE TO {ROLE} "
           "USING(true) WITH CHECK(label<>'blocked');")
    checked_doc = document(db)
    checked_doc["label"] = "blocked"
    with_check = as_role(db, update_sql(checked_doc))
    outcomes.append(("update-check", db.state(with_check)))

    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY root_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY root_insert ON jt46.root FOR INSERT TO {ROLE} WITH CHECK(false);")
    merge_insert = as_role(db, merge_sql(
        {"_id": 3, "detail": {"id": 7}, "items": []},
        "WHEN MATCHED THEN DO NOTHING WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data)",
        ident=3))
    outcomes.append(("merge-insert", db.state(merge_insert)))

    setup(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY root_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY root_update ON jt46.root FOR UPDATE TO {ROLE} USING(true);"
           f"CREATE POLICY root_delete ON jt46.root FOR DELETE TO {ROLE} USING(false);")
    direct = as_role(db, delete_sql(db))
    rpc = as_role(db, delete_sql(db, rpc=True))
    merge_delete = as_role(db, merge_sql(
        document(db), "WHEN MATCHED AND d.data#>>'{_metadata,etag}'="
        "s.data#>>'{_metadata,etag}' THEN DELETE"))
    outcomes.extend((("direct-delete", db.state(direct)), ("rpc-delete", db.state(rpc)),
                     ("merge-delete", db.state(merge_delete))))
    expected = (("allow-active", 0, "default", 0, 0, "default", 0,
                 "rls-allowed:0:1"),
                ("select-hidden", "0"), ("insert-check", "42501"),
                ("update-using", "42501"), ("merge-update", "42501"),
                ("update-check", "42501"), ("merge-insert", "42501"),
                ("direct-delete", "42501"), ("rpc-delete", "42501"),
                ("merge-delete", "42501"))
    record("PGJT-46/root-rls-matrix", tuple(outcomes) == expected, repr(outcomes))


def object_etag_rls(db: Database, record) -> None:
    outcomes = []

    def run_case(name: str, view: str, operation) -> None:
        setup(db)
        db.run("ALTER TABLE jt46.detail ENABLE ROW LEVEL SECURITY;"
               f"CREATE POLICY object_select ON jt46.detail FOR SELECT TO {ROLE} USING(true);"
               f"CREATE POLICY object_update ON jt46.detail FOR UPDATE TO {ROLE} USING(false);")
        doc = document(db, view=view)
        before = snapshot(db)
        result = as_role(db, operation(doc, view))
        effect = (db.value("SELECT label FROM jt46.root WHERE id=1;")
                  in ("checked", "nocheck") if name in ("update", "merge-update")
                  else db.value("SELECT count(*) FROM jt46.root WHERE id=1;") == "0")
        outcomes.append((name, view, db.state(result), result.returncode,
                         snapshot(db) == before, effect))

    run_case("update", "documents",
             lambda doc, view: update_sql({**doc, "label": "checked"}, view))
    run_case("update", "documents_nocheck",
             lambda doc, view: update_sql({**doc, "label": "nocheck"}, view))
    run_case("direct-delete", "documents",
             lambda _doc, view: delete_sql(db, view=view))
    run_case("direct-delete", "documents_nocheck",
             lambda _doc, view: delete_sql(db, view=view))
    run_case("rpc-delete", "documents",
             lambda _doc, view: delete_sql(db, view=view, rpc=True))
    run_case("rpc-delete", "documents_nocheck",
             lambda _doc, view: delete_sql(db, view=view, rpc=True))
    run_case("merge-update", "documents",
             lambda doc, view: merge_sql({**doc, "label": "checked"},
                                         "WHEN MATCHED THEN UPDATE SET data=s.data",
                                         view=view))
    run_case("merge-update", "documents_nocheck",
             lambda doc, view: merge_sql({**doc, "label": "nocheck"},
                                         "WHEN MATCHED THEN UPDATE SET data=s.data",
                                         view=view))
    run_case("merge-delete", "documents",
             lambda doc, view: merge_sql(
                 doc, "WHEN MATCHED AND d.data#>>'{_metadata,etag}'="
                 "s.data#>>'{_metadata,etag}' THEN DELETE", view=view))
    run_case("merge-delete", "documents_nocheck",
             lambda doc, view: merge_sql(
                 doc, "WHEN MATCHED AND d.data#>>'{_metadata,etag}'="
                 "s.data#>>'{_metadata,etag}' THEN DELETE", view=view))

    setup(db)
    db.run("ALTER TABLE jt46.detail ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY object_select ON jt46.detail FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY object_update ON jt46.detail FOR UPDATE TO {ROLE} USING(true);")
    allowed_doc = document(db); allowed_doc["label"] = "rls-object-allowed"
    rls_allowed = as_role(db, update_sql(allowed_doc))
    rls_allowed_state = db.value("SELECT label FROM jt46.root WHERE id=1;")

    setup(db)
    db.run("ALTER TABLE jt46.detail ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY object_hidden ON jt46.detail FOR SELECT TO {ROLE} USING(false);")
    hidden = document(db, role=ROLE)
    hidden["label"] = "preserved"
    hidden_update = as_role(db, update_sql(hidden))
    fk = db.value("SELECT detail_id||':'||label FROM jt46.root WHERE id=1;")
    checked = [row for row in outcomes if row[1] == "documents"]
    nocheck = [row for row in outcomes if row[1] == "documents_nocheck"]
    record("PGJT-46/object-etag-lock-matrix",
           len(checked) == len(nocheck) == 5
           and all(row[2] == "42501" and row[4] for row in checked)
           and all(row[2] is None and row[3] == 0 and not row[4] and row[5]
                   for row in nocheck)
           and rls_allowed.returncode == 0
           and rls_allowed_state == "rls-object-allowed"
           and hidden["detail"] is None and hidden_update.returncode == 0
           and fk == "7:preserved",
           f"matrix={outcomes},rls_allow={rls_allowed.returncode}/{rls_allowed_state},"
           f"hidden={hidden['detail']}/{hidden_update.returncode}/{fk}")


def array_rls(db: Database, record) -> None:
    outcomes = []
    setup(db)
    db.run("ALTER TABLE jt46.item ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY allow_select ON jt46.item FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY allow_insert ON jt46.item FOR INSERT TO {ROLE} WITH CHECK(true);"
           f"CREATE POLICY allow_update ON jt46.item FOR UPDATE TO {ROLE} "
           "USING(true) WITH CHECK(true);"
           f"CREATE POLICY allow_delete ON jt46.item FOR DELETE TO {ROLE} USING(true);")
    allow_doc = document(db)
    allow_doc["items"].append({"id": 11, "label": "inserted"})
    allow_insert = as_role(db, update_sql(allow_doc))
    allow_insert_state = db.value("SELECT parent||':'||label FROM jt46.item WHERE id=11;")
    allow_doc = document(db); allow_doc["items"][0]["label"] = "updated"
    allow_update = as_role(db, update_sql(allow_doc))
    allow_update_state = db.value("SELECT label FROM jt46.item WHERE id=10;")
    allow_doc = document(db); allow_doc["items"] = []
    allow_delete = as_role(db, update_sql(allow_doc))
    allow_state = db.value("SELECT count(*) FROM jt46.item WHERE parent=1;")
    outcomes.append(("allow-active", allow_insert.returncode, allow_insert_state,
                     allow_update.returncode, allow_update_state,
                     allow_delete.returncode, allow_state))

    setup(db)
    db.run("ALTER TABLE jt46.item ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY item_select ON jt46.item FOR SELECT TO {ROLE} USING(id<>10);"
           f"CREATE POLICY item_update ON jt46.item FOR UPDATE TO {ROLE} USING(true);"
           f"CREATE POLICY item_delete ON jt46.item FOR DELETE TO {ROLE} USING(true);")
    hidden = document(db, role=ROLE)
    hidden["label"] = "root-only"
    preserved = as_role(db, update_sql(hidden))
    hidden_state = db.value("SELECT label FROM jt46.item WHERE id=10;")
    hidden_root = db.value("SELECT label FROM jt46.root WHERE id=1;")
    outcomes.append(("hidden-preserved", len(hidden["items"]), preserved.returncode,
                     hidden_state, hidden_root))

    setup(db)
    db.run("ALTER TABLE jt46.item ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY item_select ON jt46.item FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY item_update ON jt46.item FOR UPDATE TO {ROLE} USING(false);"
           f"CREATE POLICY item_delete ON jt46.item FOR DELETE TO {ROLE} USING(true);")
    changed_doc = document(db)
    changed_doc["items"][0]["label"] = "blocked"
    changed = as_role(db, update_sql(changed_doc))
    outcomes.append(("nonlockable-update", db.state(changed)))

    setup(db)
    db.run("ALTER TABLE jt46.item ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY item_select ON jt46.item FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY item_insert ON jt46.item FOR INSERT TO {ROLE} WITH CHECK(false);"
           f"CREATE POLICY item_update ON jt46.item FOR UPDATE TO {ROLE} USING(true);"
           f"CREATE POLICY item_delete ON jt46.item FOR DELETE TO {ROLE} USING(true);")
    insert_doc = document(db)
    insert_doc["items"].append({"id": 11, "label": "blocked"})
    inserted = as_role(db, update_sql(insert_doc))
    outcomes.append(("insert-check", db.state(inserted)))

    setup(db)
    db.run("ALTER TABLE jt46.item ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY item_select ON jt46.item FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY item_update ON jt46.item FOR UPDATE TO {ROLE} USING(true);"
           f"CREATE POLICY item_delete ON jt46.item FOR DELETE TO {ROLE} USING(false);")
    delete_doc = document(db)
    delete_doc["items"] = []
    deleted = as_role(db, update_sql(delete_doc))
    outcomes.append(("delete-using", db.state(deleted)))
    record("PGJT-46/array-rls-matrix",
           outcomes == [("allow-active", 0, "1:inserted", 0, "updated", 0, "0"),
                        ("hidden-preserved", 0, 0, "ten", "root-only"),
                        ("nonlockable-update", "42501"),
                        ("insert-check", "42501"), ("delete-using", "42501")],
           repr(outcomes))


def policy_change_atomicity(db: Database, record) -> None:
    setup(db)
    stale = document(db)
    before = snapshot(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY changed_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY changed_update ON jt46.root FOR UPDATE TO {ROLE} USING(false);")
    stale["label"] = "must-not-change"
    changed_update = as_role(db, update_sql(stale))
    unchanged_update = snapshot(db) == before

    setup(db)
    delete_direct = delete_sql(db)
    delete_rpc = delete_sql(db, rpc=True)
    before_delete = snapshot(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY changed_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY changed_update ON jt46.root FOR UPDATE TO {ROLE} USING(false);"
           f"CREATE POLICY changed_delete ON jt46.root FOR DELETE TO {ROLE} USING(true);")
    direct = as_role(db, delete_direct)
    rpc = as_role(db, delete_rpc)
    unchanged_delete = snapshot(db) == before_delete

    setup(db)
    before_multi = snapshot(db)
    one = document(db, 1); one["label"] = "changed-one"
    two = document(db, 2); two["label"] = "changed-two"
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY multi_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY multi_update ON jt46.root FOR UPDATE TO {ROLE} "
           "USING(id=1) WITH CHECK(id=1);")
    multi = as_role(db, "UPDATE jt46.documents SET data=CASE WHEN _id=1 THEN "
                    f"{quote(json.dumps(one))}::jsonb ELSE {quote(json.dumps(two))}::jsonb END "
                    "WHERE _id IN(1,2);")
    unchanged_multi = snapshot(db) == before_multi

    setup(db)
    before_insert = snapshot(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY insert_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY insert_check ON jt46.root FOR INSERT TO {ROLE} WITH CHECK(id<>4);")
    insert_docs = (
        "('{\"_id\":3,\"detail\":{\"id\":7},\"items\":[]}'::jsonb),"
        "('{\"_id\":4,\"detail\":{\"id\":7},\"items\":[]}'::jsonb)")
    multi_insert = as_role(db, "INSERT INTO jt46.documents(data) VALUES " + insert_docs + ";")
    unchanged_insert = snapshot(db) == before_insert

    setup(db)
    before_merge = snapshot(db)
    db.run("ALTER TABLE jt46.root ENABLE ROW LEVEL SECURITY;"
           f"CREATE POLICY merge_select ON jt46.root FOR SELECT TO {ROLE} USING(true);"
           f"CREATE POLICY merge_insert ON jt46.root FOR INSERT TO {ROLE} WITH CHECK(id<>4);")
    merge_source = (
        "MERGE INTO jt46.documents d USING (VALUES"
        "(3,'{\"_id\":3,\"detail\":{\"id\":7},\"items\":[]}'::jsonb),"
        "(4,'{\"_id\":4,\"detail\":{\"id\":7},\"items\":[]}'::jsonb)) s(id,data) "
        "ON d._id=s.id WHEN MATCHED THEN DO NOTHING "
        "WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data);")
    multi_merge = as_role(db, merge_source)
    unchanged_merge = snapshot(db) == before_merge
    record("PGJT-46/policy-change-atomic-denial",
           db.state(changed_update) == "42501" and unchanged_update
           and db.state(direct) == "42501" and db.state(rpc) == "42501"
           and unchanged_delete and db.state(multi) == "42501" and unchanged_multi
           and db.state(multi_insert) == "42501" and unchanged_insert
           and db.state(multi_merge) == "42501" and unchanged_merge,
           f"changed={db.state(changed_update)}/{unchanged_update},"
           f"delete={db.state(direct)}/{db.state(rpc)}/{unchanged_delete},"
           f"multi={db.state(multi)}/{unchanged_multi},"
           f"insert={db.state(multi_insert)}/{unchanged_insert},"
           f"merge={db.state(multi_merge)}/{unchanged_merge}")


def invoker_metadata(db: Database, record) -> None:
    setup(db)
    views = db.value("""SELECT count(*)::text||':'||
 bool_and(reloptions @> ARRAY['security_invoker=true'])::text
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='jt46' AND c.relkind='v';""")
    functions = db.value("""SELECT count(*)::text||':'||bool_and(NOT prosecdef)::text
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='jt46';""")
    mains = db.value("""SELECT string_agg(relname,',' ORDER BY relname)
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='jt46' AND c.relname IN('documents','documents_nocheck')
 AND c.relkind='v' AND c.reloptions @> ARRAY['security_invoker=true'];""")
    deletes = db.value("""SELECT string_agg(proname,',' ORDER BY proname)
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='jt46' AND proname IN(
 'documents__delete_document','documents_nocheck__delete_document')
 AND NOT prosecdef;""")
    record("PGJT-46/invoker-metadata",
           views == "8:true" and functions == "12:true"
           and mains == "documents,documents_nocheck"
           and deletes == "documents__delete_document,documents_nocheck__delete_document",
           f"views={views}/{mains},functions={functions}/{deletes}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    results = []

    def record(name: str, passed: bool, observed: str) -> None:
        row = {"name": name, "status": "PASS" if passed else "FAIL",
               "observed": observed}
        results.append(row)
        print(f"{row['status']}: {name} ({observed})")

    try:
        source_privileges(db, record)
        membership_column_grants(db, record)
        owner_force_rls(db, record)
        root_rls(db, record)
        object_etag_rls(db, record)
        array_rls(db, record)
        policy_change_atomicity(db, record)
        invoker_metadata(db, record)
    finally:
        cleanup(db)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                   for row in results))
    return 0 if len(results) == 8 and all(row["status"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
