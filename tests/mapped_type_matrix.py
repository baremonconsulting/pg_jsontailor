#!/usr/bin/env python3
"""PGJT-44 public mapped-type contract matrix."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return quote(value)
    return str(value)


class Database:
    def __init__(self, psql: str, database: str):
        self.command = [psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database]

    def run(self, sql: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            self.command, input="\\set VERBOSITY verbose\n" + sql,
            text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(result.stderr)
        return result

    def value(self, sql: str) -> str:
        return self.run(sql).stdout.strip().splitlines()[-1]

    @staticmethod
    def state(result: subprocess.CompletedProcess[str]) -> str | None:
        match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", result.stderr)
        return match.group(1) if match else None


TYPES = (
    ("text", "text", "alpha", "beta", "gamma"),
    ("varchar", "varchar(12)", "alpha", "beta", "gamma"),
    ("bpchar", "char(8)", "alpha", "beta", "gamma"),
    ("int2", "smallint", 101, 102, 103),
    ("int4", "integer", 100001, 100002, 100003),
    ("int8", "bigint", 9000000001, 9000000002, 9000000003),
    ("numeric", "numeric(12,2)", 101.25, 102.50, 103.75),
    ("boolean", "boolean", True, False, True),
    ("date", "date", "2026-01-01", "2026-01-02", "2026-01-03"),
    ("timestamp", "timestamp(6)", "2026-01-01 01:02:03.123456",
     "2026-01-02 02:03:04.234567", "2026-01-03 03:04:05.345678"),
    ("timestamptz", "timestamptz(6)", "2026-01-01T01:02:03.123456Z",
     "2026-01-02T02:03:04.234567Z", "2026-01-03T03:04:05.345678Z"),
    ("uuid", "uuid", "10000000-0000-0000-0000-000000000001",
     "10000000-0000-0000-0000-000000000002",
     "10000000-0000-0000-0000-000000000003"),
)
EXPECTED_TYPE_NAMES = ("text", "varchar", "bpchar", "int2", "int4", "int8",
                       "numeric", "boolean", "date", "timestamp", "timestamptz", "uuid")

FIELD_COLUMNS = (
    ("textValue", "text_value", "text", "plain", "changed"),
    ("varcharValue", "varchar_value", "varchar(5)", "abcde", "vwxyz"),
    ("bpcharValue", "bpchar_value", "char(5)", "abcde", "vwxyz"),
    ("int2Value", "int2_value", "smallint", -32768, 32767),
    ("int4Value", "int4_value", "integer", -2147483648, 2147483647),
    ("int8Value", "int8_value", "bigint", -9223372036854775808, 9223372036854775807),
    ("numericValue", "numeric_value", "numeric(8,2)", 123456.78, -123456.78),
    ("booleanValue", "boolean_value", "boolean", True, False),
    ("dateValue", "date_value", "date", "2026-01-01", "2026-12-31"),
    ("timestampValue", "timestamp_value", "timestamp(6)",
     "2026-01-01 01:02:03.123456", "2026-12-31 23:59:58.654321"),
    ("timestamptzValue", "timestamptz_value", "timestamptz(6)",
     "2026-01-01T01:02:03.123456Z", "2026-12-31T23:59:58.654321Z"),
    ("uuidValue", "uuid_value", "uuid",
     "20000000-0000-0000-0000-000000000001",
     "20000000-0000-0000-0000-000000000002"),
)
EXPECTED_FIELD_NAMES = ("textValue", "varcharValue", "bpcharValue", "int2Value",
                        "int4Value", "int8Value", "numericValue", "booleanValue",
                        "dateValue", "timestampValue", "timestamptzValue", "uuidValue")


def specification(schema: str) -> dict:
    fields = {json_name: column for json_name, column, *_ in FIELD_COLUMNS}
    return {
        "root": {"table": f"{schema}.root", "key": "id", "fields": fields,
                 "insert": True, "update": True, "delete": True},
        "object": {"name": "detail", "table": f"{schema}.detail", "key": "id",
                   "root_fk": "detail_id", "fields": {"id": "id", **fields}},
        "array": {"name": "items", "table": f"{schema}.item", "key": "id",
                  "parent_fk": "parent", "fields": {"id": "id", **fields},
                  "insert": True, "update": True, "delete": True},
    }


def field_values(changed: bool = False) -> dict:
    return {name: (second if changed else first)
            for name, _column, _type, first, second in FIELD_COLUMNS}


def emitted(name: str, value: object) -> object:
    if name == "timestampValue" and isinstance(value, str):
        return value.replace(" ", "T")
    return value


def create_field_matrix(db: Database) -> None:
    columns = ",\n".join(f"{column} {sql_type}"
                          for _name, column, sql_type, *_ in FIELD_COLUMNS)
    initial_values = ",".join(sql_literal(value) for value in field_values().values())
    db.run(f"""DROP SCHEMA IF EXISTS jt_type_matrix CASCADE;
CREATE SCHEMA jt_type_matrix;
CREATE TABLE jt_type_matrix.detail(id bigint PRIMARY KEY,{columns});
CREATE TABLE jt_type_matrix.root(
  id bigint PRIMARY KEY,
  detail_id bigint NOT NULL REFERENCES jt_type_matrix.detail(id),{columns});
CREATE TABLE jt_type_matrix.item(
  id bigint PRIMARY KEY,
  parent bigint NOT NULL REFERENCES jt_type_matrix.root(id),{columns});
SELECT jsontailor.create_duality_view(
  'jt_type_matrix.documents',
  {quote(json.dumps(specification('jt_type_matrix')))}::jsonb);
""")


def document(identity: int, detail_id: int, item_id: int, changed: bool = False) -> dict:
    values = field_values(changed)
    return {"_id": identity, **values,
            "detail": {"id": detail_id, **values},
            "items": [{"id": item_id, **values}]}


def field_matrix(db: Database, record) -> None:
    create_field_matrix(db)
    outcomes = []
    null_fields = {name: None for name, *_ in FIELD_COLUMNS}
    for index, (name, column, _sql_type, initial, changed) in enumerate(FIELD_COLUMNS, 1):
        root_id, detail_id, item_id = index, 100 + index, 200 + index
        detail_values = {**null_fields, name: initial}
        columns = ",".join(entry[1] for entry in FIELD_COLUMNS)
        values = ",".join(sql_literal(detail_values[entry[0]])
                          if detail_values[entry[0]] is not None else "NULL"
                          for entry in FIELD_COLUMNS)
        db.run(f"INSERT INTO jt_type_matrix.detail(id,{columns}) "
               f"VALUES({detail_id},{values});")
        initial_doc = {"_id": root_id, name: initial,
                       "detail": {"id": detail_id, **detail_values},
                       "items": [{"id": item_id, name: initial}]}
        inserted = db.run("INSERT INTO jt_type_matrix.documents(data) VALUES(" +
                          quote(json.dumps(initial_doc)) + "::jsonb);", False)
        if inserted.returncode:
            outcomes.append((name, "insert", db.state(inserted)))
            continue
        read = json.loads(db.value(
            f"SELECT data::text FROM jt_type_matrix.documents WHERE _id={root_id};"))
        expected_initial = emitted(name, initial)
        expected_changed = emitted(name, changed)
        insert_ok = (read[name] == expected_initial
                     and read["detail"][name] == expected_initial
                     and read["items"][0][name] == expected_initial)
        read[name] = changed
        read["items"][0][name] = changed
        # Referenced objects are immutable through a document; their mapped
        # fields are nevertheless covered by insert/read validation above.
        updated = db.run("UPDATE jt_type_matrix.documents SET data=" +
                         quote(json.dumps(read)) + f"::jsonb WHERE _id={root_id};", False)
        after_update = json.loads(db.value(
            f"SELECT data::text FROM jt_type_matrix.documents WHERE _id={root_id};"))
        update_ok = (updated.returncode == 0 and after_update[name] == expected_changed
                     and after_update["detail"][name] == expected_initial
                     and after_update["items"][0][name] == expected_changed)
        after_update[name] = initial
        after_update["items"][0][name] = initial
        merged = db.run(
            f"MERGE INTO jt_type_matrix.documents d USING (VALUES ({root_id}," +
            quote(json.dumps(after_update)) + "::jsonb)) s(id,data) ON d._id=s.id "
            "WHEN MATCHED THEN UPDATE SET data=s.data;", False)
        after_merge = json.loads(db.value(
            f"SELECT data::text FROM jt_type_matrix.documents WHERE _id={root_id};"))
        merge_ok = (merged.returncode == 0 and after_merge[name] == expected_initial
                    and after_merge["detail"][name] == expected_initial
                    and after_merge["items"][0][name] == expected_initial)
        merge_root, merge_detail, merge_item = 1000 + index, 1100 + index, 1200 + index
        db.run(f"INSERT INTO jt_type_matrix.detail(id,{columns}) VALUES({merge_detail},{values});")
        merge_insert_doc = {"_id": merge_root, name: initial,
                            "detail": {"id": merge_detail, **detail_values},
                            "items": [{"id": merge_item, name: initial}]}
        merge_insert = db.run(
            f"MERGE INTO jt_type_matrix.documents d USING (VALUES ({merge_root}," +
            quote(json.dumps(merge_insert_doc)) +
            "::jsonb)) s(id,data) ON d._id=s.id "
            "WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data);", False)
        merge_insert_read = json.loads(db.value(
            f"SELECT data::text FROM jt_type_matrix.documents WHERE _id={merge_root};"))
        merge_insert_ok = (merge_insert.returncode == 0
                           and merge_insert_read[name] == expected_initial
                           and merge_insert_read["detail"][name] == expected_initial
                           and merge_insert_read["items"][0][name] == expected_initial)
        outcomes.append((name, insert_ok, update_ok, merge_ok, merge_insert_ok))
    record("PGJT-44/field-operation-matrix",
           tuple(row[0] for row in outcomes) == EXPECTED_FIELD_NAMES and all(
               len(row) == 5 and row[1:] == (True, True, True, True)
               for row in outcomes), repr(outcomes))


def invalid_matrix(db: Database, record) -> None:
    baseline = json.loads(db.value(
        "SELECT data::text FROM jt_type_matrix.documents WHERE _id=1;"))
    invalid_values = {
        "textValue": 1, "varcharValue": True, "bpcharValue": [],
        "int2Value": "1", "int4Value": 1.5, "int8Value": {},
        "numericValue": "1.23", "booleanValue": "true",
        "dateValue": "not-a-date", "timestampValue": "not-a-timestamp",
        "timestamptzValue": "not-a-timestamptz", "uuidValue": "not-a-uuid",
    }
    invalid_cases = list(invalid_values.items()) + [
        ("varcharValue", "too-long"),
        ("dateValue", "5874898-01-01"),
    ]
    outcomes = []
    sequence = 0
    for location in ("root", "object", "array"):
        for field, invalid in invalid_cases:
            sequence += 1
            candidate = json.loads(json.dumps(baseline))
            target = candidate if location == "root" else (
                candidate["detail"] if location == "object" else candidate["items"][0])
            target[field] = invalid
            if field == "textValue" and location == "root":
                candidate["booleanValue"] = True
            else:
                candidate["textValue"] = "must-roll-back"
            before = db.value(
                "SELECT md5(data::text) FROM jt_type_matrix.documents WHERE _id=1;")
            failed = db.run("UPDATE jt_type_matrix.documents SET data=" +
                            quote(json.dumps(candidate)) + "::jsonb WHERE _id=1;", False)
            after = db.value(
                "SELECT md5(data::text) FROM jt_type_matrix.documents WHERE _id=1;")
            insert_doc = json.loads(json.dumps(candidate))
            insert_doc.pop("_metadata", None)
            insert_doc["_id"] = 5000 + sequence
            insert_doc["items"][0]["id"] = 6000 + sequence
            inserted = db.run("INSERT INTO jt_type_matrix.documents(data) VALUES(" +
                              quote(json.dumps(insert_doc)) + "::jsonb);", False)
            merge_doc = json.loads(json.dumps(insert_doc))
            merge_doc["_id"] = 7000 + sequence
            merge_doc["items"][0]["id"] = 8000 + sequence
            merged = db.run(
                f"MERGE INTO jt_type_matrix.documents d USING (VALUES ({7000 + sequence}," +
                quote(json.dumps(merge_doc)) +
                "::jsonb)) s(id,data) ON d._id=s.id "
                "WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data);", False)
            outcomes.append((location, field, db.state(failed), before == after,
                             db.state(inserted), db.state(merged)))
    record("PGJT-44/invalid-values-atomic", len(outcomes) == 42 and all(
        update_state == insert_state == merge_state == "PT422" and unchanged
        for _location, _field, update_state, unchanged, insert_state, merge_state
        in outcomes), repr(outcomes))


def key_matrix(db: Database, record) -> None:
    outcomes = []
    for index, (name, sql_type, root_key, object_key, child_key) in enumerate(TYPES):
        schema = f"jt_key_{name}"
        spec = {
            "root": {"table": f"{schema}.root", "key": "id",
                     "fields": {"label": "label"}, "insert": True, "update": True},
            "object": {"name": "detail", "table": f"{schema}.detail", "key": "id",
                       "root_fk": "detail_id", "fields": {"id": "id", "label": "label"}},
            "array": {"name": "items", "table": f"{schema}.item", "key": "id",
                      "parent_fk": "parent", "fields": {"id": "id", "label": "label"},
                      "insert": True, "update": True, "delete": True},
        }
        db.run(f"""DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema};
CREATE TABLE {schema}.detail(id {sql_type} PRIMARY KEY,label text NOT NULL);
CREATE TABLE {schema}.root(id {sql_type} PRIMARY KEY,
 detail_id {sql_type} NOT NULL REFERENCES {schema}.detail(id),label text NOT NULL);
CREATE TABLE {schema}.item(id {sql_type} PRIMARY KEY,
 parent {sql_type} NOT NULL REFERENCES {schema}.root(id),label text NOT NULL);
SELECT jsontailor.create_duality_view('{schema}.documents',
 {quote(json.dumps(spec))}::jsonb);""")
        db.run(f"INSERT INTO {schema}.detail VALUES ({sql_literal(object_key)},'object');")
        doc = {"_id": root_key, "label": "inserted",
               "detail": {"id": object_key, "label": "object"},
               "items": [{"id": child_key, "label": "child"}]}
        inserted = db.run(f"INSERT INTO {schema}.documents(data) VALUES(" +
                          quote(json.dumps(doc)) + "::jsonb);", False)
        rows = db.value(f"SELECT count(*)::text||':'||(SELECT count(*) FROM {schema}.detail)"
                        f"||':'||(SELECT count(*) FROM {schema}.item) FROM {schema}.root;")
        read = json.loads(db.value(f"SELECT data::text FROM {schema}.documents;"))
        expected_root = emitted("timestampValue" if name == "timestamp" else "", root_key)
        expected_object = emitted("timestampValue" if name == "timestamp" else "", object_key)
        expected_child = emitted("timestampValue" if name == "timestamp" else "", child_key)
        if name == "bpchar":
            expected_root = str(root_key).ljust(8)
            expected_object = str(object_key).ljust(8)
            expected_child = str(child_key).ljust(8)
        keys_ok = (read["_id"] == expected_root
                   and read["detail"]["id"] == expected_object
                   and read["items"][0]["id"] == expected_child)
        read["label"] = "updated"
        updated = db.run(f"UPDATE {schema}.documents SET data=" +
                         quote(json.dumps(read)) + "::jsonb;", False)
        updated_label = db.value(f"SELECT label FROM {schema}.root;")
        merge_doc = json.loads(db.value(f"SELECT data::text FROM {schema}.documents;"))
        merge_doc["label"] = "merged"
        merged = db.run(f"MERGE INTO {schema}.documents d USING (SELECT " +
                        quote(json.dumps(merge_doc)) + "::jsonb data) s ON true "
                        "WHEN MATCHED THEN UPDATE SET data=s.data;", False)
        label = db.value(f"SELECT label FROM {schema}.root;")
        outcomes.append((name, inserted.returncode, updated.returncode,
                         updated_label, merged.returncode, rows, label, keys_ok))
    record("PGJT-44/key-position-matrix", all(
        insert == update == merge == 0 and rows == "1:1:1"
        and updated_label == "updated" and label == "merged" and keys_ok
        for _name, insert, update, updated_label, merge, rows, label, keys_ok
        in outcomes) and tuple(row[0] for row in outcomes) == EXPECTED_TYPE_NAMES,
        repr(outcomes))


def special_values(db: Database, record) -> None:
    special_spec = {"root": {"table": "jt_type_special.root", "key": "id",
                    "fields": {"amount": "amount", "observedAt": "observed_at"},
                    "insert": True, "update": True}}
    db.run("DROP SCHEMA IF EXISTS jt_type_special CASCADE;"
           "CREATE SCHEMA jt_type_special;"
           "CREATE TABLE jt_type_special.root("
           "id integer PRIMARY KEY,amount numeric,observed_at timestamptz);"
           "SELECT jsontailor.create_duality_view('jt_type_special.documents'," +
           quote(json.dumps(special_spec)) + "::jsonb);")
    numeric_doc = {"_id": 1, "amount": "NaN"}
    time_doc = {"_id": 2, "observedAt": "0002-02-03T04:05:06.123456Z BC"}
    inserted_numeric = db.run(
        "INSERT INTO jt_type_special.documents(data) VALUES(" +
        quote(json.dumps(numeric_doc)) + "::jsonb);", False)
    inserted_time = db.run(
        "INSERT INTO jt_type_special.documents(data) VALUES(" +
        quote(json.dumps(time_doc)) + "::jsonb);", False)
    cases = (
        (1, "amount", "Infinity", "Infinity"),
        (1, "amount", "-Infinity", "-Infinity"),
        (1, "amount", "NaN", "NaN"),
        (2, "observedAt", "0002-02-03T04:05:06.123456Z",
         "0002-02-03T04:05:06.123456Z"),
        (2, "observedAt", "0002-02-03T04:05:06.123456Z BC",
         "0002-02-03T04:05:06.123456Z BC"),
    )
    outcomes = []
    for identity, field, value, expected in cases:
        candidate = json.loads(db.value(
            f"SELECT data::text FROM jt_type_special.documents WHERE _id={identity};"))
        candidate[field] = value
        result = db.run("UPDATE jt_type_special.documents SET data=" +
                        quote(json.dumps(candidate)) +
                        f"::jsonb WHERE _id={identity};", False)
        observed = db.value(
            f"SELECT data->>{quote(field)} FROM jt_type_special.documents "
            f"WHERE _id={identity};")
        outcomes.append((field, value, db.state(result), observed))
    record("PGJT-44/numeric-timestamptz-boundaries",
           inserted_numeric.returncode == inserted_time.returncode == 0 and all(
        state is None and observed == expected
        for (_field, _value, state, observed), (_id, _f, _v, expected)
        in zip(outcomes, cases)),
        repr(outcomes))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    results = []

    def record(name: str, passed: bool, observed: str) -> None:
        results.append({"name": name, "status": "PASS" if passed else "FAIL",
                        "observed": observed})

    field_matrix(db, record)
    invalid_matrix(db, record)
    special_values(db, record)
    key_matrix(db, record)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                   for row in results))
    for row in results:
        print(f"{row['status']}: {row['name']} ({row['observed']})")
    return 0 if all(row["status"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
