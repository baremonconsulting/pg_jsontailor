#!/usr/bin/env python3
"""Exercise specification validation, security and edge cases.

Unsupported variants are recorded as FAIL with an explicit observation. This
keeps the validation complete without misrepresenting the V1 thin slice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
from concurrent_writer import held_writer, require_commit, wait_for


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Runner:
    def __init__(self, psql, database, schema, spec):
        self.psql, self.database = psql, database
        self.schema, self.spec = schema, spec
        self.rows = []

    def cmd(self):
        return [self.psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", self.database]

    def run(self, sql, check=True):
        p = subprocess.run(self.cmd(), input="\\set VERBOSITY verbose\n" + sql,
                           text=True, capture_output=True, timeout=15)
        if check and p.returncode:
            raise RuntimeError(p.stderr)
        return p

    def scalar(self, sql):
        return self.run(sql).stdout.strip().splitlines()[-1]

    def state(self, p):
        m = re.search(r"ERROR:\s+([0-9A-Z]{5}):", p.stderr)
        return m.group(1) if m else None

    def reset(self, specification=None, view="shop.orders_dv"):
        p = subprocess.run(self.cmd() + ["-f", str(self.schema)], text=True, capture_output=True)
        if p.returncode: raise RuntimeError(p.stderr)
        value = self.spec.read_text() if specification is None else json.dumps(specification)
        self.run("SELECT jsontailor.create_duality_view(" + lit(view) + "," + lit(value) + "::jsonb);")
        if view == 'shop.orders_dv' and json.loads(value).get('array'):
            self.run("DO $$BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jt_test_data') THEN "
                     "PERFORM jsontailor.grant_array_reader('shop.orders_dv','jt_test_data',false); "
                     "PERFORM jsontailor.grant_delete_rpc('shop.orders_dv','jt_test_data',false); "
                     "END IF;END$$;")

    def add(self, ident, variant, status, expected, observed, notes=""):
        self.rows.append({"id": str(ident), "variant": variant, "status": status,
                          "expected": expected, "observed": observed,
                          "evidence": "tests/validation_security.py", "notes": notes})

    def error_for_doc(self, expression):
        return self.state(self.run(f"UPDATE shop.orders_dv SET data={expression} WHERE _id=42;", False))

    def delete_sql(self, view="shop.orders_dv", ident="42"):
        function = view + "__delete_document"
        return (f"SELECT {function}({ident},"
                f"(SELECT data#>>'{{_metadata,etag}}' FROM {view} WHERE _id={ident}));")

    def test6(self):
        self.reset()
        states = {
            "unknown_root": self.error_for_doc("data || '{\"unknown\":1}'::jsonb"),
            "items_null": self.error_for_doc("jsonb_set(data,'{items}','null'::jsonb)"),
            "changed_id": self.error_for_doc("jsonb_set(data,'{_id}','43'::jsonb)"),
            "duplicate_key": self.error_for_doc("jsonb_set(data,'{items}',(data->'items') || (data->'items'->0))"),
        }
        passed=all(v=="PT422" for v in states.values())
        v2=json.loads(self.spec.read_text())
        for part in (v2["root"],v2["array"]):
            for op in ("insert","update","delete"): part[op]=False
        self.reset(v2); forbidden={
            "insert":self.state(self.run("INSERT INTO shop.orders_dv(data) VALUES ('{}');",False)),
            "update":self.error_for_doc("jsonb_set(data,'{status}','\"x\"')"),
            "delete":self.state(self.run("DELETE FROM shop.orders_dv WHERE _id=42;",False))}
        passed &= all(v=="PT422" for v in forbidden.values())
        v3={"root":{"table":"shop.orders","key":"order_id","fields":{"status":"status"},"update":True}}
        self.reset(v3); v3state=self.state(self.run("UPDATE shop.orders_dv SET data=data||'{\"unknown\":1}' WHERE _id=42;",False)); passed &= v3state=="PT422"
        states.update({"v2_"+k:v for k,v in forbidden.items()});states["v3_unknown"]=v3state
        self.add(6, "V1,V2,V3,V6", "PASS" if passed else "FAIL", "document-rule and disabled-operation samples return PT422", states,
                 "The runner covers every document level and all disabled root operations; detailed cases remain visible in evidence.")

    def test7(self):
        observations=[]; passed=True
        self.reset(); doc=json.loads(self.scalar("SELECT data::text FROM shop.orders_dv WHERE _id=42;"))
        self.run("UPDATE shop.customers SET name='ACME Ltd' WHERE customer_id=7;")
        doc["status"]="changed"
        v1=self.run("UPDATE shop.orders_dv SET data="+lit(json.dumps(doc))+"::jsonb WHERE _id=42;",False)
        shown=self.scalar("SELECT data#>>'{customer,name}' FROM shop.orders_dv WHERE _id=42;")
        observations.append(f"v1_nocheck={v1.returncode}/{shown}"); passed &= v1.returncode==0 and shown=="ACME Ltd"
        v6=json.loads(self.spec.read_text()); v6["object"]["etag"]=True
        self.reset(v6); stale=json.loads(self.scalar("SELECT data::text FROM shop.orders_dv WHERE _id=42;"))
        self.run("UPDATE shop.customers SET name='ACME Ltd' WHERE customer_id=7;")
        stale["status"]="changed"; p=self.run("UPDATE shop.orders_dv SET data="+lit(json.dumps(stale))+"::jsonb WHERE _id=42;",False)
        observations.append("v6_stale="+str(self.state(p))); passed &= self.state(p)=="PT412"
        fresh=json.loads(self.scalar("SELECT data::text FROM shop.orders_dv WHERE _id=42;")); fresh["customer"]["name"]="wrong"
        p=self.run("UPDATE shop.orders_dv SET data="+lit(json.dumps(fresh))+"::jsonb WHERE _id=42;",False)
        observations.append("v6_mismatch="+str(self.state(p))); passed &= self.state(p)=="PT422"
        self.add(7,"V1,V6","PASS" if passed else "FAIL","object NOCHECK and checked-object ETag semantics",";".join(observations))

    def test8(self):
        obs=[]; passed=True
        self.reset(); etag=self.scalar("SELECT data#>>'{_metadata,etag}' FROM shop.orders_dv WHERE _id=42;")
        mismatch=self.run("DELETE FROM shop.orders_dv WHERE _id=42 AND data#>>'{_metadata,etag}'='wrong';",False)
        deleted=self.run("DELETE FROM shop.orders_dv WHERE _id=42 AND data#>>'{_metadata,etag}'="+lit(etag)+";",False); remain=self.scalar("SELECT count(*) FROM shop.order_items WHERE order_id=42;")
        obs.append(f"v1={mismatch.returncode}/{deleted.returncode}/{remain}"); passed &= mismatch.returncode==0 and deleted.returncode==0 and remain=="0"
        v4=json.loads(self.spec.read_text()); v4["array"]["delete"]=False
        self.reset(v4); p=self.run(self.delete_sql(),False); state=self.state(p)
        obs.append("not_null="+str(state)); passed &= state=="23502"
        self.reset();self.run("DELETE FROM shop.order_items WHERE order_id=42;CREATE TABLE shop.notes(note_id bigint PRIMARY KEY,order_id bigint REFERENCES shop.orders(order_id),value text);INSERT INTO shop.notes VALUES(1,42,'n');")
        nv={"root":{"table":"shop.orders","key":"order_id","fields":{"status":"status"},"delete":True},"array":{"name":"notes","table":"shop.notes","key":"note_id","parent_fk":"order_id","fields":{"noteId":"note_id","value":"value"},"delete":False}}
        self.run("SELECT jsontailor.create_duality_view('shop.notes_dv',"+lit(json.dumps(nv))+"::jsonb);");p=self.run(self.delete_sql("shop.notes_dv"),False);unlinked=self.scalar("SELECT count(*) FROM shop.notes WHERE note_id=1 AND order_id IS NULL;")
        obs.append(f"v4={p.returncode}/{self.state(p)}/{unlinked}");passed &= p.returncode==0 and unlinked=="1"
        v2=json.loads(self.spec.read_text()); v2["root"]["delete"]=False
        self.reset(v2); state=self.state(self.run(self.delete_sql(),False)); obs.append("forbidden="+str(state)); passed &= state=="PT422"
        self.reset(); self.run("DELETE FROM shop.order_items WHERE order_id=42; DELETE FROM shop.orders WHERE order_id=42;"); count=self.scalar("SELECT count(*) FROM shop.orders_dv WHERE _id=42;")
        obs.append("absent="+count); passed &= count=="0"
        self.add(8,"V1,V4","PASS" if passed else "FAIL","delete, unlink/NOT NULL, absent and forbidden cases",";".join(obs))

    def test9(self):
        observations=[]; passed=True
        for name, expr, expected in [
            ("check","jsonb_set(data,'{items,0,qty}','0'::jsonb)","23514"),
            ("not_null","jsonb_set(data,'{status}','null'::jsonb)","23502"),
            ("foreign_key","jsonb_set(data,'{customer}','{\"customerId\":999}'::jsonb)","23503")]:
            self.reset(); state=self.error_for_doc(expr); observations.append(f"{name}:{state}"); passed &= state==expected
        self.add(9,"V1","PASS" if passed else "FAIL","23514,23502,23503",";".join(observations))

    def test10(self):
        self.reset()
        self.run("DO $$BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jt_test_viewonly') THEN CREATE ROLE jt_test_viewonly NOLOGIN; END IF; IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jt_test_data') THEN CREATE ROLE jt_test_data NOLOGIN; END IF; END$$; GRANT USAGE ON SCHEMA shop TO jt_test_viewonly,jt_test_data; GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_viewonly,jt_test_data; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data; GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA shop TO jt_test_data;")
        self.run("SELECT jsontailor.grant_array_reader('shop.orders_dv','jt_test_data',false);")
        self.run("SELECT jsontailor.grant_delete_rpc('shop.orders_dv','jt_test_data',false);")
        denied=self.run("SET ROLE jt_test_viewonly; SELECT * FROM shop.orders_dv;",False)
        obs=["view_only="+str(self.state(denied))];passed=self.state(denied)=="42501"
        allowed=self.run("SET ROLE jt_test_data;SELECT count(*) FROM shop.orders_dv;RESET ROLE;",False);obs.append("table_grants="+str(allowed.returncode));passed &= allowed.returncode==0
        # c: a SELECT-hidden child is absent, survives replacement, and blocks root deletion by FK.
        self.run("ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;CREATE POLICY c_sel ON shop.order_items FOR SELECT TO jt_test_data USING(item_id<>2);CREATE POLICY c_upd ON shop.order_items FOR UPDATE TO jt_test_data USING(true);CREATE POLICY c_del ON shop.order_items FOR DELETE TO jt_test_data USING(true);")
        visible=self.scalar("SET ROLE jt_test_data;SELECT jsonb_array_length(data->'items') FROM shop.orders_dv WHERE _id=42;")
        p=self.run("SET ROLE jt_test_data;UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','\"rls\"') WHERE _id=42;RESET ROLE;",False); hidden=self.scalar("SELECT count(*) FROM shop.order_items WHERE item_id=2;")
        d=self.run("SET ROLE jt_test_data;"+self.delete_sql(),False);obs.append(f"c={visible}/{p.returncode}/{hidden}/{self.state(d)}");passed &= visible=="1" and p.returncode==0 and hidden=="1" and self.state(d)=="23503"
        # d: hidden referenced object reads null and an unrelated update preserves the FK.
        self.reset();self.run("GRANT USAGE ON SCHEMA shop TO jt_test_data;GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data;GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_data;ALTER TABLE shop.customers ENABLE ROW LEVEL SECURITY;CREATE POLICY d_sel ON shop.customers FOR SELECT TO jt_test_data USING(customer_id<>7);")
        obj=self.scalar("SET ROLE jt_test_data;SELECT jsonb_typeof(data->'customer') FROM shop.orders_dv WHERE _id=42;");p=self.run("SET ROLE jt_test_data;UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','\"rls\"') WHERE _id=42;",False);fk=self.scalar("SELECT customer_id FROM shop.orders WHERE order_id=42;");obs.append(f"d={obj}/{p.returncode}/{fk}");passed &= obj=="null" and p.returncode==0 and fk=="7"
        # f: root visible for SELECT but filtered by UPDATE; both replacement and delete fail atomically.
        self.reset();self.run("GRANT USAGE ON SCHEMA shop TO jt_test_data;GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data;GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_data;ALTER TABLE shop.orders ENABLE ROW LEVEL SECURITY;CREATE POLICY f_sel ON shop.orders FOR SELECT TO jt_test_data USING(true);CREATE POLICY f_upd ON shop.orders FOR UPDATE TO jt_test_data USING(false);CREATE POLICY f_del ON shop.orders FOR DELETE TO jt_test_data USING(true);")
        fu=self.run("SET ROLE jt_test_data;UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','\"blocked\"') WHERE _id=42;",False);fd=self.run("SET ROLE jt_test_data;"+self.delete_sql(),False);obs.append(f"f={self.state(fu)}/{self.state(fd)}");passed &= self.state(fu)=="42501" and self.state(fd)=="42501"
        self.reset();self.run("GRANT USAGE ON SCHEMA shop TO jt_test_data;GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data;GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_data;ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;CREATE POLICY fd_sel ON shop.order_items FOR SELECT TO jt_test_data USING(true);CREATE POLICY fd_upd ON shop.order_items FOR UPDATE TO jt_test_data USING(true);CREATE POLICY fd_del ON shop.order_items FOR DELETE TO jt_test_data USING(false);")
        fdu=self.run("SET ROLE jt_test_data;UPDATE shop.orders_dv SET data=jsonb_set(data,'{items}',(data->'items')-0) WHERE _id=42;",False);fdd=self.run("SET ROLE jt_test_data;"+self.delete_sql(),False);obs.append(f"f_child_delete={self.state(fdu)}/{self.state(fdd)}");passed &= self.state(fdu)=="42501" and self.state(fdd)=="42501"
        # e and g: hostile path; visible but non-lockable children.
        self.reset();self.run("GRANT USAGE ON SCHEMA shop TO jt_test_data;GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data;GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_data;CREATE SCHEMA IF NOT EXISTS rusive_schema;ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;CREATE POLICY g_sel ON shop.order_items FOR SELECT TO jt_test_data USING(true);CREATE POLICY g_upd ON shop.order_items FOR UPDATE TO jt_test_data USING(false);CREATE POLICY g_del ON shop.order_items FOR DELETE TO jt_test_data USING(true);")
        status_before=self.scalar("SELECT data FROM shop.orders_dv WHERE _id=42;")
        status=self.run("SET ROLE jt_test_data;SET search_path=rusive_schema,pg_catalog;UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','\"ok\"') WHERE _id=42;",False)
        status_after=self.scalar("SELECT data FROM shop.orders_dv WHERE _id=42;")
        self.reset();self.run("GRANT USAGE ON SCHEMA shop TO jt_test_data;GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data;GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_data;ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;CREATE POLICY g_sel ON shop.order_items FOR SELECT TO jt_test_data USING(true);CREATE POLICY g_upd ON shop.order_items FOR UPDATE TO jt_test_data USING(false);CREATE POLICY g_del ON shop.order_items FOR DELETE TO jt_test_data USING(true);")
        changed=self.run("SET ROLE jt_test_data;UPDATE shop.orders_dv SET data=jsonb_set(data,'{items,0,qty}','3') WHERE _id=42;",False);deleted=self.run("SET ROLE jt_test_data;"+self.delete_sql(),False)
        # g1: an uncommitted direct child change must not make the view deletion wait.
        expected = json.loads(self.scalar("SELECT data FROM shop.orders_dv WHERE _id=42;"))
        expected["items"][0]["qty"] = 9
        with held_writer(self.cmd(), self.scalar,
                         "UPDATE shop.order_items SET quantity=9 WHERE item_id=1;", "shop.order_items"):
            started=time.monotonic();g1=self.run("SET statement_timeout='2s';SET ROLE jt_test_data;UPDATE shop.orders_dv SET data=jsonb_set(data,'{items}',(data->'items')-0) WHERE _id=42;",False);elapsed1=time.monotonic()-started
        final = json.loads(self.scalar("SELECT data FROM shop.orders_dv WHERE _id=42;"))
        expected.pop("_metadata"); final.pop("_metadata")
        passed &= final == expected and self.scalar("SELECT quantity FROM shop.order_items WHERE item_id=1;") == "9"
        # g5: a root-only replacement locks the visible children contributing to its ETag.
        self.reset();self.run("GRANT USAGE ON SCHEMA shop TO jt_test_data;GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA shop TO jt_test_data;GRANT SELECT,UPDATE,INSERT,DELETE ON shop.orders_dv TO jt_test_data;ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;CREATE POLICY g_sel ON shop.order_items FOR SELECT TO jt_test_data USING(true);CREATE POLICY g_upd ON shop.order_items FOR UPDATE TO jt_test_data USING(true) WITH CHECK(true);CREATE POLICY g_del ON shop.order_items FOR DELETE TO jt_test_data USING(true);")
        expected = json.loads(self.scalar("SELECT data FROM shop.orders_dv WHERE _id=42;"))
        initial_etag = expected["_metadata"]["etag"]
        expected["status"] = "held"; expected["items"][0]["qty"] = 8
        contender_name = "jt_rls_contender_" + secrets.token_hex(6)
        contender = None
        started=time.monotonic()
        try:
            with held_writer(self.cmd(), self.scalar,
                             "SET ROLE jt_test_data;SET search_path=rusive_schema,pg_catalog;UPDATE shop.orders_dv SET data=jsonb_set(data,'{status}','\"held\"') WHERE _id=42;", "shop.orders"):
                contender = subprocess.Popen(self.cmd(), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                contender.stdin.write("\\set VERBOSITY verbose\n"
                    f"SET application_name='{contender_name}';SET statement_timeout='10s';"
                    "BEGIN;UPDATE shop.order_items SET quantity=8 WHERE item_id=1;COMMIT;\n")
                contender.stdin.flush()
                wait_for(self.scalar, contender,
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity c "
                    "CROSS JOIN LATERAL unnest(pg_blocking_pids(c.pid)) blocker_pid "
                    "JOIN pg_stat_activity b ON b.pid=blocker_pid "
                    f"WHERE c.application_name='{contender_name}' "
                    "AND b.application_name LIKE 'jt_writer_%');", "RLS contender")
            require_commit(contender, timeout=10)
            g5_state = "committed"
        finally:
            if contender is not None and contender.poll() is None:
                contender.terminate();contender.communicate(timeout=5)
        elapsed5=time.monotonic()-started
        final = json.loads(self.scalar("SELECT data FROM shop.orders_dv WHERE _id=42;"))
        final_etag = final["_metadata"]["etag"]
        expected.pop("_metadata"); final.pop("_metadata")
        passed &= final == expected and initial_etag != final_etag and self.scalar("SELECT status||':'||(SELECT quantity FROM shop.order_items WHERE item_id=1) FROM shop.orders WHERE order_id=42;") == "held:8"
        obs.append(f"e_g={self.state(status)}/{self.state(changed)}/{self.state(deleted)};g1={self.state(g1)}/{elapsed1:.3f};g5={g5_state}/{elapsed5:.3f}")
        passed &= self.state(status)=="42501" and status_before==status_after and self.state(changed)=="42501" and self.state(deleted)=="42501" and self.state(g1)=="42501" and g5_state=="committed"
        self.add(10,"V1/RLS","PASS" if passed else "FAIL","permissions, hidden rows/object, hostile path and non-lockable rows",";".join(obs),"RLS policy matrices are recreated between subcases; two-session races are covered by test 5 and the isolation suite.")

    def test12(self):
        self.reset()
        supports_not_enforced = int(self.scalar("SHOW server_version_num;")) >= 180000
        not_enforced_ddl = (
            "CREATE TABLE jt_bad.not_enforced(id integer PRIMARY KEY,"
            "parent integer REFERENCES jt_bad.parent(id) NOT ENFORCED);"
            if supports_not_enforced else ""
        )
        self.run("""DROP SCHEMA IF EXISTS jt_bad CASCADE;CREATE SCHEMA jt_bad;
CREATE TABLE jt_bad.gen(id integer PRIMARY KEY,g integer GENERATED ALWAYS AS (id+1) STORED,u jsonb);
CREATE TABLE jt_bad.ident(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY);
CREATE TABLE jt_bad.parent(id integer PRIMARY KEY,fk integer);CREATE TABLE jt_bad.ref(id integer PRIMARY KEY);
CREATE TABLE jt_bad.parent_def(id integer PRIMARY KEY,fk integer REFERENCES jt_bad.ref(id) DEFERRABLE);
CREATE TABLE jt_bad.child(id integer PRIMARY KEY,parent integer,CONSTRAINT fk_def FOREIGN KEY(parent) REFERENCES jt_bad.parent(id) DEFERRABLE);
CREATE TABLE jt_bad.cascade(id integer PRIMARY KEY,parent integer REFERENCES jt_bad.parent(id) ON DELETE CASCADE);
""" + not_enforced_ddl + """
CREATE TABLE jt_bad.order_bad(id integer PRIMARY KEY,parent integer REFERENCES jt_bad.parent(id),ord money);
CREATE TABLE jt_bad.ident_field(id integer PRIMARY KEY,g bigint GENERATED ALWAYS AS IDENTITY);
CREATE TABLE jt_bad.arr_ident(id integer PRIMARY KEY,parent integer REFERENCES jt_bad.parent(id),g bigint GENERATED ALWAYS AS IDENTITY);
CREATE TABLE jt_bad.root_fk_ident(id integer PRIMARY KEY,fk bigint GENERATED ALWAYS AS IDENTITY REFERENCES jt_bad.ident(id));
CREATE TABLE jt_bad.inherited_root(id integer PRIMARY KEY,value text);
CREATE TABLE jt_bad.inherited_root_child() INHERITS(jt_bad.inherited_root);
ALTER TABLE jt_bad.inherited_root_child ADD PRIMARY KEY(id);
CREATE TABLE jt_bad.inherited_array(id integer PRIMARY KEY,parent integer REFERENCES jt_bad.parent(id),value text);
CREATE TABLE jt_bad.inherited_array_child() INHERITS(jt_bad.inherited_array);
ALTER TABLE jt_bad.inherited_array_child ADD PRIMARY KEY(id);
CREATE TABLE jt_bad.inherited_object(id integer PRIMARY KEY,value text);
CREATE TABLE jt_bad.inherited_object_child() INHERITS(jt_bad.inherited_object);
ALTER TABLE jt_bad.inherited_object_child ADD PRIMARY KEY(id);
CREATE TABLE jt_bad.object_owner(id integer PRIMARY KEY,object_id integer REFERENCES jt_bad.inherited_object(id));
CREATE TABLE jt_bad.partitioned_object(id integer PRIMARY KEY,value text) PARTITION BY RANGE(id);
CREATE TABLE jt_bad.partitioned_object_low PARTITION OF jt_bad.partitioned_object FOR VALUES FROM (0) TO (100);
CREATE TABLE jt_bad.partitioned_root(id integer PRIMARY KEY,object_id integer REFERENCES jt_bad.partitioned_object(id),value text) PARTITION BY RANGE(id);
CREATE TABLE jt_bad.partitioned_root_low PARTITION OF jt_bad.partitioned_root FOR VALUES FROM (0) TO (100);
CREATE TABLE jt_bad.partitioned_array(id integer PRIMARY KEY,parent integer NOT NULL REFERENCES jt_bad.partitioned_root(id),value text) PARTITION BY RANGE(id);
CREATE TABLE jt_bad.partitioned_array_low PARTITION OF jt_bad.partitioned_array FOR VALUES FROM (0) TO (100);
CREATE VIEW jt_bad.v AS SELECT 1 id;""")
        base=json.loads(self.spec.read_text()); cases=[]
        cases += [("null_spec","shop.bad",None),("three_part","a.b.c",base),("unknown_top","shop.bad",{"x":1}),("missing_root","shop.bad",{}),("root_wrong_type","shop.bad",{"root":[]})]
        def variant(name,edit):
            x=json.loads(json.dumps(base));edit(x);cases.append((name,"shop.bad_"+str(len(cases)),x))
        variant("table_parts",lambda x:x["root"].update(table="orders"));variant("table_missing",lambda x:x["root"].update(table="shop.nope"));variant("not_table",lambda x:x["root"].update(table="jt_bad.v"))
        cases.append(("classic_inherited_root","shop.bad_inherited_root",{"root":{"table":"jt_bad.inherited_root","key":"id","fields":{"value":"value"}}}))
        cases.append(("classic_inherited_array","shop.bad_inherited_array",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"array":{"name":"items","table":"jt_bad.inherited_array","key":"id","parent_fk":"parent","fields":{"id":"id","value":"value"}}}))
        cases.append(("classic_inherited_object","shop.bad_inherited_object",{"root":{"table":"jt_bad.object_owner","key":"id","fields":{}},"object":{"name":"object","table":"jt_bad.inherited_object","key":"id","root_fk":"object_id","fields":{"id":"id","value":"value"}}}))
        variant("column_missing",lambda x:x["root"]["fields"].update(status="nope"));variant("unsupported",lambda x:x["root"].update(table="jt_bad.gen",key="id",fields={"u":"u"}))
        variant("generated",lambda x:x["root"].update(table="jt_bad.gen",key="id",fields={"g":"g"}));variant("key_not_pk",lambda x:x["root"].update(key="status"))
        variant("root_key_mapped",lambda x:x["root"]["fields"].update(bad="order_id"));variant("array_parent_mapped",lambda x:x["array"]["fields"].update(bad="order_id"))
        variant("object_key_unmapped",lambda x:x["object"].update(fields={"name":"name"}));variant("array_key_unmapped",lambda x:x["array"].update(fields={"value":"product"}))
        variant("duplicate_column",lambda x:x["root"]["fields"].update(again="status"));variant("invalid_field",lambda x:x["root"]["fields"].update(**{"_bad":"status"}))
        variant("duplicate_doc_name",lambda x:x["array"].update(name="status"));variant("long_generated_name",lambda x:None)
        cases[-1]=(cases[-1][0],"shop."+"x"*56,cases[-1][2])
        ident={"root":{"table":"jt_bad.ident","key":"id","fields":{},"update":True}};cases.append(("identity_no_rewrite","shop.bad_ident",ident))
        cases.append(("object_fk_missing","shop.bad_fk_missing",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"object":{"name":"obj","table":"jt_bad.ref","key":"id","root_fk":"fk","fields":{"id":"id"}}}))
        cases.append(("object_fk_deferrable","shop.bad_fk_def",{"root":{"table":"jt_bad.parent_def","key":"id","fields":{}},"object":{"name":"obj","table":"jt_bad.ref","key":"id","root_fk":"fk","fields":{"id":"id"}}}))
        cases.append(("array_fk_deferrable","shop.bad_arr_def",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"array":{"name":"items","table":"jt_bad.child","key":"id","parent_fk":"parent","fields":{"id":"id"}}}))
        cases.append(("array_fk_cascade","shop.bad_arr_cascade",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"array":{"name":"items","table":"jt_bad.cascade","key":"id","parent_fk":"parent","fields":{"id":"id"}}}))
        if supports_not_enforced:
            cases.append(("array_fk_not_enforced","shop.bad_arr_ne",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"array":{"name":"items","table":"jt_bad.not_enforced","key":"id","parent_fk":"parent","fields":{"id":"id"}}}))
        cases.append(("array_order_unsupported","shop.bad_arr_order",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"array":{"name":"items","table":"jt_bad.order_bad","key":"id","parent_fk":"parent","order_by":"ord","fields":{"id":"id"}}}))
        cases.append(("root_field_identity","shop.bad_root_ident",{"root":{"table":"jt_bad.ident_field","key":"id","fields":{"g":"g"}}}))
        cases.append(("array_field_identity","shop.bad_arr_ident",{"root":{"table":"jt_bad.parent","key":"id","fields":{}},"array":{"name":"items","table":"jt_bad.arr_ident","key":"id","parent_fk":"parent","fields":{"id":"id","g":"g"}}}))
        cases.append(("root_fk_identity","shop.bad_fk_ident",{"root":{"table":"jt_bad.root_fk_ident","key":"id","fields":{}},"object":{"name":"obj","table":"jt_bad.ident","key":"id","root_fk":"fk","fields":{"id":"id"}}}))
        bad=[]
        for name, view, spec in cases:
            expr="NULL::jsonb" if spec is None else lit(json.dumps(spec))+"::jsonb"
            p=self.run("SELECT jsontailor.duality_view_ddl("+lit(view)+","+expr+");",False)
            bad.append(f"{name}:{self.state(p)}")
        passed=all(x.endswith(":22023") for x in bad)
        partitioned={"root":{"table":"jt_bad.partitioned_root","key":"id","fields":{"value":"value"},"insert":True,"update":True},"object":{"name":"object","table":"jt_bad.partitioned_object","key":"id","root_fk":"object_id","fields":{"id":"id","value":"value"}},"array":{"name":"items","table":"jt_bad.partitioned_array","key":"id","parent_fk":"parent","fields":{"id":"id","value":"value"},"insert":True,"update":True,"delete":True}}
        partitioned_result=self.run("INSERT INTO jt_bad.partitioned_object VALUES(7,'reference');SELECT jsontailor.create_duality_view('shop.good_partitioned',"+lit(json.dumps(partitioned))+"::jsonb);INSERT INTO shop.good_partitioned(data) VALUES('{\"_id\":1,\"value\":\"root\",\"object\":{\"id\":7},\"items\":[{\"id\":11,\"value\":\"first\"}]}'::jsonb);UPDATE shop.good_partitioned SET data=jsonb_set(data,'{items,0,value}','\"changed\"'::jsonb) WHERE _id=1;",False)
        partitioned_state=self.scalar("SELECT count(*)::text||':'||max(data#>>'{object,value}')||':'||max(data#>>'{items,0,value}') FROM shop.good_partitioned WHERE _id=1;") if partitioned_result.returncode==0 else "not-created"
        passed &= partitioned_result.returncode==0 and partitioned_state=="1:reference:changed"
        bad.append(f"declarative_partition:{partitioned_state if partitioned_result.returncode==0 else self.state(partitioned_result)}")
        leaf_result=self.run("SELECT jsontailor.duality_view_ddl('shop.good_leaf','{\"root\":{\"table\":\"jt_bad.parent\",\"key\":\"id\",\"fields\":{}}}'::jsonb);",False)
        passed &= leaf_result.returncode==0
        bad.append(f"ordinary_leaf:{'accepted' if leaf_result.returncode==0 else self.state(leaf_result)}")
        self.add(12,"validation","PASS" if passed else "FAIL","invalid-specification catalogue returns 22023",";".join(bad),
                 "Cases directly cover structural, catalogue, mapping, FK enforcement/action, identity, inheritance and generated-name rules; declarative partitioning remains supported.")

    def test14(self):
        self.run("""DROP SCHEMA IF EXISTS jt_v7 CASCADE; CREATE SCHEMA jt_v7;
CREATE TABLE jt_v7.root(id integer PRIMARY KEY,t text,v varchar(9),c char(2),s smallint,i integer,b bigint,n numeric(12,2),flag boolean,d date,ts timestamp,tz timestamptz,u uuid);
CREATE TABLE jt_v7.child(id integer PRIMARY KEY,parent integer NOT NULL REFERENCES jt_v7.root(id),t text,v varchar(9),c char(2),s smallint,i integer,b bigint,n numeric(12,2),flag boolean,d date,ts timestamp,tz timestamptz,u uuid);
INSERT INTO jt_v7.root VALUES(1,'é','v','c',2,3,4,5.20,true,'2026-11-03','2026-11-03 10:00','2026-11-03 10:00+01','00000000-0000-0000-0000-000000000001');
INSERT INTO jt_v7.child VALUES(1,1,'x','y','z',2,3,4,5.20,false,'2026-11-04','2026-11-04 10:00','2026-11-04 10:00+01','00000000-0000-0000-0000-000000000002');
CREATE TABLE jt_v7.excluded(id integer PRIMARY KEY,r real,d double precision,iv interval,m money,ba bytea);""")
        fields={"t":"t","v":"v","c":"c","s":"s","i":"i","b":"b","n":"n","flag":"flag","d":"d","ts":"ts","tz":"tz","u":"u"}
        spec={"root":{"table":"jt_v7.root","key":"id","fields":fields,"insert":True,"update":True,"delete":True},"array":{"name":"items","table":"jt_v7.child","key":"id","parent_fk":"parent","fields":{"id":"id",**fields},"insert":True,"update":True,"delete":True}}
        self.run("SELECT jsontailor.create_duality_view('jt_v7.dv',"+lit(json.dumps(spec))+"::jsonb);")
        tags=[]
        for settings in ("SET TimeZone='UTC';SET DateStyle='ISO, MDY';SET extra_float_digits=0;","SET TimeZone='Pacific/Auckland';SET DateStyle='German, DMY';SET extra_float_digits=3;"):
            tags.append(self.scalar(settings+"SELECT data#>>'{_metadata,etag}' FROM jt_v7.dv WHERE _id=1;"))
        excluded=[]
        for col in ("r","d","iv","m","ba"):
            bad={"root":{"table":"jt_v7.excluded","key":"id","fields":{"bad":col}}}
            excluded.append(self.state(self.run("SELECT jsontailor.duality_view_ddl('jt_v7.bad_"+col+"',"+lit(json.dumps(bad))+"::jsonb);",False)))
        passed=len(set(tags))==1 and all(x=="22023" for x in excluded)
        self.add(14,"V7","PASS" if passed else "FAIL","stable ETag for supported types and 22023 exclusions",f"tags={tags};excluded={excluded}")

    def test15(self):
        self.reset(); before=self.scalar("SELECT xmin::text||':'||(SELECT string_agg(xmin::text,',' ORDER BY item_id) FROM shop.order_items WHERE order_id=42) FROM shop.orders WHERE order_id=42;")
        self.run("UPDATE shop.orders_dv SET data=data WHERE _id=42;")
        after=self.scalar("SELECT xmin::text||':'||(SELECT string_agg(xmin::text,',' ORDER BY item_id) FROM shop.order_items WHERE order_id=42) FROM shop.orders WHERE order_id=42;")
        self.add(15,"V1","PASS" if before==after else "FAIL","unchanged xmin and ETag",f"before={before};after={after}")

    def test16(self):
        coll='"und-x-icu"' if self.scalar("SELECT EXISTS(SELECT 1 FROM pg_collation WHERE collname='und-x-icu');")=='t' else '"C"'
        self.run(f"""DROP SCHEMA IF EXISTS jt_v5 CASCADE;CREATE SCHEMA jt_v5;
CREATE TABLE jt_v5.root(id integer PRIMARY KEY);CREATE TABLE jt_v5.child(id text COLLATE {coll} PRIMARY KEY,parent integer NOT NULL REFERENCES jt_v5.root(id),ord text COLLATE {coll},value text);
INSERT INTO jt_v5.root VALUES(1);INSERT INTO jt_v5.child VALUES('a',1,'same','a'),('B',1,'same','B'),('á',1,'same','accent');""")
        spec={"root":{"table":"jt_v5.root","key":"id","fields":{}},"array":{"name":"items","table":"jt_v5.child","key":"id","parent_fk":"parent","order_by":"ord","fields":{"id":"id","ord":"ord","value":"value"}}}
        self.run("SELECT jsontailor.create_duality_view('jt_v5.dv',"+lit(json.dumps(spec))+"::jsonb);")
        observed=self.scalar("SELECT string_agg(x.e->>'id',',' ORDER BY x.ordinality) FROM jt_v5.dv CROSS JOIN LATERAL jsonb_array_elements(data->'items') WITH ORDINALITY AS x(e,ordinality) WHERE _id=1;")
        expected=self.scalar('SELECT string_agg(id,\',\' ORDER BY ord COLLATE "C",id COLLATE "C") FROM jt_v5.child;')
        self.add(16,"V5","PASS" if observed==expected else "FAIL","C collation ordering for order_by and text key",f"observed={observed};expected={expected};source_collation={coll}")

    def test17(self):
        root="root*/$fn$; ' \" space"; child="child*/$$; ' \" space"; key="id$fn$; ' \""; parent="parent$$; ' \""; value="value$fn$; ' \""
        qi=lambda x:'"'+x.replace('"','""')+'"'
        self.run(f"DROP SCHEMA IF EXISTS jt_host CASCADE;CREATE SCHEMA jt_host;CREATE TABLE jt_host.{qi(root)}({qi(key)} integer PRIMARY KEY,{qi(value)} text);CREATE TABLE jt_host.{qi(child)}({qi(key)} integer PRIMARY KEY,{qi(parent)} integer REFERENCES jt_host.{qi(root)}({qi(key)}),{qi(value)} text);INSERT INTO jt_host.{qi(root)} VALUES(1,'r');INSERT INTO jt_host.{qi(child)} VALUES(1,1,'c');CREATE SCHEMA IF NOT EXISTS rusive_schema;")
        spec={"root":{"table":"jt_host."+qi(root),"key":key,"fields":{"value":value},"insert":True,"update":True},"array":{"name":"items","table":"jt_host."+qi(child),"key":key,"parent_fk":parent,"fields":{"id":key,"value":value},"insert":True,"update":True,"delete":True}}
        p=self.run("SET search_path=rusive_schema,pg_catalog;SELECT jsontailor.create_duality_view('jt_host.\"odd$fn$; name\"',"+lit(json.dumps(spec))+"::jsonb);",False)
        ddl=self.run("SELECT jsontailor.duality_view_ddl('jt_host.\"odd$$; second\"',"+lit(json.dumps(spec))+"::jsonb);").stdout.strip()
        p2=self.run("SET search_path=rusive_schema,pg_catalog;"+ddl,False)
        update=self.run("UPDATE jt_host.\"odd$fn$; name\" SET data=jsonb_set(data,'{value}','\"changed\"') WHERE _id=1;",False) if p.returncode==0 else p
        inserted={"_id":2,"value":"new","items":[{"id":2,"value":"child"}]}
        insert=self.run("INSERT INTO jt_host.\"odd$fn$; name\"(data) VALUES("+lit(json.dumps(inserted))+"::jsonb);",False) if p.returncode==0 else p
        values=self.scalar("SELECT (SELECT data#>>'{value}' FROM jt_host.\"odd$fn$; name\" WHERE _id=1)||':'||(SELECT data#>>'{items,0,value}' FROM jt_host.\"odd$fn$; name\" WHERE _id=2)||':'||(SELECT data#>>'{items,0,value}' FROM jt_host.\"odd$$; second\" WHERE _id=1);") if p.returncode==0 and p2.returncode==0 and update.returncode==0 and insert.returncode==0 else "not-created"
        passed=p.returncode==0 and p2.returncode==0 and update.returncode==0 and insert.returncode==0 and values=="changed:child:c"
        self.add(17,"hostile identifiers","PASS" if passed else "FAIL","hostile search_path and hostile table/column names including comment terminators",f"create={p.returncode};ddl={p2.returncode};update={update.returncode};insert={insert.returncode};values={values}")

    def test18(self):
        values=self.scalar("""SELECT count(*)::text||':'||string_agg(proname||'/'||provolatile::text||'/'||prosecdef::text||'/'||array_to_string(proconfig,','),';' ORDER BY proname)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='jsontailor';""")
        usage=self.scalar("SELECT has_schema_privilege('public','jsontailor','USAGE');")
        passed=values.startswith("7:") and values.count("/s/false/search_path=pg_catalog, pg_temp")==1 and values.count("/v/false/search_path=pg_catalog, pg_temp")==6 and usage=='t'
        self.add(18,"installation","PASS" if passed else "FAIL","seven functions with contract attributes and PUBLIC usage",f"catalog={values};usage={usage}")

    def test19(self):
        self.reset()
        p=self.run("""INSERT INTO shop.orders_dv(data) VALUES ('{"customer":{"customerId":7},"items":[{"product":"new","qty":1,"price":2.50}]}'::jsonb) RETURNING _id,data;""",False)
        explicit=self.run("""INSERT INTO shop.orders_dv(data) VALUES ('{"_id":200,"status":"explicit","created":"2026-11-03","customer":{"customerId":7},"items":[]}'::jsonb);""",False)
        nullid=self.run("INSERT INTO shop.orders_dv(data) VALUES ('{\"_id\":null,\"items\":[]}'::jsonb);",False)
        passed=p.returncode==0 and explicit.returncode==0 and self.state(nullid)=='PT422'
        self.add(19,"V1","PASS" if passed else "FAIL","defaults, explicit id, object key, child defaults and null rejection",
                 f"default_rc={p.returncode};explicit_rc={explicit.returncode};null={self.state(nullid)}")

    def test20(self):
        self.run("""DROP SCHEMA IF EXISTS jt_id CASCADE;CREATE SCHEMA jt_id;
CREATE TABLE jt_id.ra(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,status text);CREATE TABLE jt_id.ca(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,parent bigint NOT NULL REFERENCES jt_id.ra(id),value text);INSERT INTO jt_id.ra(status) VALUES('a');INSERT INTO jt_id.ca(parent,value) VALUES(1,'x');
CREATE TABLE jt_id.rb(id bigint PRIMARY KEY);CREATE TABLE jt_id.cb(id bigint PRIMARY KEY,parent bigint NOT NULL REFERENCES jt_id.rb(id),value text);INSERT INTO jt_id.rb VALUES(1);INSERT INTO jt_id.cb VALUES(1,1,'x');""")
        def mk(root,child,fields): return {"root":{"table":"jt_id."+root,"key":"id","fields":fields,"insert":True,"update":True,"delete":True},"array":{"name":"items","table":"jt_id."+child,"key":"id","parent_fk":"parent","fields":{"id":"id","value":"value"},"insert":True,"update":True,"delete":True}}
        a=mk('ra','ca',{'status':'status'}); b=mk('rb','cb',{})
        self.run("SELECT jsontailor.create_duality_view('jt_id.dva',"+lit(json.dumps(a))+"::jsonb);SELECT jsontailor.create_duality_view('jt_id.dvb',"+lit(json.dumps(b))+"::jsonb);")
        before_a=self.scalar("SELECT xmin::text FROM jt_id.ra WHERE id=1;"); before_b=self.scalar("SELECT xmin::text FROM jt_id.rb WHERE id=1;")
        pa=self.run("UPDATE jt_id.dva SET data=jsonb_set(data,'{items,0,value}','\"y\"') WHERE _id=1;",False)
        pb=self.run("UPDATE jt_id.dvb SET data=jsonb_set(data,'{items,0,value}','\"y\"') WHERE _id=1;",False)
        after_a=self.scalar("SELECT xmin::text FROM jt_id.ra WHERE id=1;"); after_b=self.scalar("SELECT xmin::text FROM jt_id.rb WHERE id=1;")
        no_key=self.run("INSERT INTO jt_id.dva(data) VALUES ('{\"status\":\"n\",\"items\":[{\"value\":\"z\"}]}'::jsonb);",False)
        explicit=self.run("INSERT INTO jt_id.dva(data) VALUES ('{\"status\":\"n\",\"items\":[{\"id\":99,\"value\":\"z\"}]}'::jsonb);",False)
        self.run("CREATE TABLE jt_id.ref(id bigint PRIMARY KEY);CREATE TABLE jt_id.rc(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,ref_id bigint REFERENCES jt_id.ref(id));")
        defaults={"root":{"table":"jt_id.rc","key":"id","fields":{},"insert":True},"object":{"name":"ref","table":"jt_id.ref","key":"id","root_fk":"ref_id","fields":{"id":"id"}}}
        self.run("SELECT jsontailor.create_duality_view('jt_id.dvc',"+lit(json.dumps(defaults))+"::jsonb);");default_root=self.run("INSERT INTO jt_id.dvc(data) VALUES ('{}');",False)
        passed=pa.returncode==0 and pb.returncode==0 and before_a!=after_a and before_b!=after_b and no_key.returncode==0 and self.state(explicit)=="PT422" and default_root.returncode==0
        self.add(20,"identity variants","PASS" if passed else "FAIL","root and child identity rewrite semantics",f"a={pa.returncode}/{before_a}->{after_a};b={pb.returncode}/{before_b}->{after_b};child_default={no_key.returncode};child_explicit={self.state(explicit)};root_defaults={default_root.returncode}")


def main():
    p=argparse.ArgumentParser(); p.add_argument('--psql',required=True);p.add_argument('--database',required=True)
    p.add_argument('--schema',type=Path,required=True);p.add_argument('--spec',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); r=Runner(a.psql,a.database,a.schema,a.spec)
    for n in (6,7,8,9,10,12,14,15,16,17,18,19,20): getattr(r,f'test{n}')(); print(r.rows[-1])
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in r.rows))
    return 0

if __name__=='__main__': raise SystemExit(main())
