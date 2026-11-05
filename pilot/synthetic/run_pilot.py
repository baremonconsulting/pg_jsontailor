#!/usr/bin/env python3
"""Run the isolated synthetic client-pilot rehearsal and emit auditable evidence."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "pilot" / "synthetic"
DOCS = PILOT / "documents"
RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
parser = argparse.ArgumentParser()
parser.add_argument("--pg-bin", type=Path,
                    default=Path(os.environ["PG_JSONTAILOR_PG_BIN"])
                    if os.environ.get("PG_JSONTAILOR_PG_BIN")
                    else Path(shutil.which("pg_config") or "/usr/bin/pg_config").parent)
parser.add_argument("--pg-host", default=os.environ.get("PGHOST", str(ROOT / ".run/socket")))
parser.add_argument("--pg-port", default=os.environ.get("PGPORT", "55433"))
parser.add_argument("--pg-user", default=os.environ.get(
    "PGUSER", os.environ.get("USER", "postgres")))
parser.add_argument("--postgrest", type=Path,
                    default=Path(os.environ["PG_JSONTAILOR_POSTGREST"])
                    if os.environ.get("PG_JSONTAILOR_POSTGREST")
                    else Path(shutil.which("postgrest") or
                              ROOT / ".tools/postgrest-v16.5/postgrest"))
parser.add_argument("--postgrest-db-host", default="127.0.0.1")
parser.add_argument("--rest-host", default="127.0.0.1")
parser.add_argument("--rest-port", type=int, default=3060)
parser.add_argument("--admin-port", type=int, default=3061)
parser.add_argument("--database", default="jsontailor_synthetic_pilot")
parser.add_argument("--restore-database", default="jsontailor_synthetic_pilot_restore")
parser.add_argument("--output", type=Path)
args = parser.parse_args()

OUT = args.output or ROOT / "results" / f"pilot-{RUN_ID}"
RAW = OUT / "raw"
DB = args.database
RESTORE_DB = args.restore_database
REST_ORIGIN = f"http://{args.rest_host}:{args.rest_port}"
BASE = REST_ORIGIN + "/orders_dv"
ADMIN_URL = f"http://{args.rest_host}:{args.admin_port}/ready"
PG = args.pg_bin
PGRST = args.postgrest
ENV = {**os.environ, "PGHOST": args.pg_host, "PGPORT": str(args.pg_port),
       "PGUSER": args.pg_user}

if DB in {"postgres", "template0", "template1"} or RESTORE_DB in {
        "postgres", "template0", "template1"} or DB == RESTORE_DB:
    parser.error("pilot databases must be distinct dedicated disposable databases")

OUT.mkdir(parents=True)
RAW.mkdir()
documents = [json.loads(path.read_text()) for path in sorted(DOCS.glob("*.json"))]
events: list[dict] = []
checks: list[dict] = []


def run(args, *, input_text=None, check=True, env=ENV):
    result = subprocess.run([str(a) for a in args], input=input_text, text=True,
                            capture_output=True, env=env)
    if check and result.returncode:
        raise RuntimeError(f"command failed {args}:\n{result.stdout}\n{result.stderr}")
    return result


def sql(text, database=DB, check=True):
    return run([PG / "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database],
               input_text=text, check=check)


def scalar(text, database=DB):
    result = run([PG / "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", database],
                 input_text=text)
    return result.stdout.strip()


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def normalise(doc):
    value = copy.deepcopy(doc)
    value.pop("_metadata", None)
    value.pop("links", None)
    value["created"] = str(value["created"])[:10]
    for item in value.get("items", []):
        item["price"] = float(item["price"])
    return value


def http(label, method="GET", query="", document=None, prefer=False, raw_body=False):
    body = None if document is None else (document if raw_body else {"data": document})
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Accept": "application/json"}
    if payload is not None: headers["Content-Type"] = "application/json"
    if prefer: headers["Prefer"] = "return=representation"
    req = urllib.request.Request(BASE + query, data=payload, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status, response_headers, raw = response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as error:
        status, response_headers, raw = error.code, dict(error.headers), error.read().decode()
    elapsed_ms = (time.perf_counter() - started) * 1000
    try: parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError: parsed = raw
    events.append(dict(label=label, method=method, url=BASE + query, request=body,
                       status=status, response_headers=response_headers,
                       response=parsed, elapsed_ms=round(elapsed_ms, 3)))
    return status, parsed, elapsed_ms


def add_check(name, passed, observed, expected):
    checks.append(dict(name=name, status="PASS" if passed else "FAIL",
                       expected=expected, observed=observed))


# Disposable database and schema.
run([PG / "dropdb", "--if-exists", DB])
run([PG / "dropdb", "--if-exists", RESTORE_DB])
run([PG / "createdb", DB])
sql("CREATE EXTENSION pg_jsontailor; CREATE SCHEMA pilot; CREATE SCHEMA pilot_api;\n"
    "CREATE TABLE pilot.customers(customer_id bigint PRIMARY KEY,name text NOT NULL);\n"
    "CREATE TABLE pilot.orders(order_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,customer_id bigint REFERENCES pilot.customers,status text NOT NULL DEFAULT 'new',created_at date NOT NULL DEFAULT CURRENT_DATE);\n"
    "CREATE TABLE pilot.order_items(item_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,order_id bigint NOT NULL REFERENCES pilot.orders,product text NOT NULL,quantity integer NOT NULL CHECK(quantity>0),unit_price numeric(12,2) NOT NULL);\n"
    "INSERT INTO pilot.customers VALUES(7,'ACME'),(8,'Globex'),(9,'Northwind');")

for doc in documents:
    sql(f"INSERT INTO pilot.orders(order_id,customer_id,status,created_at) VALUES({doc['_id']},{doc['customer']['customerId']},{literal(doc['status'])},{literal(doc['created'])}::date);\n" +
        "\n".join(f"INSERT INTO pilot.order_items(item_id,order_id,product,quantity,unit_price) VALUES({i['itemId']},{doc['_id']},{literal(i['product'])},{i['qty']},{i['price']});" for i in doc["items"]))
sql("SELECT setval(pg_get_serial_sequence('pilot.orders','order_id'),1000); SELECT setval(pg_get_serial_sequence('pilot.order_items','item_id'),10000);")
spec = (PILOT / "spec.json").read_text()
sql(f"SELECT jsontailor.create_duality_view('pilot_api.orders_dv',{literal(spec)}::jsonb);")

# Least-privilege PostgREST roles; base tables remain unexposed by REST schema cache.
sql("""
DO $$BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jt_pilot_anon') THEN CREATE ROLE jt_pilot_anon NOLOGIN; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jt_pilot_authenticator') THEN CREATE ROLE jt_pilot_authenticator LOGIN PASSWORD 'pilot-local-only'; END IF;
END$$;
GRANT jt_pilot_anon TO jt_pilot_authenticator;
GRANT USAGE ON SCHEMA pilot,pilot_api TO jt_pilot_anon;
GRANT SELECT,INSERT,UPDATE,DELETE ON pilot_api.orders_dv TO jt_pilot_anon;
GRANT SELECT,INSERT,UPDATE,DELETE ON pilot.orders,pilot.order_items TO jt_pilot_anon;
GRANT SELECT ON pilot.customers TO jt_pilot_anon;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA pilot TO jt_pilot_anon;
""")

# Snapshot before the rehearsal is the rollback artefact.
dump = RAW / "pre_pilot.dump"
run([PG / "pg_dump", "-Fc", "-d", DB, "-f", dump])

log_handle = (RAW / "postgrest.log").open("w")
postgrest_config = RAW / "postgrest.conf"
postgrest_uri = (f"postgresql://jt_pilot_authenticator:pilot-local-only@"
                 f"{args.postgrest_db_host}:{args.pg_port}/{DB}")
postgrest_config.write_text(
    f"db-uri = {json.dumps(postgrest_uri)}\n"
    'db-schemas = "pilot_api"\n'
    'db-anon-role = "jt_pilot_anon"\n'
    f"server-host = {json.dumps(args.rest_host)}\n"
    f"server-port = {args.rest_port}\n"
    f"admin-server-port = {args.admin_port}\n"
    'log-level = "warn"\n')
server = subprocess.Popen([str(PGRST), str(postgrest_config)], stdout=log_handle,
                          stderr=subprocess.STDOUT, env=ENV)
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(ADMIN_URL, timeout=.2)
            break
        except Exception:
            time.sleep(.1)
    else: raise RuntimeError("PostgREST did not become ready")

    # Exact read-back of all five documents.
    for expected in documents:
        status, rows, _ = http(f"read document {expected['_id']}", query=f"?_id=eq.{expected['_id']}")
        observed = rows[0]["data"] if status == 200 and len(rows) == 1 else None
        add_check(f"read-{expected['_id']}", status == 200 and normalise(observed) == normalise(expected),
                  normalise(observed) if observed else observed, normalise(expected))

    # Filter/paging.
    status, rows, _ = http("filter and paging", query="?data-%3E%3Estatus=eq.fulfilled&limit=1&offset=0")
    add_check("filter-paging", status == 200 and len(rows) == 1 and rows[0]["_id"] == 203,
              {"status":status,"ids":[r.get("_id") for r in rows]}, "200 and [203]")

    # Insert generated keys.
    inserted = {"status":"new","customer":{"customerId":8},"items":[{"product":"Generated","qty":1,"price":3.25}]}
    status, body, _ = http("insert generated keys", "POST", document=inserted, prefer=True)
    generated_id = body[0]["_id"] if status == 201 else None
    add_check("insert", status == 201 and generated_id is not None, {"status":status,"id":generated_id}, "201 and generated id")

    # Replacement and relational state.
    _, rows, _ = http("read for replacement", query="?_id=eq.202")
    current = rows[0]["data"]
    current["status"] = "approved"
    current["items"][0]["qty"] = 4
    current["items"].append({"product":"Nová položka","qty":1,"price":8.75})
    status, _, _ = http("replace current document", "PATCH", "?_id=eq.202", current, True)
    relational = scalar("SELECT status||':'||(SELECT sum(quantity)::text FROM pilot.order_items WHERE order_id=202) FROM pilot.orders WHERE order_id=202;")
    add_check("replacement", status == 200 and relational == "approved:5", {"status":status,"state":relational}, "200, approved, quantity 5")

    # Optimistic concurrency: same version, first wins.
    _, rows, _ = http("client A read", query="?_id=eq.203"); a = rows[0]["data"]
    b = copy.deepcopy(a); a["status"]="client-a"; b["status"]="client-b"
    sa, _, _ = http("client A write", "PATCH", "?_id=eq.203", a, True)
    sb, _, _ = http("client B stale write", "PATCH", "?_id=eq.203", b, True)
    add_check("optimistic-concurrency", (sa,sb)==(200,412), [sa,sb], [200,412])

    # Error states.
    _, rows, _ = http("read for validation", query="?_id=eq.204"); valid=rows[0]["data"]
    no_etag=copy.deepcopy(valid); no_etag.pop("_metadata")
    missing=copy.deepcopy(valid); missing.pop("status")
    unknown=copy.deepcopy(valid); unknown["unexpected"]=True
    se,_,_=http("missing ETag", "PATCH", "?_id=eq.204", no_etag, True)
    sm,_,_=http("missing field", "PATCH", "?_id=eq.204", missing, True)
    su,_,_=http("unknown field", "PATCH", "?_id=eq.204", unknown, True)
    add_check("error-contract", [se,sm,su]==[428,422,422], [se,sm,su], [428,422,422])

    # Base tables are not REST resources.
    old_base=BASE; BASE = REST_ORIGIN + "/orders"
    hidden_status,_,_=http("base table hidden")
    BASE=old_base
    add_check("rest-surface", hidden_status==404, hidden_status, 404)

    # Delete one of the five documents through the ETag-aware RPC.
    _, rows, _ = http("read document 205 for deletion", query="?_id=eq.205")
    delete_etag = rows[0]["data"]["_metadata"]["etag"]
    old_base = BASE
    BASE = REST_ORIGIN + "/rpc/orders_dv__delete_document"
    sd,_,_=http("delete document 205", "POST", document={
        "expected_id": 205, "expected_etag": delete_etag}, raw_body=True)
    BASE = old_base
    left=scalar("SELECT count(*) FROM pilot.orders WHERE order_id=205;")
    add_check("delete", sd==200 and left == "0", {"status":sd,"rows":left}, "200 and zero rows")

    # Small operational sample, not a benchmark claim.
    operational_samples=[]
    for n in range(20):
        status,rows,elapsed=http(f"operational read {n+1}", query="?_id=eq.201")
        valid = (status == 200 and isinstance(rows,list) and len(rows) == 1
                 and rows[0].get("_id") == 201 and isinstance(rows[0].get("data"),dict))
        operational_samples.append({"status":status,"valid":valid,"elapsed_ms":elapsed})
    add_check("operational-read-sample",
              all(sample["valid"] and sample["elapsed_ms"] < 1000
                  for sample in operational_samples),
              {"statuses":[sample["status"] for sample in operational_samples],
               "valid_documents":sum(sample["valid"] for sample in operational_samples),
               "median_ms":round(statistics.median(
                   sample["elapsed_ms"] for sample in operational_samples),3),
               "max_ms":round(max(
                   sample["elapsed_ms"] for sample in operational_samples),3)},
              "20 successful valid local reads under 1000 ms")
finally:
    server.terminate()
    try: server.wait(timeout=5)
    except subprocess.TimeoutExpired: server.kill()
    log_handle.close()

# Roll back by restoring the pre-pilot snapshot into a fresh database.
run([PG / "createdb", RESTORE_DB])
restore = run([PG / "pg_restore", "--no-owner", "-d", RESTORE_DB, dump], check=False)
(RAW / "restore.log").write_text(restore.stdout + restore.stderr)
restored = run([PG / "psql", "-X", "-A", "-t", "-d", RESTORE_DB],
               input_text="SELECT count(*)||':'||(SELECT count(*) FROM pilot.order_items)||':'||(SELECT count(*) FROM pilot_api.orders_dv) FROM pilot.orders;", check=False)
restore_value = restored.stdout.strip()
add_check("rollback-restore", restore.returncode == 0 and restore_value.endswith("5:10:5"),
          {"restore_rc":restore.returncode,"state":restore_value}, "restore rc 0 and 5:10:5")

(RAW / "http_transcript.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))
(OUT / "test_results.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2))
failures=[c for c in checks if c["status"]!="PASS"]
decision="GO" if not failures else "NO-GO"
summary={"run_id":RUN_ID,"scope":"synthetic pre-pilot","documents":5,"checks":len(checks),
         "passed":len(checks)-len(failures),"failed":len(failures),"decision":decision,
         "limitation":"Synthetic evidence does not replace validation with anonymised client data."}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({**summary,"output":str(OUT)},ensure_ascii=False,indent=2))
raise SystemExit(0 if decision=="GO" else 1)
