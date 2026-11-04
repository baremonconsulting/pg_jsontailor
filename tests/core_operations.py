#!/usr/bin/env python3
"""Exercise the core document operations against the reference specification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Harness:
    def __init__(self, psql: str, database: str, schema: Path, spec: Path):
        self.psql = psql
        self.database = database
        self.schema = schema
        self.spec = spec
        self.results: list[dict] = []

    def command(self) -> list[str]:
        return [self.psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", self.database]

    def run(self, sql: str, check: bool = True) -> subprocess.CompletedProcess:
        completed = subprocess.run(self.command(), input="\\set VERBOSITY verbose\n" + sql,
                                   text=True, capture_output=True)
        if check and completed.returncode:
            raise RuntimeError(completed.stderr)
        return completed

    def reset(self) -> None:
        completed = subprocess.run(self.command() + ["-f", str(self.schema)],
                                   text=True, capture_output=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        spec_text = self.spec.read_text()
        self.run("SELECT jsontailor.create_duality_view('shop.orders_dv', "
                 f"{literal(spec_text)}::jsonb);\n")

    def scalar(self, sql: str) -> str:
        return self.run(sql).stdout.strip().splitlines()[-1]

    @staticmethod
    def state(completed: subprocess.CompletedProcess) -> str | None:
        match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", completed.stderr)
        return match.group(1) if match else None

    def record(self, test_id: str, expected: str, observed: str,
               passed: bool, notes: str = "") -> None:
        self.results.append({"id": test_id, "variant": "V1", "status": "PASS" if passed else "FAIL",
                             "expected": expected, "observed": observed,
                             "evidence": "tests/core_operations.py", "notes": notes})

    def document(self) -> str:
        return self.scalar("SELECT data::text FROM shop.orders_dv WHERE _id=42;")

    def test1(self) -> None:
        self.reset()
        etag = self.scalar("SELECT data#>>'{_metadata,etag}' FROM shop.orders_dv WHERE _id=42;")
        returned = self.scalar("""
UPDATE shop.orders_dv
SET data = pg_catalog.jsonb_set(
             pg_catalog.jsonb_set(data, '{status}', '"processing"'::jsonb),
             '{items}',
             '[{"itemId":1,"product":"Kabel","qty":3,"price":99.00},
               {"product":"Adapter","qty":1,"price":12.34}]'::jsonb)
WHERE _id=42
RETURNING data#>>'{_metadata,etag}';
""")
        tables_ok = self.scalar("""
SELECT (SELECT status='processing' AND customer_id=7 FROM shop.orders WHERE order_id=42)
   AND (SELECT count(*)=2 AND bool_and(
          (item_id=1 AND quantity=3 AND product='Kabel') OR
          (item_id<>1 AND quantity=1 AND product='Adapter'))
        FROM shop.order_items WHERE order_id=42);
""")
        passed = etag == "70AF45CCE541C9A5C8B598F2DA5C2CBC" and tables_ok == "t" and returned != etag
        self.record("1", "control ETag and successful round trip", f"etag={etag};new={returned};tables={tables_ok}", passed)

    def test2(self) -> None:
        self.reset()
        old = self.document()
        self.run("UPDATE shop.orders SET status='direct' WHERE order_id=42;")
        attempted = json.loads(old)
        attempted["status"] = "client"
        result = self.run("UPDATE shop.orders_dv SET data=" + literal(json.dumps(attempted)) + "::jsonb WHERE _id=42;", False)
        unchanged = self.scalar("SELECT status FROM shop.orders WHERE order_id=42;")
        state = self.state(result)
        self.record("2", "PT412 and direct state preserved", f"sqlstate={state};status={unchanged}", state == "PT412" and unchanged == "direct")

    def test3(self) -> None:
        cases = [
            ("missing_metadata", "data - '_metadata'", "PT428"),
            ("missing_etag", "data #- '{_metadata,etag}'", "PT428"),
            ("etag_number", "jsonb_set(data,'{_metadata,etag}','1'::jsonb)", "PT428"),
            ("metadata_scalar", "jsonb_set(data,'{_metadata}','1'::jsonb)", "PT422"),
        ]
        observed = []
        passed = True
        for name, expression, expected in cases:
            self.reset()
            result = self.run(f"UPDATE shop.orders_dv SET data={expression} WHERE _id=42;", False)
            state = self.state(result)
            observed.append(f"{name}:{state}")
            passed &= state == expected
        self.reset()
        extra = self.run("""
UPDATE shop.orders_dv
SET data=jsonb_set(data,'{_metadata,asof}','"ignored"'::jsonb)
WHERE _id=42;
""", False)
        observed.append(f"extra_asof:rc{extra.returncode}")
        passed &= extra.returncode == 0
        self.record("3", "PT428/PT422 and ignored asof", ";".join(observed), passed)

    def concurrent(self, isolation: str, add_only: bool) -> tuple[str | None, float]:
        self.reset()
        base = json.loads(self.document())
        first = json.loads(json.dumps(base))
        second = json.loads(json.dumps(base))
        if add_only:
            first["items"].append({"product": "A", "qty": 1, "price": 1})
        else:
            first["status"] = "first"
        second["status"] = "second"
        a_sql = ("BEGIN; UPDATE shop.orders_dv SET data=" + literal(json.dumps(first)) +
                 "::jsonb WHERE _id=42; SELECT pg_sleep(1.0); COMMIT;")
        b_sql = (f"BEGIN ISOLATION LEVEL {isolation}; UPDATE shop.orders_dv SET data=" +
                 literal(json.dumps(second)) + "::jsonb WHERE _id=42; COMMIT;")
        a = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        assert a.stdin is not None
        a.stdin.write("\\set VERBOSITY verbose\n" + a_sql)
        a.stdin.close()
        time.sleep(0.2)
        started = time.monotonic()
        b = self.run(b_sql, False)
        waited = time.monotonic() - started
        a.wait(timeout=10)
        return self.state(b), waited

    def test4(self) -> None:
        observations = []
        passed = True
        for isolation, expected in (("READ COMMITTED", "PT412"),
                                    ("REPEATABLE READ", "40001"),
                                    ("SERIALIZABLE", "40001")):
            for add_only in (False, True):
                state, waited = self.concurrent(isolation, add_only)
                observations.append(f"{isolation}/{'add' if add_only else 'status'}:{state}/{waited:.3f}s")
                passed &= state == expected and waited >= 0.6
        self.record("4", "PT412 in RC; 40001 in RR/SERIALIZABLE", ";".join(observations), passed)

    def test5(self) -> None:
        observations = []
        passed = True
        # a) Direct committed change after read.
        self.reset()
        doc = json.loads(self.document())
        self.run("UPDATE shop.order_items SET quantity=11 WHERE item_id=1;")
        doc["status"] = "client"
        result = self.run("UPDATE shop.orders_dv SET data=" + literal(json.dumps(doc)) + "::jsonb WHERE _id=42;", False)
        state = self.state(result); observations.append(f"a:{state}"); passed &= state == "PT412"

        # b) Uncommitted child change commits while the view writer waits.
        self.reset(); doc = json.loads(self.document()); doc["status"] = "client"
        direct = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        assert direct.stdin is not None
        direct.stdin.write("BEGIN; UPDATE shop.order_items SET quantity=11 WHERE item_id=1; SELECT pg_sleep(1); COMMIT;")
        direct.stdin.close(); time.sleep(0.2)
        started=time.monotonic(); result=self.run("UPDATE shop.orders_dv SET data="+literal(json.dumps(doc))+"::jsonb WHERE _id=42;",False)
        waited=time.monotonic()-started; direct.wait(timeout=10)
        state=self.state(result); observations.append(f"b:{state}/{waited:.3f}s"); passed &= state=="PT412" and waited>=0.6

        # c) Direct insert waits while the view writer owns the root lock.
        self.reset(); doc=json.loads(self.document()); doc["status"]="view"
        writer=subprocess.Popen(self.command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert writer.stdin is not None
        writer.stdin.write("BEGIN; UPDATE shop.orders_dv SET data="+literal(json.dumps(doc))+"::jsonb WHERE _id=42; SELECT pg_sleep(1); COMMIT;")
        writer.stdin.close(); time.sleep(0.2)
        started=time.monotonic(); inserted=self.run("INSERT INTO shop.order_items(order_id,product,quantity,unit_price) VALUES(42,'direct',1,1);",False)
        waited=time.monotonic()-started; writer.wait(timeout=10)
        observations.append(f"c:rc{inserted.returncode}/{waited:.3f}s"); passed &= inserted.returncode==0 and waited>=0.6

        # d) Document deletion while the writer waits for the root lock.
        self.reset(); doc=json.loads(self.document()); doc["status"]="client"
        deleter=subprocess.Popen(self.command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert deleter.stdin is not None
        deleter.stdin.write("BEGIN; DELETE FROM shop.order_items WHERE order_id=42; DELETE FROM shop.orders WHERE order_id=42; SELECT pg_sleep(1); COMMIT;")
        deleter.stdin.close(); time.sleep(0.2)
        result=self.run("UPDATE shop.orders_dv SET data="+literal(json.dumps(doc))+"::jsonb WHERE _id=42;",False)
        deleter.wait(timeout=10); state=self.state(result); observations.append(f"d:{state}"); passed &= state=="PT412"
        self.record("5", "direct-write race semantics", ";".join(observations), passed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    harness = Harness(args.psql, args.database, args.schema, args.spec)
    for test in (harness.test1, harness.test2, harness.test3, harness.test4, harness.test5):
        test()
        print(harness.results[-1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in harness.results))
    return 0 if all(row["status"] == "PASS" for row in harness.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
