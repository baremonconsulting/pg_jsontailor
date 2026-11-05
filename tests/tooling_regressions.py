#!/usr/bin/env python3
"""Regression checks for benchmark evidence and pilot acceptance tooling."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def load_verifier():
    path = ROOT / "bench/verify_results.py"
    spec = importlib.util.spec_from_file_location("benchmark_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_results


def load_jsonl_verifier():
    path = ROOT / "tools/verify_jsonl_results.py"
    spec = importlib.util.spec_from_file_location("jsonl_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


def expect_rejection(verify, folder: Path, expected: str) -> None:
    try:
        verify(folder)
    except RuntimeError as error:
        if expected not in str(error):
            raise AssertionError(f"unexpected rejection: {error}") from error
    else:
        raise AssertionError(f"malformed benchmark was accepted: {folder}")


def benchmark_checks() -> None:
    verify = load_verifier()
    source = ROOT / "bench/reference/azure-04d053e"
    with tempfile.TemporaryDirectory(prefix="pgjt-tooling-") as temporary:
        scratch = Path(temporary)
        changed = scratch / "changed"
        shutil.copytree(source, changed)
        with (changed / "measurements.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            rows = list(reader)
        row = next(row for row in rows if row["warmup"] == "False")
        row["warmup"] = "false"
        with (changed / "measurements.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        expect_rejection(verify, changed, "noncanonical warm-up flag")

        empty = scratch / "empty"
        empty.mkdir()
        (empty / "environment.json").write_text(json.dumps({
            "sizes": [], "operations": ["read"], "warmups": 3, "repetitions": 30}))
        (empty / "summary.json").write_text("[]")
        (empty / "plans.json").write_text("{}")
        with (empty / "measurements.csv").open("w", newline="") as stream:
            csv.DictWriter(stream, fieldnames=fields).writeheader()
        expect_rejection(verify, empty, "invalid size configuration")

        for name,change,expected in (
                ("empty-operations", lambda value: value.update(operations=[]),
                 "invalid operation configuration"),
                ("fractional-size", lambda value: value.update(sizes=[0.5,1,10,100,1000]),
                 "invalid size configuration"),
                ("fractional-warmups", lambda value: value.update(warmups=3.5),
                 "invalid sampling configuration")):
            case = scratch / name
            shutil.copytree(source, case)
            environment = json.loads((case / "environment.json").read_text())
            change(environment)
            (case / "environment.json").write_text(json.dumps(environment))
            expect_rejection(verify, case, expected)


def jsonl_checks() -> None:
    verify = load_jsonl_verifier()
    with tempfile.TemporaryDirectory(prefix="pgjt-jsonl-") as temporary:
        folder = Path(temporary)
        valid = folder / "valid.jsonl"
        valid.write_text('{"name":"one","status":"PASS"}\n')
        if verify(valid) != 1:
            raise AssertionError("valid JSONL result count is wrong")
        valid_id = folder / "valid-id.jsonl"
        valid_id.write_text('{"id":"one","status":"PASS"}\n')
        if verify(valid_id) != 1:
            raise AssertionError("valid id-based JSONL result count is wrong")
        for name, content, expected in (
                ("empty", "", "no test results"),
                ("duplicate", '{"name":"one","status":"PASS"}\n'
                 '{"name":"one","status":"PASS"}\n', "duplicate test identifiers"),
                ("failure", '{"name":"one","status":"FAIL"}\n', "non-PASS")):
            path = folder / f"{name}.jsonl"
            path.write_text(content)
            try:
                verify(path)
            except RuntimeError as error:
                if expected not in str(error):
                    raise AssertionError(f"unexpected JSONL rejection: {error}") from error
            else:
                raise AssertionError(f"invalid JSONL results accepted: {name}")


def pilot_check() -> None:
    path = ROOT / "pilot/synthetic/checks.py"
    spec = importlib.util.spec_from_file_location("pilot_checks", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for response in ((500,{"error":"injected"},2.0),(200,[{"_id":201,"data":{}}],2.0)):
        def http(*_args, **_kwargs):
            return response
        passed, observed = module.operational_read_sample(http)
        if passed:
            raise AssertionError(
                f"invalid response passed pilot acceptance: {response}/{observed}")


def main() -> int:
    benchmark_checks()
    jsonl_checks()
    pilot_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
