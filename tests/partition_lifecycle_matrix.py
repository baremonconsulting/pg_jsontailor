#!/usr/bin/env python3
"""PGJT-47 declarative partition and lifecycle contract matrix."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


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


SPEC = {
    "root": {"table": "jt47.root", "key": "id", "fields": {"label": "label"},
             "insert": True, "update": True, "delete": True},
    "object": {"name": "detail", "table": "jt47.detail", "key": "id",
               "root_fk": "detail_id", "fields": {"id": "id", "label": "label"},
               "etag": True},
    "array": {"name": "items", "table": "jt47.item", "key": "id",
              "parent_fk": "parent", "fields": {"id": "id", "label": "label"},
              "insert": True, "update": True, "delete": True},
}


def setup(db: Database) -> None:
    db.run(f"""DROP SCHEMA IF EXISTS jt47 CASCADE; CREATE SCHEMA jt47;
CREATE TABLE jt47.detail(id integer PRIMARY KEY,label text NOT NULL)
 PARTITION BY RANGE(id);
CREATE TABLE jt47.detail_low PARTITION OF jt47.detail FOR VALUES FROM(0) TO(100);
CREATE TABLE jt47.root(id integer PRIMARY KEY,detail_id integer REFERENCES jt47.detail(id),
 label text NOT NULL DEFAULT 'root-default') PARTITION BY RANGE(id);
CREATE TABLE jt47.root_low PARTITION OF jt47.root FOR VALUES FROM(0) TO(100);
CREATE TABLE jt47.item(id integer PRIMARY KEY,parent integer NOT NULL REFERENCES jt47.root(id),
 label text NOT NULL DEFAULT 'item-default') PARTITION BY RANGE(id);
CREATE TABLE jt47.item_low PARTITION OF jt47.item FOR VALUES FROM(0) TO(100);
CREATE INDEX item_parent_order ON jt47.item(parent,id);
INSERT INTO jt47.detail VALUES(7,'seven'),(8,'eight');
INSERT INTO jt47.root VALUES(1,7,'one'),(2,8,'two');
INSERT INTO jt47.item VALUES(10,1,'ten'),(20,2,'twenty');
SELECT jsontailor.create_duality_view(
 'jt47.documents',{quote(json.dumps(SPEC))}::jsonb);""")


def document(db: Database, ident: int) -> dict:
    return json.loads(db.value(
        f"SELECT data::text FROM jt47.documents WHERE _id={ident};"))


def snapshot(db: Database) -> str:
    return db.value("""SELECT jsonb_build_object(
 'root',(SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM jt47.root r),
 'detail',(SELECT jsonb_agg(to_jsonb(d) ORDER BY id) FROM jt47.detail d),
 'item',(SELECT jsonb_agg(to_jsonb(i) ORDER BY id) FROM jt47.item i))::text;""")


def health(db: Database) -> str:
    return db.value("""SELECT health_status||':'||(generator_hash=current_generator_hash)::text
 FROM jsontailor.duality_view_inventory() WHERE view_name='jt47.documents';""")


def attach(db: Database, role: str) -> None:
    db.run(f"CREATE TABLE jt47.{role}_high (LIKE jt47.{role} INCLUDING ALL);"
           f"ALTER TABLE jt47.{role} ATTACH PARTITION jt47.{role}_high "
           "FOR VALUES FROM(100) TO(200);")


def partition_role_transitions(db: Database, record) -> None:
    outcomes = []
    for role, row in (
        ("detail", "INSERT INTO jt47.detail VALUES(107,'object-high');"),
        ("root", "INSERT INTO jt47.root VALUES(107,7,'root-high');"),
        ("item", "INSERT INTO jt47.item VALUES(107,1,'item-high');"),
    ):
        setup(db)
        attach(db, role)
        db.run(row)
        attached_leaf = db.value(
            f"SELECT tableoid::regclass::text FROM jt47.{role} WHERE id=107;")
        before_health = health(db)
        db.run(f"ALTER TABLE jt47.{role} DETACH PARTITION jt47.{role}_high;")
        detached = db.value(
            f"SELECT count(*)::text||':'||(SELECT count(*) FROM jt47.{role}_high)||':'||"
            f"(SELECT count(*) FROM jt47.{role} WHERE id=107) "
            f"FROM pg_inherits WHERE inhrelid='jt47.{role}_high'::regclass;")
        detached_health = health(db)
        db.run(f"ALTER TABLE jt47.{role} ATTACH PARTITION jt47.{role}_high "
               "FOR VALUES FROM(100) TO(200);")
        reattached_leaf = db.value(
            f"SELECT tableoid::regclass::text FROM jt47.{role} WHERE id=107;")
        outcomes.append((role, attached_leaf, before_health, detached, detached_health,
                         reattached_leaf, health(db)))
    expected = tuple((role, f"jt47.{role}_high", "OK:true", "0:1:0", "OK:true",
                      f"jt47.{role}_high", "OK:true")
                     for role in ("detail", "root", "item"))
    record("PGJT-47/partition-role-transitions", tuple(outcomes) == expected,
           repr(outcomes))


def missing_partition_recovery(db: Database, record) -> None:
    setup(db)
    before = snapshot(db)
    root_doc = {"_id": 150, "label": "root-high", "detail": {"id": 7}, "items": []}
    missing_root = db.run("INSERT INTO jt47.documents(data) VALUES(" +
                          quote(json.dumps(root_doc)) + "::jsonb);", False)
    root_atomic = snapshot(db) == before
    attach(db, "root")
    recovered_root = db.run("INSERT INTO jt47.documents(data) VALUES(" +
                            quote(json.dumps(root_doc)) + "::jsonb);", False)
    root_leaf = db.value("SELECT tableoid::regclass::text||':'||label "
                         "FROM jt47.root WHERE id=150;")

    setup(db)
    before = snapshot(db)
    array_doc = document(db, 1)
    array_doc["label"] = "must-rollback"
    array_doc["items"].append({"id": 150, "label": "array-high"})
    missing_array = db.run("UPDATE jt47.documents SET data=" +
                           quote(json.dumps(array_doc)) + "::jsonb WHERE _id=1;", False)
    array_atomic = snapshot(db) == before
    attach(db, "item")
    recovered_array = db.run("UPDATE jt47.documents SET data=" +
                             quote(json.dumps(array_doc)) + "::jsonb WHERE _id=1;", False)
    array_leaf = db.value("SELECT tableoid::regclass::text||':'||label "
                          "FROM jt47.item WHERE id=150;")

    setup(db)
    before = snapshot(db)
    missing_object = db.run("INSERT INTO jt47.detail VALUES(150,'object-high');", False)
    object_atomic = snapshot(db) == before
    attach(db, "detail")
    recovered_object = db.run("INSERT INTO jt47.detail VALUES(150,'object-high');", False)
    object_leaf = db.value("SELECT tableoid::regclass::text||':'||label "
                           "FROM jt47.detail WHERE id=150;")
    observed = (db.state(missing_root), root_atomic, recovered_root.returncode, root_leaf,
                db.state(missing_array), array_atomic, recovered_array.returncode, array_leaf,
                db.state(missing_object), object_atomic, recovered_object.returncode, object_leaf)
    expected = ("23514", True, 0, "jt47.root_high:root-high",
                "PT422", True, 0, "jt47.item_high:array-high",
                "23514", True, 0, "jt47.detail_high:object-high")
    record("PGJT-47/missing-partition-recovery", observed == expected, repr(observed))


def post_change_operations(db: Database, record) -> None:
    setup(db)
    for role in ("detail", "root", "item"):
        attach(db, role)
    db.run("INSERT INTO jt47.detail VALUES(107,'high-detail');")
    inserted = {"_id": 150, "label": "inserted", "detail": {"id": 107},
                "items": [{"id": 150, "label": "inserted-item"}]}
    insert_result = db.run("INSERT INTO jt47.documents(data) VALUES(" +
                           quote(json.dumps(inserted)) + "::jsonb);", False)
    readback = document(db, 150)
    readback["label"] = "updated"
    readback["items"][0]["label"] = "updated-item"
    update_result = db.run("UPDATE jt47.documents SET data=" +
                           quote(json.dumps(readback)) + "::jsonb WHERE _id=150;", False)
    updated = document(db, 150)
    leaves = db.value("SELECT (SELECT tableoid::regclass::text FROM jt47.root WHERE id=150)||':'||"
                      "(SELECT tableoid::regclass::text FROM jt47.item WHERE id=150);")
    merge_insert_doc = {"_id": 151, "label": "merge-insert", "detail": {"id": 107},
                        "items": [{"id": 151, "label": "merge-item"}]}
    merge_insert = db.run(
        "MERGE INTO jt47.documents d USING (VALUES(151," +
        quote(json.dumps(merge_insert_doc)) + "::jsonb))s(id,data) ON d._id=s.id "
        "WHEN MATCHED THEN DO NOTHING WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data);", False)
    merge_inserted = document(db, 151)
    merge_insert_state = (merge_inserted["label"], merge_inserted["detail"]["label"],
                          merge_inserted["items"][0]["label"])
    merge_doc = document(db, 151); merge_doc["label"] = "merge-update"
    merge_update = db.run(
        "MERGE INTO jt47.documents d USING (VALUES(151," +
        quote(json.dumps(merge_doc)) + "::jsonb))s(id,data) ON d._id=s.id "
        "WHEN MATCHED THEN UPDATE SET data=s.data;", False)
    merge_update_state = document(db, 151)["label"]
    merge_doc = document(db, 151)
    merge_delete = db.run(
        "MERGE INTO jt47.documents d USING (VALUES(151," +
        quote(json.dumps(merge_doc)) + "::jsonb))s(id,data) ON d._id=s.id "
        "WHEN MATCHED AND d.data#>>'{_metadata,etag}'=s.data#>>'{_metadata,etag}' "
        "THEN DELETE;", False)
    rpc_doc = {"_id": 152, "label": "rpc-delete", "detail": {"id": 107},
               "items": [{"id": 152, "label": "rpc-item"}]}
    rpc_insert = db.run("INSERT INTO jt47.documents(data) VALUES(" +
                        quote(json.dumps(rpc_doc)) + "::jsonb);", False)
    rpc_etag = document(db, 152)["_metadata"]["etag"]
    rpc_delete = db.run("SELECT jt47.documents__delete_document(152," +
                        quote(rpc_etag) + ");", False)
    etag = document(db, 150)["_metadata"]["etag"]
    direct_delete = db.run("DELETE FROM jt47.documents WHERE _id=150 AND "
                           "data#>>'{_metadata,etag}'=" + quote(etag) + ";", False)
    final_state = db.value("SELECT count(*)::text||':'||"
                           "(SELECT count(*) FROM jt47.item WHERE id IN(150,151)) "
                           "FROM jt47.root WHERE id IN(150,151,152);")
    record("PGJT-47/post-change-operations",
           all(result.returncode == 0 for result in
               (insert_result, update_result, merge_insert, merge_update,
                merge_delete, rpc_insert, rpc_delete, direct_delete))
           and updated["label"] == "updated"
           and updated["items"][0]["label"] == "updated-item"
           and merge_insert_state == ("merge-insert", "high-detail", "merge-item")
           and merge_update_state == "merge-update"
           and final_state == "0:0" and leaves == "jt47.root_high:jt47.item_high",
           f"rc={[r.returncode for r in (insert_result,update_result,merge_insert,merge_update,merge_delete,rpc_insert,rpc_delete,direct_delete)]},"
           f"updated={updated['label']}/{updated['items'][0]['label']},"
           f"merge={merge_insert_state}/{merge_update_state},"
           f"final={final_state},leaves={leaves}")


def health_regeneration(db: Database, record) -> None:
    setup(db)
    initial = health(db)
    attach(db, "root")
    after_attach = health(db)
    db.run("INSERT INTO jt47.documents(data) VALUES "
           "('{\"_id\":150,\"label\":\"before-regen\","
           "\"detail\":{\"id\":7},\"items\":[]}'::jsonb);")
    before_regen = document(db, 150)["label"]
    regenerated = db.run("SELECT jsontailor.regenerate_duality_view('jt47.documents');", False)
    after_regen = health(db)
    doc = document(db, 150); doc["label"] = "after-regen"
    updated = db.run("UPDATE jt47.documents SET data=" + quote(json.dumps(doc)) +
                     "::jsonb WHERE _id=150;", False)
    final = db.value("SELECT label||':'||tableoid::regclass::text FROM jt47.root WHERE id=150;")
    observed = (initial, after_attach, before_regen, regenerated.returncode,
                after_regen, updated.returncode, final)
    expected = ("OK:true", "OK:true", "before-regen", 0,
                "OK:true", 0, "after-regen:jt47.root_high")
    record("PGJT-47/partition-health-regeneration", observed == expected, repr(observed))


def classic_inherits_rejection(db: Database, record) -> None:
    states = []
    for role in ("root", "object", "array"):
        db.run("DROP SCHEMA IF EXISTS jt47_inherits CASCADE; CREATE SCHEMA jt47_inherits;"
               "CREATE TABLE jt47_inherits.root(id integer PRIMARY KEY,obj integer,label text);"
               "CREATE TABLE jt47_inherits.object(id integer PRIMARY KEY,label text);"
               "CREATE TABLE jt47_inherits.array(id integer PRIMARY KEY,parent integer,label text);")
        table = {"root": "root", "object": "object", "array": "array"}[role]
        db.run(f"CREATE TABLE jt47_inherits.{table}_child() "
               f"INHERITS(jt47_inherits.{table});")
        spec = {"root": {"table": "jt47_inherits.root", "key": "id",
                         "fields": {"label": "label"}, "update": True}}
        if role == "object":
            db.run("ALTER TABLE jt47_inherits.root ADD CONSTRAINT root_object_fk "
                   "FOREIGN KEY(obj) REFERENCES jt47_inherits.object(id);")
            spec["object"] = {"name": "object", "table": "jt47_inherits.object",
                              "key": "id", "root_fk": "obj",
                              "fields": {"id": "id", "label": "label"}}
        if role == "array":
            db.run("ALTER TABLE jt47_inherits.array ADD CONSTRAINT array_root_fk "
                   "FOREIGN KEY(parent) REFERENCES jt47_inherits.root(id);")
            spec["array"] = {"name": "items", "table": "jt47_inherits.array",
                             "key": "id", "parent_fk": "parent",
                             "fields": {"id": "id", "label": "label"},
                             "insert": True, "update": True, "delete": True}
        result = db.run("SELECT jsontailor.duality_view_ddl('jt47_inherits.documents'," +
                        quote(json.dumps(spec)) + "::jsonb);", False)
        states.append((role, db.state(result)))
    for role in ("root", "object", "array"):
        db.run("""DROP SCHEMA IF EXISTS jt47_late CASCADE; CREATE SCHEMA jt47_late;
CREATE TABLE jt47_late.object(id integer PRIMARY KEY,label text);
CREATE TABLE jt47_late.root(id integer PRIMARY KEY,obj integer REFERENCES jt47_late.object(id),label text);
CREATE TABLE jt47_late.array(id integer PRIMARY KEY,parent integer REFERENCES jt47_late.root(id),label text);
INSERT INTO jt47_late.object VALUES(7,'seven');
INSERT INTO jt47_late.root VALUES(1,7,'one');
INSERT INTO jt47_late.array VALUES(10,1,'ten');""")
        late_spec = {
            "root": {"table": "jt47_late.root", "key": "id",
                     "fields": {"label": "label"}, "update": True},
            "object": {"name": "object", "table": "jt47_late.object", "key": "id",
                       "root_fk": "obj", "fields": {"id": "id", "label": "label"}},
            "array": {"name": "items", "table": "jt47_late.array", "key": "id",
                      "parent_fk": "parent", "fields": {"id": "id", "label": "label"},
                      "insert": True, "update": True, "delete": True}}
        db.run("SELECT jsontailor.create_duality_view('jt47_late.documents'," +
               quote(json.dumps(late_spec)) + "::jsonb);")
        table = {"root": "root", "object": "object", "array": "array"}[role]
        db.run(f"CREATE TABLE jt47_late.{table}_child() INHERITS(jt47_late.{table});")
        late_doc = json.loads(db.value(
            "SELECT data::text FROM jt47_late.documents WHERE _id=1;"))
        late_doc["label"] = "blocked"
        late = db.run("UPDATE jt47_late.documents SET data=" +
                      quote(json.dumps(late_doc)) + "::jsonb WHERE _id=1;", False)
        states.append((f"late-{role}", db.state(late)))
    record("PGJT-47/classic-inherits-rejection",
           states == [("root", "22023"), ("object", "22023"), ("array", "22023"),
                      ("late-root", "PT422"), ("late-object", "PT422"),
                      ("late-array", "PT422")],
           repr(states))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    version = int(db.value("SHOW server_version_num;"))
    if version < 170000:
        raise RuntimeError(f"PostgreSQL 17+ required, found {version}")
    results = []

    def record(name: str, passed: bool, observed: str) -> None:
        row = {"name": name, "status": "PASS" if passed else "FAIL",
               "postgresql": version, "observed": observed}
        results.append(row)
        print(f"{row['status']}: {name} ({observed})")

    partition_role_transitions(db, record)
    missing_partition_recovery(db, record)
    post_change_operations(db, record)
    health_regeneration(db, record)
    classic_inherits_rejection(db, record)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                   for row in results))
    return 0 if len(results) == 5 and all(r["status"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
