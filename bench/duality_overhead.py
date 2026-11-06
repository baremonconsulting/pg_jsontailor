#!/usr/bin/env python3
"""Benchmark generated duality-view operations against direct relational SQL."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import time
import uuid

from verify_results import verify_results

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--pg-bin", type=Path,
                    default=Path(os.environ["PG_JSONTAILOR_PG_BIN"])
                    if os.environ.get("PG_JSONTAILOR_PG_BIN")
                    else Path(shutil.which("pg_config") or "/usr/bin/pg_config").parent)
parser.add_argument("--database", default="jsontailor_duality_benchmark")
parser.add_argument("--sizes", default="0,1,10,100,1000")
parser.add_argument("--warmups", type=int, default=3)
parser.add_argument("--repetitions", type=int, default=30)
parser.add_argument("--output", type=Path)
parser.add_argument("--pg-host", default=os.environ.get("PGHOST", str(ROOT / ".run" / "socket")))
parser.add_argument("--pg-port", default=os.environ.get("PGPORT", "55433"))
parser.add_argument("--pg-user", default=os.environ.get("PGUSER", os.environ.get("USER", "postgres")))
parser.add_argument("--source-commit", help="exact source commit when running outside a Git checkout")
parser.add_argument("--drop-database-after", action="store_true",
                    help="drop the disposable benchmark database after artifacts are written")
args = parser.parse_args()
PG = args.pg_bin
DB = args.database
try:
    SIZES = tuple(int(value) for value in args.sizes.split(","))
except ValueError as error:
    parser.error(f"--sizes must be a comma-separated integer list: {error}")
WARMUPS = args.warmups
REPETITIONS = args.repetitions
OPERATIONS = ("read", "insert", "root_update", "update", "delete",
              "merge_insert", "merge_update", "merge_delete")
RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
OUT = args.output or ROOT / "results" / f"duality-benchmark-{RUN_ID}"
ENV = {**os.environ, "PGHOST": args.pg_host, "PGPORT": str(args.pg_port), "PGUSER": args.pg_user}
ARTIFACTS = ("measurements.csv", "plans.json", "summary.json", "environment.json")
PRODUCT_INPUTS = ("sql", "pg_jsontailor.control", "Makefile")

if not SIZES or any(size < 0 for size in SIZES) or len(set(SIZES)) != len(SIZES):
    parser.error("--sizes must contain distinct non-negative integers")
if WARMUPS < 0:
    parser.error("--warmups must be non-negative")
if REPETITIONS <= 0:
    parser.error("--repetitions must be positive")
if not DB or DB in {"postgres", "template0", "template1"}:
    parser.error("--database must name a dedicated disposable database")


def run(args, input_text=None, check=True):
    result = subprocess.run([str(a) for a in args], input=input_text, text=True,
                            capture_output=True, env=ENV)
    if check and result.returncode:
        raise RuntimeError(f"failed {args}:\n{result.stdout}\n{result.stderr}")
    return result


def sql(text, tuples=False):
    flags = "-XqAt" if tuples else "-Xq"
    return run([PG / "psql", flags, "-v", "ON_ERROR_STOP=1", "-d", DB], text).stdout


def lit(value):
    return "'" + value.replace("'", "''") + "'"


def product_hashes():
    hashes = {}
    for relative in PRODUCT_INPUTS:
        source = ROOT / relative
        files = sorted(path for path in source.rglob("*") if path.is_file()) \
            if source.is_dir() else [source]
        for path in files:
            hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return hashes


class PersistentPsql:
    """Keep one backend so PL/pgSQL compilation and SPI plans can be reused."""
    def __init__(self):
        self.process = subprocess.Popen(
            [str(PG / "psql"), "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", DB],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=ENV, bufsize=1)

    def execute(self, script):
        marker = "__JT_END_" + uuid.uuid4().hex + "__"
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(script + f"\n\\echo {marker}\n")
        self.process.stdin.flush()
        lines=[]
        for line in self.process.stdout:
            if line.rstrip("\n") == marker:
                return "".join(lines)
            lines.append(line)
        raise RuntimeError("persistent psql exited before marker: " + "".join(lines)[-1000:])

    def close(self):
        if self.process.stdin: self.process.stdin.close()
        rc=self.process.wait(timeout=10)
        if rc: raise RuntimeError(f"persistent psql exited {rc}")


def explain(session, statements):
    script = "SET jit=off; SET max_parallel_workers_per_gather=0; BEGIN;\n"
    for statement in statements:
        script += "EXPLAIN (ANALYZE, TIMING OFF, BUFFERS, WAL, FORMAT JSON) " + statement + ";\n"
    script += "ROLLBACK;\n"
    raw = session.execute(script)
    decoder = json.JSONDecoder(); pos = 0; payloads = []
    while pos < len(raw):
        while pos < len(raw) and raw[pos].isspace(): pos += 1
        if pos >= len(raw): break
        value, pos = decoder.raw_decode(raw, pos)
        payloads.append(value[0])
    if len(payloads) != len(statements):
        raise RuntimeError(f"expected {len(statements)} plans, got {len(payloads)}: {raw[:500]}")
    return {
        "execution_ms": sum(float(p["Execution Time"]) for p in payloads),
        "planning_ms": sum(float(p["Planning Time"]) for p in payloads),
        "shared_hit_blocks": sum(int(p["Plan"].get("Shared Hit Blocks", 0)) for p in payloads),
        "shared_dirtied_blocks": sum(int(p["Plan"].get("Shared Dirtied Blocks", 0)) for p in payloads),
        "wal_records": sum(int(p["Plan"].get("WAL Records", 0)) for p in payloads),
        "plans": payloads,
    }


if OUT.exists() and not OUT.is_dir():
    parser.error(f"--output exists and is not a directory: {OUT}")
git_head = run(["git", "-C", ROOT, "rev-parse", "HEAD"], check=False).stdout.strip() or None
git_status_result = run(["git", "-C", ROOT, "status", "--porcelain"], check=False)
if args.source_commit and git_head and args.source_commit != git_head:
    parser.error("--source-commit differs from the current Git checkout")
source_commit = args.source_commit or git_head
if not source_commit:
    parser.error("--source-commit is required when running outside a Git checkout")
if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
    parser.error("source commit must be a full 40-character lowercase Git SHA-1")
source_dirty = bool(git_status_result.stdout) if git_head else None

OUT.mkdir(parents=True, exist_ok=True)
collisions = [name for name in ARTIFACTS if (OUT / name).exists()]
if collisions:
    parser.error("--output already contains benchmark artifacts: " + ", ".join(collisions))
run([PG / "dropdb", "--if-exists", DB])
run([PG / "createdb", DB])
sql("""
CREATE EXTENSION pg_jsontailor;
CREATE SCHEMA bench_dv;
CREATE TABLE bench_dv.customers(customer_id bigint PRIMARY KEY,name text NOT NULL);
CREATE TABLE bench_dv.orders(order_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,customer_id bigint REFERENCES bench_dv.customers,status text NOT NULL,created_at date NOT NULL);
CREATE TABLE bench_dv.items(item_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,order_id bigint NOT NULL REFERENCES bench_dv.orders,product text NOT NULL,quantity integer NOT NULL CHECK(quantity>0),unit_price numeric(12,2) NOT NULL);
CREATE INDEX ON bench_dv.items(order_id);
CREATE TABLE bench_dv.payloads(size integer PRIMARY KEY,source_id bigint NOT NULL,insert_id bigint NOT NULL,root_doc jsonb,update_doc jsonb,insert_doc jsonb);
INSERT INTO bench_dv.customers VALUES(1,'Benchmark customer');
""")
for size in SIZES:
    source_id = 1000 + size
    insert_id = 100000 + size
    sql(f"INSERT INTO bench_dv.orders VALUES({source_id},1,'new',DATE '2026-11-03');\n" +
        (f"INSERT INTO bench_dv.items(item_id,order_id,product,quantity,unit_price) SELECT {source_id}*10000+g,{source_id},'Product '||g,1,10.25 FROM generate_series(1,{size}) g;" if size else ""))
spec = json.dumps({
    "root":{"table":"bench_dv.orders","key":"order_id","fields":{"status":"status","created":"created_at"},"insert":True,"update":True,"delete":True},
    "object":{"name":"customer","table":"bench_dv.customers","key":"customer_id","root_fk":"customer_id","fields":{"customerId":"customer_id","name":"name"},"etag":False},
    "array":{"name":"items","table":"bench_dv.items","key":"item_id","parent_fk":"order_id","order_by":"item_id","fields":{"itemId":"item_id","product":"product","qty":"quantity","price":"unit_price"},"insert":True,"update":True,"delete":True}
})
sql(f"SELECT jsontailor.create_duality_view('bench_dv.orders_dv',{lit(spec)}::jsonb);")
for size in SIZES:
    source_id = 1000 + size; insert_id = 100000 + size
    sql(f"""
INSERT INTO bench_dv.payloads(size,source_id,insert_id,root_doc,update_doc,insert_doc)
SELECT {size},{source_id},{insert_id},
       jsonb_set(v.data,'{{status}}','"root-updated"'),
       jsonb_set(jsonb_set(v.data,'{{status}}','\"updated\"'), '{{items}}',
         COALESCE((SELECT jsonb_agg(x.elem || jsonb_build_object('qty',(x.elem->>'qty')::integer+1) ORDER BY x.ord)
                   FROM jsonb_array_elements(v.data->'items') WITH ORDINALITY x(elem,ord)),'[]'::jsonb)),
       jsonb_build_object('_id',{insert_id},'status','inserted','created','2026-11-03','customer',jsonb_build_object('customerId',1),
         'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('itemId',{insert_id}*10000+x.ord,'product',x.elem->>'product','qty',2,'price',(x.elem->>'price')::numeric) ORDER BY x.ord)
                           FROM jsonb_array_elements(v.data->'items') WITH ORDINALITY x(elem,ord)),'[]'::jsonb))
FROM bench_dv.orders_dv v WHERE v._id={source_id};
""")
sql("ANALYZE bench_dv.orders; ANALYZE bench_dv.items; ANALYZE bench_dv.payloads;")


def statements(operation, implementation, size):
    source_id=1000+size; insert_id=100000+size
    if operation == "read":
        if implementation == "duality":
            return [f"SELECT pg_column_size(data) FROM bench_dv.orders_dv WHERE _id={source_id}"]
        return [f"""SELECT pg_column_size(jsonb_build_object('_id',o.order_id,'status',o.status,'created',o.created_at,'customer',jsonb_build_object('customerId',c.customer_id,'name',c.name),'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('itemId',i.item_id,'product',i.product,'qty',i.quantity,'price',i.unit_price) ORDER BY i.item_id) FROM bench_dv.items i WHERE i.order_id=o.order_id),'[]'::jsonb))) FROM bench_dv.orders o LEFT JOIN bench_dv.customers c ON c.customer_id=o.customer_id WHERE o.order_id={source_id}"""]
    if operation == "update":
        if implementation == "duality":
            return [f"UPDATE bench_dv.orders_dv SET data=(SELECT update_doc FROM bench_dv.payloads WHERE size={size}) WHERE _id={source_id}"]
        return [f"UPDATE bench_dv.orders o SET status=p.update_doc->>'status',created_at=(p.update_doc->>'created')::date,customer_id=(p.update_doc#>>'{{customer,customerId}}')::bigint FROM bench_dv.payloads p WHERE p.size={size} AND o.order_id={source_id}",
                f"UPDATE bench_dv.items i SET product=x.product,quantity=x.qty,unit_price=x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.update_doc->'items') x(\"itemId\" bigint,product text,qty integer,price numeric) WHERE p.size={size} AND i.order_id={source_id} AND i.item_id=x.\"itemId\""]
    if operation == "root_update":
        if implementation == "duality":
            return [f"UPDATE bench_dv.orders_dv SET data=(SELECT root_doc FROM bench_dv.payloads WHERE size={size}) WHERE _id={source_id}"]
        return [f"UPDATE bench_dv.orders o SET status=p.root_doc->>'status' FROM bench_dv.payloads p WHERE p.size={size} AND o.order_id={source_id}"]
    if operation == "insert":
        if implementation == "duality":
            return [f"INSERT INTO bench_dv.orders_dv(data) SELECT insert_doc FROM bench_dv.payloads WHERE size={size}"]
        return [f"INSERT INTO bench_dv.orders(order_id,customer_id,status,created_at) SELECT insert_id,(insert_doc#>>'{{customer,customerId}}')::bigint,insert_doc->>'status',(insert_doc->>'created')::date FROM bench_dv.payloads WHERE size={size}",
                f"INSERT INTO bench_dv.items(item_id,order_id,product,quantity,unit_price) SELECT x.\"itemId\",p.insert_id,x.product,x.qty,x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.insert_doc->'items') x(\"itemId\" bigint,product text,qty integer,price numeric) WHERE p.size={size}"]
    if operation == "delete":
        if implementation == "duality":
            return [f"SELECT bench_dv.orders_dv__delete_document({source_id},(SELECT data#>>'{{_metadata,etag}}' FROM bench_dv.orders_dv WHERE _id={source_id}))"]
        return [f"DELETE FROM bench_dv.items WHERE order_id={source_id}",f"DELETE FROM bench_dv.orders WHERE order_id={source_id}"]
    if operation == "merge_insert":
        if implementation == "duality":
            return [f"""MERGE INTO bench_dv.orders_dv AS target
USING (SELECT insert_id AS root_id,insert_doc AS doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target._id=source.root_id
WHEN NOT MATCHED THEN INSERT(data) VALUES(source.doc)"""]
        return [f"""MERGE INTO bench_dv.orders AS target
USING (SELECT insert_id,insert_doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.insert_id
WHEN NOT MATCHED THEN INSERT(order_id,customer_id,status,created_at)
VALUES(source.insert_id,(source.insert_doc#>>'{{customer,customerId}}')::bigint,source.insert_doc->>'status',(source.insert_doc->>'created')::date)""",
                f"""MERGE INTO bench_dv.items AS target
USING (SELECT x."itemId",p.insert_id,x.product,x.qty,x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.insert_doc->'items') x("itemId" bigint,product text,qty integer,price numeric) WHERE p.size={size}) AS source
ON target.item_id=source."itemId"
WHEN NOT MATCHED THEN INSERT(item_id,order_id,product,quantity,unit_price)
VALUES(source."itemId",source.insert_id,source.product,source.qty,source.price)"""]
    if operation == "merge_update":
        if implementation == "duality":
            return [f"""MERGE INTO bench_dv.orders_dv AS target
USING (SELECT source_id AS root_id,update_doc AS doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target._id=source.root_id
WHEN MATCHED THEN UPDATE SET data=source.doc"""]
        return [f"""MERGE INTO bench_dv.orders AS target
USING (SELECT source_id,update_doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.source_id
WHEN MATCHED THEN UPDATE SET status=source.update_doc->>'status',created_at=(source.update_doc->>'created')::date,customer_id=(source.update_doc#>>'{{customer,customerId}}')::bigint""",
                f"""MERGE INTO bench_dv.items AS target
USING (SELECT p.source_id,x."itemId",x.product,x.qty,x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.update_doc->'items') x("itemId" bigint,product text,qty integer,price numeric) WHERE p.size={size}) AS source
ON target.order_id=source.source_id AND target.item_id=source."itemId"
WHEN MATCHED THEN UPDATE SET product=source.product,quantity=source.qty,unit_price=source.price"""]
    if operation == "merge_delete":
        if implementation == "duality":
            return [f"""MERGE INTO bench_dv.orders_dv AS target
USING (SELECT source_id AS root_id,root_doc AS doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target._id=source.root_id
WHEN MATCHED AND target.data#>>'{{_metadata,etag}}'=source.doc#>>'{{_metadata,etag}}' THEN DELETE"""]
        return [f"""MERGE INTO bench_dv.items AS target
USING (SELECT source_id FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.source_id
WHEN MATCHED THEN DELETE""",
                f"""MERGE INTO bench_dv.orders AS target
USING (SELECT source_id FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.source_id
WHEN MATCHED THEN DELETE"""]
    raise ValueError(operation)


rows=[]; saved_plans={}; session=PersistentPsql()
try:
    for size in SIZES:
        for operation in OPERATIONS:
            total=WARMUPS+REPETITIONS
            for repetition in range(total):
                order=("direct","duality") if repetition%2==0 else ("duality","direct")
                for implementation in order:
                    result=explain(session,statements(operation,implementation,size))
                    rows.append({"size":size,"operation":operation,"implementation":implementation,
                                 "run":repetition,"warmup":repetition<WARMUPS,
                                 "execution_ms":result["execution_ms"],"planning_ms":result["planning_ms"],
                                 "shared_hit_blocks":result["shared_hit_blocks"],
                                 "shared_dirtied_blocks":result["shared_dirtied_blocks"],
                                 "wal_records":result["wal_records"],"status":"PASS"})
                    if repetition==WARMUPS: saved_plans[f"{size}:{operation}:{implementation}"]=result["plans"]
finally:
    session.close()

with (OUT/"measurements.csv").open("w",newline="") as handle:
    writer=csv.DictWriter(handle,fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
(OUT/"plans.json").write_text(json.dumps(saved_plans,indent=2))

summary=[]
for size in SIZES:
    for operation in OPERATIONS:
        values={}
        for implementation in ("direct","duality"):
            xs=[r["execution_ms"] for r in rows if r["size"]==size and r["operation"]==operation and r["implementation"]==implementation and not r["warmup"]]
            xs.sort(); values[implementation]={"median_ms":statistics.median(xs),"p95_ms":xs[int(.95*(len(xs)-1))],"min_ms":min(xs),"max_ms":max(xs)}
        summary.append({"size":size,"operation":operation,**values,
                        "median_ratio":values["duality"]["median_ms"]/values["direct"]["median_ms"]})
(OUT/"summary.json").write_text(json.dumps(summary,indent=2))
metadata={"run_id":RUN_ID,"postgresql":sql("SHOW server_version;",tuples=True).strip(),"platform":platform.platform(),"cpu_count":os.cpu_count(),"sizes":SIZES,"operations":OPERATIONS,"warmups":WARMUPS,"repetitions":REPETITIONS,"jit":"off","parallel_gather_workers":0,"database":DB,"pg_host":args.pg_host,"pg_port":str(args.pg_port),"pg_user":args.pg_user,"backend_mode":"one persistent psql backend for all measurements","source_commit":source_commit,"source_dirty":source_dirty,"product_hashes":product_hashes()}
(OUT/"environment.json").write_text(json.dumps(metadata,indent=2))
verification = verify_results(OUT)
if args.drop_database_after:
    run([PG / "dropdb", DB])
print(json.dumps({"run_id":RUN_ID,"output":str(OUT),**verification},indent=2))
