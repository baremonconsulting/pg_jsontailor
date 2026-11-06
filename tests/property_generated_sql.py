#!/usr/bin/env python3
"""Seeded PGJT-50 properties for specifications, identifiers and documents."""
from __future__ import annotations

import argparse, hashlib, json, random, re, subprocess
from pathlib import Path


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class DB:
    def __init__(self, psql: str, database: str):
        self.cmd = [psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database]
    def run(self, sql: str, ok: bool = True):
        result = subprocess.run(self.cmd, input="\\set VERBOSITY verbose\n" + sql,
                                text=True, capture_output=True)
        if ok and result.returncode:
            raise RuntimeError(result.stderr)
        return result
    def value(self, sql: str) -> str:
        rows = self.run(sql).stdout.strip().splitlines()
        if not rows: raise RuntimeError("query returned no value")
        return rows[-1]
    @staticmethod
    def state(result) -> str | None:
        match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", result.stderr)
        return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", type=int, default=50050)
    parser.add_argument("--cases", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.cases <= 128: raise SystemExit("--cases must be in 1..128")
    rng, db, evidence = random.Random(args.seed), DB(args.psql, args.database), []
    case_tokens = []
    def record(test_id: str, passed: bool, detail: str):
        evidence.append({"id": test_id, "status": "PASS" if passed else "FAIL",
                         "seed": args.seed, "cases": args.cases, "detail": detail})

    db.run("DROP SCHEMA IF EXISTS jt50 CASCADE; CREATE SCHEMA jt50; "
           "CREATE TABLE jt50.root(id text PRIMARY KEY,label text NOT NULL,n text NOT NULL);")
    valid = invalid = docs = boundaries = True
    hostile = True
    for i in range(args.cases):
        token = f"p{rng.getrandbits(48):012x}"
        case_tokens.append(token)
        fields = {"label":"label","n":"n"} if i % 2 else {"n":"n","label":"label"}
        spec = {"root":{"table":"jt50.root","key":"id","fields":fields,
                "insert":True,"update":bool(i % 3),"delete":bool(i % 4)}}
        view = f"jt50.{token}"
        ddl = db.run("SELECT jsontailor.create_duality_view(" + lit(view) + "," +
                     lit(json.dumps(spec, sort_keys=True)) + "::jsonb);", False)
        valid &= ddl.returncode == 0
        if ddl.returncode == 0:
            ident, label, number = token, "v-'\"-" + token, str(rng.randrange(-100000, 100000))
            doc = {"_id": ident, "label": label, "n": number}
            inserted = db.run(f"INSERT INTO {view}(data) VALUES(" + lit(json.dumps(doc)) +
                              "::jsonb);", False)
            read = db.value(f"SELECT jsonb_build_array(data->>'label',data->>'n')::text FROM {view} WHERE _id=" + lit(ident) + ";") if inserted.returncode == 0 else ""
            docs &= inserted.returncode == 0 and json.loads(read) == [label, number]
        bad_variants = [dict(spec, unknown_property=i),
                        {"root": []},
                        {"root":{"table":"jt50.root","key":"id","fields":[]}},
                        {"root":{"key":"id","fields":{"label":"label"}}}]
        bad = bad_variants[i % len(bad_variants)]
        rejected = db.run("SELECT jsontailor.duality_view_ddl(" + lit(f"jt50.bad_{i}") + "," +
                          lit(json.dumps(bad)) + "::jsonb);", False)
        invalid &= rejected.returncode != 0 and db.state(rejected) == "22023"

    # SQL identifiers requiring quoting must survive generator and catalog round trips.
    weird = 'root space " quote'
    db.run('CREATE TABLE jt50."root space "" quote"('
           '"id key" text PRIMARY KEY,"label space" text NOT NULL);')
    spec = {"root":{"table":'jt50."root space "" quote"',"key":"id key",
                    "fields":{"odd_key":"label space"},"insert":True}}
    result = db.run("SELECT jsontailor.create_duality_view(" + lit("jt50.hostile_dv") + "," +
                    lit(json.dumps(spec)) + "::jsonb);", False)
    hostile &= result.returncode == 0 and db.value(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='jt50' AND c.relname='hostile_dv';") == "1"
    hostile_doc={"_id":"hostile-id","odd_key":"hostile-value"}
    hostile_insert=db.run("INSERT INTO jt50.hostile_dv(data) VALUES("+
                          lit(json.dumps(hostile_doc))+"::jsonb);",False)
    hostile &= hostile_insert.returncode == 0 and db.value(
        "SELECT data->>'odd_key' FROM jt50.hostile_dv WHERE _id='hostile-id';") == "hostile-value"

    # The longest generated suffix is __delete_document (17 bytes).
    long_name = "x" * 46
    spec = {"root":{"table":"jt50.root","key":"id","fields":{"label":"label"}}}
    at_limit = db.run("SELECT jsontailor.duality_view_ddl(" + lit("jt50." + long_name) + "," +
                      lit(json.dumps(spec)) + "::jsonb);", False)
    over_limit = db.run("SELECT jsontailor.duality_view_ddl(" + lit("jt50." + "y" * 47) + "," +
                        lit(json.dumps(spec)) + "::jsonb);", False)
    boundaries &= at_limit.returncode == 0 and over_limit.returncode != 0

    # Nested object/array documents at bounded cardinalities.
    db.run("""CREATE TABLE jt50.detail(id text PRIMARY KEY,label text NOT NULL);
CREATE TABLE jt50.parent(id text PRIMARY KEY,detail_id text REFERENCES jt50.detail(id),label text NOT NULL);
CREATE TABLE jt50.child(id text PRIMARY KEY,parent_id text NOT NULL REFERENCES jt50.parent(id),label text NOT NULL);
INSERT INTO jt50.detail VALUES('d','detail');""")
    nested_spec={"root":{"table":"jt50.parent","key":"id","fields":{"label":"label"},"insert":True},
      "object":{"name":"detail","table":"jt50.detail","key":"id","root_fk":"detail_id","fields":{"id":"id","label":"label"}},
      "array":{"name":"items","table":"jt50.child","key":"id","parent_fk":"parent_id","fields":{"id":"id","label":"label"},"insert":True}}
    nested_created=db.run("SELECT jsontailor.create_duality_view('jt50.nested_dv',"+lit(json.dumps(nested_spec))+"::jsonb);",False)
    for size in (0,1,32):
        nested={"_id":f"nested-{size}","label":"root","detail":{"id":"d"},
                "items":[{"id":f"c-{size}-{n}","label":f"child-{n}"} for n in range(size)]}
        inserted=db.run("INSERT INTO jt50.nested_dv(data) VALUES("+lit(json.dumps(nested))+"::jsonb);",False)
        observed=db.value("SELECT jsonb_array_length(data->'items') FROM jt50.nested_dv WHERE _id="+lit(f"nested-{size}")) if inserted.returncode==0 else "-1"
        boundaries &= nested_created.returncode==0 and inserted.returncode==0 and observed==str(size)
    wrong_nesting=db.run("INSERT INTO jt50.nested_dv(data) VALUES("+lit(json.dumps(
        {"_id":"bad-nesting","label":"root","detail":[{"id":"d"}],"items":[]}))+"::jsonb);",False)
    boundaries &= wrong_nesting.returncode != 0 and db.state(wrong_nesting)=="PT422"

    digest = hashlib.sha256("\n".join(case_tokens).encode()).hexdigest()
    record("PGJT-50/seeded-valid-specifications", valid, digest)
    record("PGJT-50/seeded-invalid-specifications", invalid, digest)
    record("PGJT-50/hostile-identifiers", hostile, weird)
    record("PGJT-50/seeded-json-documents", docs, digest)
    record("PGJT-50/size-nesting-boundaries", boundaries, "identifier boundaries")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in evidence))
    return 0 if all(row["status"] == "PASS" for row in evidence) else 1


if __name__ == "__main__": raise SystemExit(main())
