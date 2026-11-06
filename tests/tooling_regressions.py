#!/usr/bin/env python3
"""Regression checks for benchmark evidence and pilot acceptance tooling."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from unittest import mock


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


def load_release_verifier():
    path = ROOT / "tools/verify_release_candidate.py"
    spec = importlib.util.spec_from_file_location("release_candidate_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_dump_restore():
    path = ROOT / "tests/dump_restore.py"
    spec = importlib.util.spec_from_file_location("dump_restore_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def release_policy_checks() -> None:
    verifier = load_release_verifier()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    dependency = (ROOT / ".github/workflows/dependency-review.yml").read_text()
    verifier.verify_release_policy(release, dependency)

    start = release.index("required_checks=(")
    end = release.index("\n          archive=", start)
    gate = release[start:end]
    gate = re.sub(
        r"check_runs=\$\(gh api --paginate.*?@tsv'\)",
        'check_runs="$PROBE_CHECK_RUNS"', gate, flags=re.DOTALL)
    if "gh api" in gate:
        raise AssertionError("release gate probe did not isolate the check loop")

    def gate_status(rows: list[tuple[str, str, str]]) -> int:
        value = "\n".join("\t".join(row) for row in rows)
        return subprocess.run(
            ["bash", "-ec", gate], text=True, capture_output=True,
            env={**os.environ, "PROBE_CHECK_RUNS": value}).returncode

    successful = [(name, "completed", "success")
                  for name in verifier.EXACT_SHA_CHECKS]
    if gate_status(successful):
        raise AssertionError("complete exact-SHA release evidence was rejected")
    for index, name in enumerate(verifier.EXACT_SHA_CHECKS):
        if not gate_status(successful[:index] + successful[index + 1:]):
            raise AssertionError(f"missing release check was accepted: {name}")
        failed = list(successful)
        failed[index] = (name, "completed", "failure")
        if not gate_status(failed):
            raise AssertionError(f"failed release check was accepted: {name}")
        unfinished = list(successful)
        unfinished[index] = (name, "in_progress", "")
        if not gate_status(unfinished):
            raise AssertionError(f"unfinished release check was accepted: {name}")

    for changed_release, changed_dependency, expected in (
        (release.replace("            'Dependency review'\n", ""), dependency,
         "exact-SHA check list differs"),
        (release, dependency.replace("  push:\n    branches: [main]\n", ""),
         "does not run on main pushes"),
        (release, dependency.replace("base-ref: ${{ github.event.before }}",
                                     "base-ref: ${{ github.sha }}"),
         "comparison range is not exact"),
    ):
        try:
            verifier.verify_release_policy(changed_release, changed_dependency)
        except RuntimeError as error:
            if expected not in str(error):
                raise AssertionError(f"unexpected release-policy rejection: {error}") from error
        else:
            raise AssertionError("invalid release policy was accepted")

    def run(repo: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=repo, text=True,
                                capture_output=True, check=True)
        return result.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="pgjt-release-policy-") as temporary:
        repo = Path(temporary)
        run(repo, "init", "-q")
        run(repo, "config", "user.name", "Policy Test")
        run(repo, "config", "user.email", "policy@example.invalid")
        (repo / "sql").mkdir()
        (repo / "sql/product.sql").write_text("SELECT 1;\n")
        (repo / "pg_jsontailor.control").write_text("default_version = '0.2.0'\n")
        (repo / "Makefile").write_text("all:\n\t@true\n")
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "base")
        base = run(repo, "rev-parse", "HEAD")
        fingerprints = verifier.product_hashes(repo, base)
        if not fingerprints:
            raise AssertionError("product fingerprints were not produced")
        ok, _ = verifier.benchmark_source_status(repo, base, base)
        if not ok:
            raise AssertionError("exact benchmark source commit was rejected")

        (repo / "README.md").write_text("documentation only\n")
        run(repo, "add", "README.md")
        run(repo, "commit", "-qm", "documentation")
        docs = run(repo, "rev-parse", "HEAD")
        ok, _ = verifier.benchmark_source_status(repo, base, docs)
        if not ok:
            raise AssertionError("product-equivalent benchmark ancestor was rejected")

        for index, path in enumerate(verifier.PRODUCT_EQUIVALENCE_PATHS):
            run(repo, "switch", "-qC", f"changed-{index}", base)
            target = repo / path
            if target.is_dir():
                target = target / "product.sql"
            target.write_text(target.read_text() + "# changed\n")
            run(repo, "add", str(target.relative_to(repo)))
            run(repo, "commit", "-qm", f"change {path}")
            changed = run(repo, "rev-parse", "HEAD")
            ok, detail = verifier.benchmark_source_status(repo, base, changed)
            if ok or "product SQL changed" not in detail:
                raise AssertionError(f"changed product input was accepted: {path}/{detail}")

        ok, detail = verifier.benchmark_source_status(repo, "2" * 40, docs)
        if ok or "absent" not in detail:
            raise AssertionError(f"missing benchmark source was accepted: {detail}")
        run(repo, "switch", "-q", "--orphan", "unrelated")
        (repo / "sql").mkdir()
        (repo / "sql/product.sql").write_text("SELECT 1;\n")
        (repo / "pg_jsontailor.control").write_text("default_version = '0.2.0'\n")
        (repo / "Makefile").write_text("all:\n\t@true\n")
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "unrelated")
        unrelated = run(repo, "rev-parse", "HEAD")
        ok, detail = verifier.benchmark_source_status(repo, base, unrelated)
        if ok or "not an ancestor" not in detail:
            raise AssertionError(f"unrelated benchmark source was accepted: {detail}")
        ok, detail = verifier.benchmark_source_status(
            repo, base, unrelated, recorded_hashes=fingerprints)
        if not ok:
            raise AssertionError(
                f"product-equivalent squash fingerprint was rejected: {detail}")
        ok, detail = verifier.benchmark_source_status(
            repo, "1" * 40, unrelated, recorded_hashes=fingerprints)
        if not ok:
            raise AssertionError(
                f"unreachable squash source fingerprint was rejected: {detail}")

        (repo / "sql/product.sql").write_text("SELECT 2;\n")
        run(repo, "add", "sql/product.sql")
        run(repo, "commit", "-qm", "squash product change")
        changed_squash = run(repo, "rev-parse", "HEAD")
        ok, detail = verifier.benchmark_source_status(
            repo, base, changed_squash, recorded_hashes=fingerprints)
        if ok or "product SQL changed" not in detail:
            raise AssertionError(
                f"changed squash product input was accepted: {detail}")


def dump_restore_command_checks() -> None:
    harness = load_dump_restore()

    def execute(maintenance: str, fail_after_source: bool = False):
        calls: list[tuple[str, tuple[str, ...]]] = []

        def captured(command, name: str, *args: str, input_text: str | None = None):
            calls.append((name, args))
            if (fail_after_source and name == "psql" and input_text
                    and "CREATE EXTENSION pg_jsontailor" in input_text):
                raise RuntimeError("injected restore setup failure")
            stdout = ""
            if name == "psql" and input_text and "SELECT extversion" in input_text:
                stdout = "0.2.0\n1:1\ntrue:false:dump restore health\nUPDATE 1\nt\n"
            return subprocess.CompletedProcess([name, *args], 0, stdout, "")

        argv = ["dump_restore.py", "--pg-bin", "/unused",
                "--maintenance-database", maintenance,
                "--database-prefix", "probe"]
        with mock.patch.object(harness.Commands, "run", captured), \
                mock.patch.object(sys, "argv", argv):
            if fail_after_source:
                try:
                    harness.main()
                except RuntimeError as error:
                    if "injected restore setup failure" not in str(error):
                        raise
                else:
                    raise AssertionError("injected dump/restore failure was ignored")
            elif harness.main() != 0:
                raise AssertionError("captured dump/restore main failed")
        return [(name, args) for name, args in calls if name in ("createdb", "dropdb")]

    for maintenance in ("postgres", "selected_maintenance"):
        lifecycle = execute(maintenance)
        expected = [
            ("dropdb", ("--if-exists", "--force", "--maintenance-db", maintenance,
                        "probe_custom")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db", maintenance,
                        "probe_plain")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db", maintenance,
                        "probe_source")),
            ("createdb", ("--maintenance-db", maintenance, "probe_source")),
            ("createdb", ("--maintenance-db", maintenance, "probe_custom")),
            ("createdb", ("--maintenance-db", maintenance, "probe_plain")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db", maintenance,
                        "probe_plain")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db", maintenance,
                        "probe_custom")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db", maintenance,
                        "probe_source")),
        ]
        if lifecycle != expected:
            raise AssertionError(
                f"dump/restore maintenance forwarding differs for {maintenance}: {lifecycle}")

    failed = execute("failure_maintenance", fail_after_source=True)
    cleanup = failed[-3:]
    if cleanup != [
            ("dropdb", ("--if-exists", "--force", "--maintenance-db",
                        "failure_maintenance", "probe_plain")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db",
                        "failure_maintenance", "probe_custom")),
            ("dropdb", ("--if-exists", "--force", "--maintenance-db",
                        "failure_maintenance", "probe_source"))]:
        raise AssertionError(f"failure cleanup lost maintenance database: {cleanup}")


def main() -> int:
    benchmark_checks()
    jsonl_checks()
    pilot_check()
    release_policy_checks()
    dump_restore_command_checks()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
