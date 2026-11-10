#!/usr/bin/env python3
"""Run the PGJT-48 PostgREST contract and save an HTTP transcript."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


def request(base: str, method: str, path: str, body=None, headers=None):
    payload = None if body is None else json.dumps(body).encode()
    values = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + path, data=payload, headers=values, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode()
            return response.status, dict(response.headers), raw
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode()


def save_transcript(path: Path, base: str, calls: list[dict]) -> None:
    lines = ["# PostgREST transcript", "", f"PostgREST endpoint: `{base}`", ""]
    for item in calls:
        lines += [f"## {item['method']} `{item['path']}`", "",
                  f"Status: **{item['status']}**", "",
                  "Request headers: `" + json.dumps(item['request_headers'], sort_keys=True) + "`", "",
                  "Response headers: `" + json.dumps(item['response_headers'], sort_keys=True) + "`", "",
                  "Request body:", "```json", json.dumps(item['request'], ensure_ascii=False), "```", "",
                  "Response:", "```json", item['response'], "```", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:3050")
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    calls = []

    def call(method, path, body=None, headers=None):
        item = {"method": method, "path": path, "request": body,
                "request_headers": headers or {}, "status": "ERROR",
                "response_headers": {}, "response": "request did not complete"}
        calls.append(item)
        try:
            status, response_headers, raw = request(args.base, method, path, body, headers)
            item.update(status=status, response_headers=response_headers, response=raw)
            return status, response_headers, raw
        except Exception as error:
            item["response"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            save_transcript(args.transcript, args.base, calls)

    status, _, raw = call("GET", "/orders_dv?_id=eq.42")
    document = json.loads(raw)[0]["data"]
    initial_document = json.loads(json.dumps(document))
    stale = json.loads(json.dumps(document))
    document["status"] = "rest-updated"
    patch_status, _, raw = call("PATCH", "/orders_dv?_id=eq.42",
                                {"data": document}, {"Prefer": "return=representation"})
    updated = json.loads(raw)[0]["data"]

    stale["status"] = "stale"
    stale_status, _, _ = call("PATCH", "/orders_dv?_id=eq.42", {"data": stale})
    no_etag = json.loads(json.dumps(updated)); no_etag.pop("_metadata")
    missing_status, _, _ = call("PATCH", "/orders_dv?_id=eq.42", {"data": no_etag})
    incomplete = json.loads(json.dumps(updated)); incomplete.pop("created")
    incomplete_status, _, _ = call("PATCH", "/orders_dv?_id=eq.42", {"data": incomplete})
    _, _, raw = call("GET", "/orders_dv?_id=eq.42")
    after_rejections = json.loads(raw)[0]["data"]

    new_document = {"status": "rest-new", "customer": {"customerId": 7},
                    "items": [{"product": "REST item", "qty": 1, "price": 2.50}]}
    post_status, _, raw = call("POST", "/orders_dv", {"data": new_document},
                               {"Prefer": "return=representation"})
    inserted = json.loads(raw)[0]
    new_id = inserted["_id"]
    _, _, raw = call("GET", f"/orders_dv?_id=eq.{new_id}")
    insert_readback = json.loads(raw)
    delete_status, _, raw = call("POST", "/rpc/orders_dv__delete_document", {
        "expected_id": new_id,
        "expected_etag": inserted["data"]["_metadata"]["etag"],
    })
    deleted_document = json.loads(raw)
    _, _, raw = call("GET", f"/orders_dv?_id=eq.{new_id}")
    delete_readback = json.loads(raw)
    _, _, raw = call("GET", "/orders_dv?_id=eq.42")
    seed_after_delete = json.loads(raw)
    options_status, options_headers, _ = call("OPTIONS", "/orders_dv")
    table_get, _, _ = call("GET", "/orders")
    table_patch, _, _ = call("PATCH", "/order_items?id=eq.1", {"quantity": 99})

    allow = options_headers.get("Allow", "")
    checks = [
        ("PGJT-48/http-get", status == 200 and initial_document["status"] == "new"
         and initial_document["customer"]["customerId"] == 7
         and len(initial_document["items"]) == 2,
         "exact seeded document", initial_document),
        ("PGJT-48/http-complete-patch", patch_status == 200
         and updated["status"] == "rest-updated"
         and {k: v for k, v in updated.items() if k != "_metadata"}
         == {k: v for k, v in document.items() if k != "_metadata"}
         and updated["_metadata"]["etag"] != stale["_metadata"]["etag"],
         "exact replacement and changed ETag", updated),
        ("PGJT-48/http-stale-etag", stale_status == 412
         and json.loads(calls[2]["response"])["code"] == "PT412"
         and after_rejections == updated, "412/PT412 and unchanged", stale_status),
        ("PGJT-48/http-missing-etag", missing_status == 428
         and json.loads(calls[3]["response"])["code"] == "PT428"
         and after_rejections == updated, "428/PT428 and unchanged", missing_status),
        ("PGJT-48/http-incomplete-document", incomplete_status == 422
         and json.loads(calls[4]["response"])["code"] == "PT422"
         and after_rejections == updated, "422/PT422 and unchanged", incomplete_status),
        ("PGJT-48/http-insert", post_status == 201 and new_id != 42
         and insert_readback == [inserted]
         and inserted["data"]["status"] == "rest-new"
         and inserted["data"]["customer"] == {"customerId": 7, "name": "ACME"}
         and len(inserted["data"]["items"]) == 1
         and inserted["data"]["items"][0]["product"] == "REST item"
         and inserted["data"]["items"][0]["qty"] == 1
         and inserted["data"]["items"][0]["price"] == 2.5,
         "201 and exact read-back", insert_readback),
        ("PGJT-48/http-delete-rpc", delete_status == 200
         and deleted_document == inserted["data"] and delete_readback == []
         and len(seed_after_delete) == 1 and seed_after_delete[0]["_id"] == 42,
         "200 exact deleted document and empty read-back", delete_readback),
        ("PGJT-48/http-options", options_status == 200
         and all(method in allow for method in ("POST", "PATCH"))
         and "DELETE" not in allow,
         "200 with POST/PATCH but no view DELETE", {"status": options_status, "allow": allow}),
        ("PGJT-48/http-hidden-base-tables", table_get == 404 and table_patch == 404,
         "GET=404,PATCH=404", {"get": table_get, "patch": table_patch}),
    ]
    passed = all(item[1] for item in checks)
    save_transcript(args.transcript, args.base, calls)
    results = [{"id": test_id, "variant": "postgrest-16.5",
                "status": "PASS" if ok else "FAIL", "expected": expected,
                "observed": observed, "evidence": str(args.transcript),
                "notes": "PGJT-48 HTTP contract"}
               for test_id, ok, expected, observed in checks]
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text("".join(json.dumps(item, sort_keys=True) + "\n"
                                   for item in results))
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        result_path = Path(sys.argv[sys.argv.index("--result") + 1])
        transcript_path = Path(sys.argv[sys.argv.index("--transcript") + 1])
        failure = {"id": "PGJT-48/harness-failure", "variant": "postgrest-16.5",
                   "status": "FAIL", "expected": "complete HTTP contract",
                   "observed": f"{type(error).__name__}: {error}",
                   "evidence": str(transcript_path),
                   "notes": "Partial transcript was preserved"}
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(failure, sort_keys=True) + "\n")
        print(json.dumps(failure, indent=2, sort_keys=True))
        exit_code = 1
    raise SystemExit(exit_code)
