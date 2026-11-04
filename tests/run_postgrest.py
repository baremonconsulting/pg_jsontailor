#!/usr/bin/env python3
"""Run mandatory PostgREST test 13 and save an HTTP transcript."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def request(base: str, method: str, path: str, body=None, headers=None):
    payload = None if body is None else json.dumps(body).encode()
    values = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + path, data=payload, headers=values, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read().decode()
            return response.status, dict(response.headers), raw
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:3050")
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    calls = []

    def call(method, path, body=None, headers=None):
        status, response_headers, raw = request(args.base, method, path, body, headers)
        calls.append({"method": method, "path": path, "request": body,
                      "request_headers": headers or {}, "status": status,
                      "response_headers": response_headers, "response": raw})
        return status, response_headers, raw

    status, _, raw = call("GET", "/orders_dv?_id=eq.42")
    document = json.loads(raw)[0]["data"]
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

    new_document = {"status": "rest-new", "customer": {"customerId": 7},
                    "items": [{"product": "REST item", "qty": 1, "price": 2.50}]}
    post_status, _, raw = call("POST", "/orders_dv", {"data": new_document},
                               {"Prefer": "return=representation"})
    inserted = json.loads(raw)[0]
    new_id = inserted["_id"]
    delete_status, _, _ = call("POST", "/rpc/orders_dv__delete_document", {
        "expected_id": new_id,
        "expected_etag": inserted["data"]["_metadata"]["etag"],
    })
    options_status, options_headers, _ = call("OPTIONS", "/orders_dv")
    table_get, _, _ = call("GET", "/orders")
    table_patch, _, _ = call("PATCH", "/order_items?id=eq.1", {"quantity": 99})

    allow = options_headers.get("Allow", "")
    expected = [status == 200, patch_status == 200, stale_status == 412,
                missing_status == 428, incomplete_status == 422,
                post_status == 201, delete_status == 200, options_status == 200,
                all(method in allow for method in ("POST", "PATCH", "DELETE")),
                table_get == 404, table_patch == 404]
    passed = all(expected)
    lines = ["# PostgREST transcript", "", f"PostgREST endpoint: `{args.base}`", ""]
    for item in calls:
        lines += [f"## {item['method']} `{item['path']}`", "",
                  f"Status: **{item['status']}**", "",
                  "Request headers: `" + json.dumps(item['request_headers'], sort_keys=True) + "`", "",
                  "Request body:", "```json", json.dumps(item['request'], ensure_ascii=False), "```", "",
                  "Response:", "```json", item['response'], "```", ""]
    args.transcript.parent.mkdir(parents=True, exist_ok=True)
    args.transcript.write_text("\n".join(lines))
    result = {"id": "13", "variant": "V1/api", "status": "PASS" if passed else "FAIL",
              "expected": "200/201, PT412=412, PT428=428, PT422=422, hidden tables=404",
              "observed": {"get": status, "patch": patch_status, "stale": stale_status,
                           "missing": missing_status, "incomplete": incomplete_status,
                           "post": post_status, "delete_rpc": delete_status,
                           "options": options_status,
                           "allow": allow, "table_get": table_get, "table_patch": table_patch},
              "evidence": str(args.transcript), "notes": "PATCH replaces the data document"}
    args.result.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
