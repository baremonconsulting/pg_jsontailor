#!/usr/bin/env python3
"""Execute generated-SQL mutants and require behavioural suites to kill them."""
from __future__ import annotations
import argparse, json, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import result_rows, validate

MUTATIONS = {
 "etag": ("IF cur IS NULL OR cur#>>'{_metadata,etag}' IS DISTINCT FROM doc#>>'{_metadata,etag}' THEN", "IF cur IS NULL THEN"),
 "locks": ("FOR SHARE OF o", ""),
 "rls": ("SECURITY INVOKER", "SECURITY DEFINER"),
 "type-checks": ("IF jsonb_typeof(doc->bad) IS DISTINCT FROM sql_kind", "IF false AND jsonb_typeof(doc->bad) IS DISTINCT FROM sql_kind"),
 "binding-guards": ("guard_body:=format('BEGIN %s %s RETURN NULL; END',source_lock,guard_checks);", "guard_body:='BEGIN RETURN NULL; END';"),
 "atomic-reconciliation": ("GET DIAGNOSTICS n=ROW_COUNT;IF n<>expected THEN", "GET DIAGNOSTICS n=ROW_COUNT;IF false THEN"),
}
EXPECTED_IDS = {
 "validation_security.py": {"6","7","8","9","10","12","14","15","16","17","18","19","20"},
 "security_contract_matrix.py": {"PGJT-46/source-privilege-matrix","PGJT-46/membership-column-grants","PGJT-46/owner-force-rls","PGJT-46/root-rls-matrix","PGJT-46/object-etag-lock-matrix","PGJT-46/array-rls-matrix","PGJT-46/policy-change-atomic-denial","PGJT-46/invoker-metadata"},
}

def sql(psql,database,text):
 return subprocess.run([psql,"-XAt","-v","ON_ERROR_STOP=1","-d",database],input="\\set VERBOSITY verbose\n"+text,text=True,capture_output=True)

def load(args,source):
 result=sql(args.psql,args.database,"DROP EXTENSION IF EXISTS pg_jsontailor CASCADE;DROP SCHEMA IF EXISTS jsontailor CASCADE;CREATE SCHEMA jsontailor;\n"+source)
 if result.returncode: raise RuntimeError(result.stderr)

def suites(args,directory,label):
 results=[]
 for script in ("validation_security.py","security_contract_matrix.py"):
  output=directory/f"{label}-{script}.jsonl"
  cmd=[sys.executable,str(args.repo/"tests"/script),"--psql",args.psql,"--database",args.database,"--output",str(output)]
  if script=="validation_security.py": cmd += ["--schema",str(args.repo/"examples/shop_schema.sql"),"--spec",str(args.repo/"examples/orders_dv.spec.json")]
  result=subprocess.run(cmd,text=True,capture_output=True)
  if not output.is_file(): raise RuntimeError(f"{script} produced no structured evidence: {result.stderr[-500:]}")
  rows=validate(result_rows(output), {'validation_security.py':'validation','security_contract_matrix.py':'security'}[script], allow_failures=True)
  if not rows or any(row.get("status") not in {"PASS","FAIL"} for row in rows): raise RuntimeError(f"{script} produced invalid structured evidence")
  identifiers=[str(row.get("id",row.get("name",row.get("test_id")))) for row in rows]
  if len(identifiers)!=len(set(identifiers)) or set(identifiers)!=EXPECTED_IDS[script]: raise RuntimeError(f"{script} produced an incomplete evidence inventory")
  failed=[row.get("id",row.get("name",row.get("test_id"))) for row in rows if row.get("status") == "FAIL"]
  if result.returncode and not failed: raise RuntimeError(f"{script} failed without a failing invariant: {result.stderr[-500:]}")
  results.append((script,failed))
 return all(not failed for _,failed in results),repr(results)

def setup_example(args):
 spec=(args.repo/"examples/orders_dv.spec.json").read_text().replace("'","''")
 setup=(args.repo/"examples/shop_schema.sql").read_text()+"\nSELECT jsontailor.create_duality_view('shop.orders_dv','"+spec+"'::jsonb);"
 result=sql(args.psql,args.database,setup)
 if result.returncode: raise RuntimeError(result.stderr)

def protected_probe(args,name):
 setup_example(args)
 if name=="etag":
  statement="""CREATE TEMP TABLE stale AS SELECT data FROM shop.orders_dv WHERE _id=42;
UPDATE shop.orders SET status='concurrent' WHERE order_id=42;
UPDATE shop.orders_dv SET data=(SELECT data FROM stale) WHERE _id=42;"""
 elif name=="type-checks":
  statement="""UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','123'::jsonb)
WHERE _id=42;"""
 elif name=="binding-guards":
  statement="""ALTER TABLE shop.orders RENAME CONSTRAINT orders_pkey TO changed_orders_pkey;
UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','\"changed\"'::jsonb)
WHERE _id=42;"""
 elif name=="rls":
  statement="""DO $$BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jt50_limited') THEN CREATE ROLE jt50_limited;END IF;END$$;
GRANT USAGE ON SCHEMA shop TO jt50_limited;
GRANT SELECT,INSERT ON shop.orders_dv TO jt50_limited;
GRANT SELECT,INSERT,UPDATE,DELETE ON shop.orders,shop.customers,shop.order_items TO jt50_limited;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA shop TO jt50_limited;
ALTER TABLE shop.orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS jt50_deny ON shop.orders;
CREATE POLICY jt50_deny ON shop.orders FOR ALL TO jt50_limited USING(true) WITH CHECK(true);
SET ROLE jt50_limited;
INSERT INTO shop.orders_dv(data) VALUES('{"_id":8999,"status":"allowed","created":"2026-11-06","customer":{"customerId":7},"items":[]}'::jsonb);
RESET ROLE;
DROP POLICY jt50_deny ON shop.orders;
CREATE POLICY jt50_deny ON shop.orders FOR ALL TO jt50_limited USING(false) WITH CHECK(false);
SET ROLE jt50_limited;
INSERT INTO shop.orders_dv(data) VALUES('{"_id":9000,"status":"blocked","created":"2026-11-06","customer":{"customerId":7},"items":[]}'::jsonb);"""
 else: raise ValueError(name)
 result=sql(args.psql,args.database,statement)
 if name=="rls":
  allowed=sql(args.psql,args.database,"SELECT count(*) FROM shop.orders WHERE order_id=8999;")
  if not allowed.stdout.strip().endswith("1"): raise RuntimeError("RLS positive-policy control did not persist its source row: "+result.stderr[-500:])
  if result.returncode:
   unchanged=sql(args.psql,args.database,"SELECT count(*) FROM shop.orders WHERE order_id=9000;")
   if "42501" not in result.stderr or "row-level security" not in result.stderr or not unchanged.stdout.strip().endswith("0"):
    raise RuntimeError("RLS probe failed outside the expected deny-policy invariant: "+result.stderr[-500:])
   return True,result.stderr[-500:]
  readback=sql(args.psql,args.database,"SELECT count(*) FROM shop.orders WHERE order_id=9000;")
  if not readback.stdout.strip().endswith("1"): raise RuntimeError("RLS mutant produced no source-row readback")
 return result.returncode != 0,result.stderr[-500:]

def main():
 p=argparse.ArgumentParser(); p.add_argument("--psql",default="psql");p.add_argument("--database",required=True)
 p.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument("--output",type=Path,required=True)
 args=p.parse_args(); source="\n".join((args.repo/"sql/pg_jsontailor--0.2.0.sql").read_text().splitlines()[1:])+"\n";rows=[]
 with tempfile.TemporaryDirectory(prefix="pgjt50-") as temp:
  directory=Path(temp);load(args,source);control,detail=suites(args,directory,"control")
  if not control: raise RuntimeError("control suites failed: "+detail)
  for protected in ("etag","type-checks","binding-guards","rls"):
   load(args,source);blocked,probe_detail=protected_probe(args,protected)
   if not blocked: raise RuntimeError("control did not block "+protected+": "+probe_detail)
  for name,(needle,replacement) in MUTATIONS.items():
   matches=source.count(needle);compiled=True
   try: load(args,source.replace(needle,replacement))
   except RuntimeError: compiled=False
   survived=False;detail="mutant did not compile"
   if compiled and name in {"etag","type-checks","binding-guards","rls"}:
    blocked,detail=protected_probe(args,name);survived=blocked
   elif compiled: survived,detail=suites(args,directory,name)
   killed=matches>0 and compiled and not survived
   rows.append({"id":"PGJT-50/mutation-"+name,"status":"PASS" if control and killed else "FAIL","operator_matches":matches,"mutant_compiled":compiled,"mutant_killed":killed,"detail":detail})
 args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text("".join(json.dumps(x,sort_keys=True)+"\n" for x in rows))
 return 0 if all(x["status"]=="PASS" for x in rows) else 1
if __name__=="__main__":raise SystemExit(main())
