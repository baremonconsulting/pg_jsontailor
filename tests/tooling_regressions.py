#!/usr/bin/env python3
"""Regression checks for benchmark evidence and pilot acceptance tooling."""
from __future__ import annotations

import csv
import copy
import hashlib
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.product_fingerprint import digest, working_tree_hashes


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


def load_dump_restore_matrix():
    dependency = load_dump_restore()
    path = ROOT / "tests/dump_restore_matrix.py"
    spec = importlib.util.spec_from_file_location("dump_restore_matrix_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"dump_restore": dependency}):
        spec.loader.exec_module(module)
    return module


def load_coverage_verifier():
    path = ROOT / "tools/verify_test_coverage.py"
    spec = importlib.util.spec_from_file_location("test_coverage_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def coverage_manifest_checks() -> None:
    verifier = load_coverage_verifier()
    manifest = json.loads((ROOT / "tests/coverage_manifest.json").read_text())
    verifier.verify(manifest)

    def rejected(change, expected: str) -> None:
        candidate = copy.deepcopy(manifest)
        change(candidate)
        try:
            verifier.verify(candidate)
        except RuntimeError as error:
            if expected not in str(error):
                raise AssertionError(f"unexpected coverage rejection: {error}") from error
        else:
            raise AssertionError(f"invalid coverage manifest accepted: {expected}")

    rejected(lambda value: value["requirements"].__setitem__(
        1, copy.deepcopy(value["requirements"][0])), "duplicate Jira requirement")
    rejected(lambda value: value["requirements"][0].__setitem__(
        "test_id", "PGJT-99/wrong-prefix"), "must use the Jira key")
    rejected(lambda value: value["requirements"][0].__setitem__(
        "marker", "marker-that-does-not-exist"), "marker is absent")
    rejected(lambda value: value["claims"][0].__setitem__(
        "tests", ["PGJT-99/unknown"]), "unknown evidence tests")
    rejected(lambda value: value["requirements"][0].__setitem__("kind", "partial"),
             "partial evidence must identify")
    rejected(lambda value: value["claims"].pop(), "public claim inventory differs")
    rejected(lambda value: value["claims"][0].__setitem__(
        "claim_id", "PGJT-999/CLAIM-UNKNOWN"), "public claim inventory differs")
    rejected(lambda value: value["runtime_evidence"][0].update(
        {"scope": "postgresql", "suite": "installcheck"}),
        "runtime_evidence differs from the reviewed PGJT-40 contract")
    rejected(lambda value: value["claims"][0].__setitem__(
        "tests", ["PGJT-48/http-get"]),
        "claims differs from the reviewed PGJT-40 contract")
    rejected(lambda value: value["requirements"][0].__setitem__(
        "kind", "policy"),
        "requirements differs from the reviewed PGJT-40 contract")
    rejected(lambda value: value["supplemental_evidence"][0].update(
        {"file": "README.md", "marker": "PostgREST scenarios"}),
        "supplemental_evidence differs from the reviewed PGJT-40 contract")

    repository = verifier.attest(manifest, "repository", None, {}, {"repository"})
    expected_repository = sum(item["scope"] == "repository"
                              for item in manifest["runtime_evidence"])
    if len(repository["tests"]) != expected_repository:
        raise AssertionError("repository runtime attestation count differs")
    if any(item.get("claim_id") == "PGJT-40/CLAIM-POSTGREST"
           for item in repository["limitations"]):
        raise AssertionError("automated PostgREST claim remains a limitation")

    with tempfile.TemporaryDirectory(prefix="pgjt-coverage-") as temporary:
        folder = Path(temporary)
        rows: dict[str, list[dict]] = {}
        for item in manifest["runtime_evidence"]:
            if item["scope"] != "postgresql" or "field" not in item:
                continue
            row = {item["field"]: item["value"], "status": "PASS"}
            if row not in rows.setdefault(item["suite"], []):
                rows[item["suite"]].append(row)
        paths = {}
        for suite, values in rows.items():
            path = folder / f"{suite}.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in values))
            paths[suite] = path
        database = verifier.attest(
            manifest, "postgresql", "18", paths, {"installcheck", "dump_restore"})
        expected_database = sum(item["scope"] == "postgresql"
                                for item in manifest["runtime_evidence"])
        if len(database["tests"]) != expected_database:
            raise AssertionError("database runtime attestation count differs")
        postgrest_rows = [
            {item["field"]: item["value"], "status": "PASS"}
            for item in manifest["runtime_evidence"]
            if item["scope"] == "postgrest"
        ]
        postgrest_path = folder / "postgrest.jsonl"
        postgrest_path.write_text(
            "".join(json.dumps(row) + "\n" for row in postgrest_rows))
        postgrest = verifier.attest(
            manifest, "postgrest", "18", {"postgrest": postgrest_path}, set())
        if len(postgrest["tests"]) != len(postgrest_rows):
            raise AssertionError("PostgREST runtime attestation count differs")
        partials = [row for row in database["tests"]
                    if row["coverage"] == "partial"]
        if any(not row.get("gap") for row in partials):
            raise AssertionError("partial runtime evidence hides its tracked gap")

        validation_selectors = [
            item["value"] for item in manifest["runtime_evidence"]
            if item["scope"] == "postgresql"
            and item["suite"] == "validation"
            and item.get("field") == "id"
        ]
        if not validation_selectors or not all(
                isinstance(value, str) for value in validation_selectors):
            raise AssertionError(
                "validation runtime selectors must match the writer's string IDs")

        def runtime_rejected(change, expected: str) -> None:
            changed_paths = {}
            for suite, path in paths.items():
                changed = folder / f"changed-{suite}.jsonl"
                changed.write_text(path.read_text())
                changed_paths[suite] = changed
            change(changed_paths)
            try:
                verifier.attest(manifest, "postgresql", "18", changed_paths,
                                {"installcheck", "dump_restore"})
            except RuntimeError as error:
                if expected not in str(error):
                    raise AssertionError(
                        f"unexpected runtime-evidence rejection: {error}") from error
            else:
                raise AssertionError(
                    f"invalid runtime evidence was accepted: {expected}")

        selected = next(item for item in manifest["runtime_evidence"]
                        if item["scope"] == "postgresql" and "field" in item)

        def remove_selected(changed_paths) -> None:
            path = changed_paths[selected["suite"]]
            values = [json.loads(line) for line in path.read_text().splitlines()]
            values = [row for row in values
                      if row.get(selected["field"]) != selected["value"]]
            path.write_text("".join(json.dumps(row) + "\n" for row in values))

        runtime_rejected(remove_selected, "expected one runtime result, found 0")

        def fail_selected(changed_paths) -> None:
            path = changed_paths[selected["suite"]]
            values = [json.loads(line) for line in path.read_text().splitlines()]
            for row in values:
                if row.get(selected["field"]) == selected["value"]:
                    row["status"] = "FAIL"
            path.write_text("".join(json.dumps(row) + "\n" for row in values))

        runtime_rejected(fail_selected, "runtime result did not pass")

        def duplicate_selected(changed_paths) -> None:
            path = changed_paths[selected["suite"]]
            values = [json.loads(line) for line in path.read_text().splitlines()]
            duplicate = next(row for row in values
                             if row.get(selected["field"]) == selected["value"])
            values.append(copy.deepcopy(duplicate))
            path.write_text("".join(json.dumps(row) + "\n" for row in values))

        runtime_rejected(duplicate_selected, "expected one runtime result, found 2")

        def duplicate_status_key(changed_paths) -> None:
            path = changed_paths[selected["suite"]]
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                row = json.loads(line)
                if row.get(selected["field"]) == selected["value"]:
                    lines[index] = line[:-1] + ',"status":"FAIL","status":"PASS"}'
                    break
            path.write_text("\n".join(lines) + "\n")

        runtime_rejected(duplicate_status_key, "duplicate JSON key: status")

        first_suite = next(iter(paths))
        paths[first_suite].write_text("")
        try:
            verifier.attest(manifest, "postgresql", "18", paths,
                            {"installcheck", "dump_restore"})
        except RuntimeError as error:
            if "runtime result has no object rows" not in str(error):
                raise AssertionError(f"unexpected missing-result rejection: {error}") from error
        else:
            raise AssertionError("missing runtime results were accepted")


def repeatability_harness_checks() -> None:
    path = ROOT / "tests/integrity_repeatability.py"
    spec = importlib.util.spec_from_file_location("integrity_repeatability", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="pgjt-41-harness-") as temporary:
        folder = Path(temporary)
        first = folder / "first.jsonl"
        second = folder / "second.jsonl"
        rows = [
            {"name": "one", "status": "PASS"},
            {"name": "two", "status": "PASS"},
        ]

        def write(path: Path, values: list[dict]) -> None:
            path.write_text("".join(json.dumps(row) + "\n" for row in values))

        write(first, rows)
        write(second, rows)
        digest = hashlib.sha256(b"one\ntwo").hexdigest()
        count, names = module.verify_runs(
            first, second, expected_count=2, expected_digest=digest)
        if count != 2 or names != ["one", "two"]:
            raise AssertionError("repeatability harness changed valid result IDs")
        for label, changed, expected in (
                ("stale-extra", rows + [{"name": "stale", "status": "PASS"}],
                 "result IDs differ"),
                ("missing", rows[:1], "result IDs differ"),
                ("failed", [{"name": "one", "status": "FAIL"}, rows[1]],
                 "non-PASS"),
                ("duplicate", [rows[0], rows[0]], "duplicate result names")):
            write(second, changed)
            try:
                module.verify_runs(
                    first, second, expected_count=2, expected_digest=digest)
            except RuntimeError as error:
                if expected not in str(error):
                    raise AssertionError(
                        f"unexpected repeatability rejection for {label}: {error}")
            else:
                raise AssertionError(f"repeatability harness accepted {label}")
        changed = [{"name": "one", "status": "PASS"},
                   {"name": "changed", "status": "PASS"}]
        write(first, changed)
        write(second, changed)
        try:
            module.verify_runs(first, second, expected_count=2,
                               expected_digest=digest)
        except RuntimeError as error:
            if "reviewed PGJT-41 inventory" not in str(error):
                raise AssertionError(f"unexpected inventory rejection: {error}")
        else:
            raise AssertionError("unreviewed integrity inventory was accepted")
        first.write_text('{"name":"one","status":"FAIL","status":"PASS"}\n')
        try:
            module.load_results(first)
        except RuntimeError as error:
            if "duplicate JSON key" not in str(error):
                raise AssertionError(f"unexpected duplicate-key rejection: {error}")
        else:
            raise AssertionError("ambiguous integrity result was accepted")
        same = folder / "same.jsonl"
        with mock.patch.object(sys, "argv", [
                "integrity_repeatability.py", "--psql", "psql",
                "--database", "db", "--output", str(same),
                "--repeatability-output", str(same)]):
            try:
                module.main()
            except RuntimeError as error:
                if "distinct paths" not in str(error):
                    raise AssertionError(f"unexpected output-alias rejection: {error}")
            else:
                raise AssertionError("aliased repeatability outputs were accepted")
        first_link = folder / "integrity-link.jsonl"
        second_link = folder / "repeatability-link.jsonl"
        first_link.write_text("existing")
        os.link(first_link, second_link)
        with mock.patch.object(sys, "argv", [
                "integrity_repeatability.py", "--psql", "psql",
                "--database", "db", "--output", str(first_link),
                "--repeatability-output", str(second_link)]):
            try:
                module.main()
            except RuntimeError as error:
                if "distinct paths" not in str(error):
                    raise AssertionError(f"unexpected hardlink rejection: {error}")
            else:
                raise AssertionError("hardlinked repeatability outputs were accepted")


def subtransaction_matrix_checks() -> None:
    path = ROOT / "tests/subtransaction_regressions.py"
    spec = importlib.util.spec_from_file_location("subtransaction_regressions", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = (
        (2, 1, 100, 100, True),
        (1, 1, 100, 100, False),
        (0, 1, 100, 100, False),
        (2, 1, 99, 100, False),
        (2, 1, 101, 100, False),
    )
    for delta, expected, changed, expected_changed, result in cases:
        if module.is_positive_xid_noise(
                delta, expected, changed, expected_changed) is not result:
            raise AssertionError("subtransaction retry policy is fail-open")
    if module.is_positive_xid_noise(2, 1, 100, 100, False):
        raise AssertionError("subtransaction retry hides invalid base-table state")
    if len(module.EXPECTED_WORKLOADS) != 8 or len(set(module.EXPECTED_WORKLOADS)) != 8:
        raise AssertionError("PGJT-42 workload inventory is incomplete or duplicated")


def deterministic_isolation_checks() -> None:
    makefile = (ROOT / "Makefile").read_text()
    spec = (ROOT / "tests/specs/pgjt_43_concurrency.spec").read_text()
    expected = (ROOT / "tests/expected/pgjt_43_concurrency.out").read_text()
    test_ids = {
        "PGJT-34/current-object-lock",
        "PGJT-34/new-object-lock",
        "PGJT-34/stale-object-etag",
        "PGJT-37/delete-rpc-source-identity",
        "PGJT-38/deferrable-child-key-race",
    }
    isolation_line = next(
        (line for line in makefile.splitlines() if line.startswith("ISOLATION =")), "")
    if isolation_line.split().count("pgjt_43_concurrency") != 1:
        raise AssertionError("PGJT-43 isolation schedule is not executed exactly once")
    if spec.count("permutation ") != 5:
        raise AssertionError("PGJT-43 isolation permutation inventory differs")
    for test_id in test_ids:
        if test_id not in spec or test_id not in expected:
            raise AssertionError(f"PGJT-43 evidence is missing: {test_id}")
    if expected.count("<waiting ...>") != 5:
        raise AssertionError("PGJT-43 expected output does not prove five lock waits")
    if "PT412" not in expected or expected.count("PT422") < 2:
        raise AssertionError("PGJT-43 expected SQLSTATE evidence differs")
    partition_spec = (ROOT / "tests/specs/pgjt_47_partition.spec").read_text()
    partition_expected = (ROOT / "tests/expected/pgjt_47_partition.out").read_text()
    if isolation_line.split().count("pgjt_47_partition") != 1:
        raise AssertionError("PGJT-47 isolation schedule is not executed exactly once")
    if partition_spec.count("permutation ") != 2:
        raise AssertionError("PGJT-47 isolation permutation inventory differs")
    if partition_expected.count("<waiting ...>") != 2:
        raise AssertionError("PGJT-47 expected output does not prove both lock waits")
    if (partition_spec.count("PGJT-47/concurrent-partition-ddl") != 2
            or partition_expected.count("PGJT-47/concurrent-partition-ddl") != 4):
        raise AssertionError("PGJT-47 isolation evidence inventory differs")


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
        canonical_makefile = (ROOT / "Makefile").read_text()
        run(repo, "init", "-q")
        run(repo, "config", "user.name", "Policy Test")
        run(repo, "config", "user.email", "policy@example.invalid")
        (repo / "sql").mkdir()
        (repo / "sql/product.sql").write_text("SELECT 1;\n")
        (repo / "pg_jsontailor.control").write_text("default_version = '0.2.0'\n")
        (repo / "Makefile").write_text(canonical_makefile)
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "base")
        base = run(repo, "rev-parse", "HEAD")
        fingerprints = verifier.product_hashes(repo, base)
        if not fingerprints:
            raise AssertionError("product fingerprints were not produced")
        expected_inputs = {"Makefile", "pg_jsontailor.control", "sql/product.sql"}
        if set(fingerprints) != expected_inputs:
            raise AssertionError(f"product fingerprint inventory differs: {fingerprints}")
        if working_tree_hashes(repo) != fingerprints:
            raise AssertionError("benchmark producer and release verifier fingerprints differ")
        ok, _ = verifier.benchmark_source_status(repo, base, base)
        if not ok:
            raise AssertionError("exact benchmark source commit was rejected")
        legacy_hashes = verifier.product_hashes(
            repo, base, fingerprint_schema=1)
        ok, detail = verifier.benchmark_source_status(
            repo, base, base, recorded_hashes=legacy_hashes,
            fingerprint_schema=1)
        if not ok:
            raise AssertionError(f"exact-source schema-v1 evidence was rejected: {detail}")

        (repo / "README.md").write_text("documentation only\n")
        run(repo, "add", "README.md")
        run(repo, "commit", "-qm", "documentation")
        docs = run(repo, "rev-parse", "HEAD")
        ok, _ = verifier.benchmark_source_status(repo, base, docs)
        if not ok:
            raise AssertionError("product-equivalent benchmark ancestor was rejected")

        declaration_changes = {
            "REGRESS": ("REGRESS = basic", "REGRESS = basic additional_contract"),
            "REGRESS_OPTS": (
                "REGRESS_OPTS = --inputdir=tests --outputdir=tests/output",
                "REGRESS_OPTS = --inputdir=tests --outputdir=tests/output --load-extension=pg_jsontailor"),
            "ISOLATION": (
                "ISOLATION = concurrency pgjt_43_concurrency pgjt_47_partition",
                "ISOLATION = concurrency pgjt_43_concurrency pgjt_47_partition additional_contract"),
            "ISOLATION_OPTS": (
                "ISOLATION_OPTS = --inputdir=tests --outputdir=tests/output_iso",
                "ISOLATION_OPTS = --inputdir=tests --outputdir=tests/output_iso --load-extension=pg_jsontailor"),
        }
        for declaration, (original, literal) in declaration_changes.items():
            run(repo, "switch", "-qC", f"literal-{declaration.lower()}", base)
            (repo / "Makefile").write_text(
                canonical_makefile.replace(original, literal))
            run(repo, "add", "Makefile")
            run(repo, "commit", "-qm", f"literal {declaration} test-only change")
            test_only = run(repo, "rev-parse", "HEAD")
            ok, detail = verifier.benchmark_source_status(repo, base, test_only)
            if not ok:
                raise AssertionError(
                    f"literal {declaration} change invalidated performance evidence: {detail}")
            ok, _ = verifier.benchmark_source_status(
                repo, base, test_only, fingerprint_schema=1)
            if ok:
                raise AssertionError(
                    f"schema-v1 evidence accepted a {declaration} byte change")

        (repo / "Makefile").write_text(canonical_makefile.replace(
            "DATA = sql/pg_jsontailor--0.2.0.sql", "DATA = sql/other.sql"))
        run(repo, "add", "Makefile")
        run(repo, "commit", "-qm", "runtime PGXS change")
        runtime_make = run(repo, "rev-parse", "HEAD")
        ok, detail = verifier.benchmark_source_status(repo, base, runtime_make)
        if ok or "product SQL changed" not in detail:
            raise AssertionError(f"runtime Makefile change was accepted: {detail}")

        for declaration, (original, _) in declaration_changes.items():
            run(repo, "switch", "-qC", f"dynamic-{declaration.lower()}", base)
            dynamic = (f"{declaration} = "
                       "$(eval DATA = sql/other.sql)")
            (repo / "Makefile").write_text(
                canonical_makefile.replace(original, dynamic))
            run(repo, "add", "Makefile")
            run(repo, "commit", "-qm", f"dynamic {declaration} declaration")
            dynamic_make = run(repo, "rev-parse", "HEAD")
            ok, detail = verifier.benchmark_source_status(repo, base, dynamic_make)
            if ok or "product SQL changed" not in detail:
                raise AssertionError(
                    f"dynamic {declaration} expression was accepted: {detail}")

        run(repo, "switch", "-qC", "bare-cr", base)
        (repo / "Makefile").write_bytes(
            b"all:\n\t@true\nDATA = sql/product.sql\rREGRESS = basic\n")
        run(repo, "add", "Makefile")
        run(repo, "commit", "-qm", "bare CR Makefile mutation")
        bare_cr = run(repo, "rev-parse", "HEAD")
        ok, detail = verifier.benchmark_source_status(repo, base, bare_cr)
        if ok or "product SQL changed" not in detail:
            raise AssertionError(f"bare-CR Makefile mutation was accepted: {detail}")

        contextual_makefiles = {
            "continuation": (
                b"DATA = sql/product.sql \\" + b"\nREGRESS = first.sql\n",
                b"DATA = sql/product.sql \\" + b"\nREGRESS = changed.sql\n"),
            "define": (
                b"define DATA\nREGRESS = first.sql\nendef\n",
                b"define DATA\nREGRESS = changed.sql\nendef\n"),
            "dependent-runtime": (
                b"DATA = $(REGRESS)\nREGRESS = first.sql\n",
                b"DATA = $(REGRESS)\nREGRESS = changed.sql\n"),
            "substitution-runtime": (
                b"DATA = $(REGRESS:%=sql/%.sql)\nREGRESS = first\n",
                b"DATA = $(REGRESS:%=sql/%.sql)\nREGRESS = changed\n"),
            "conditional-runtime": (
                b"REGRESS =\nifdef REGRESS\nDATA = sql/first.sql\nendif\n",
                b"REGRESS = changed\nifdef REGRESS\nDATA = sql/first.sql\nendif\n"),
            "recipe-prefix": (
                b".RECIPEPREFIX = R\ninstall:\nREGRESS = first.sql\n",
                b".RECIPEPREFIX = R\ninstall:\nREGRESS = changed.sql\n"),
            "exported-test-variable": (
                b"REGRESS = first.sql\nexport REGRESS\n",
                b"REGRESS = changed.sql\nexport REGRESS\n"),
        }
        for label, (before, after) in contextual_makefiles.items():
            if digest("Makefile", before) == digest("Makefile", after):
                raise AssertionError(
                    f"contextual Makefile mutation was normalized: {label}")

        canonical_bytes = canonical_makefile.encode()
        moved = canonical_makefile.splitlines()
        declarations = moved[3:7]
        del moved[3:7]
        include_index = moved.index("include $(PGXS)")
        moved[include_index + 1:include_index + 1] = declarations
        removed = "\n".join(canonical_makefile.splitlines()[:3] +
                            canonical_makefile.splitlines()[7:]) + "\n"
        for label, changed in (("relocated", "\n".join(moved) + "\n"),
                               ("removed", removed)):
            if digest("Makefile", canonical_bytes) == digest("Makefile", changed.encode()):
                raise AssertionError(f"test declaration {label} collision")

        run(repo, "switch", "-qC", "unicode-input", base)
        (repo / "sql/extraé.sql").write_text("SELECT 2;\n")
        run(repo, "add", "sql/extraé.sql")
        run(repo, "commit", "-qm", "unicode product input")
        unicode_commit = run(repo, "rev-parse", "HEAD")
        unicode_hashes = verifier.product_hashes(repo, unicode_commit)
        if unicode_hashes is None or "sql/extraé.sql" not in unicode_hashes:
            raise AssertionError("Unicode product input did not round-trip")

        run(repo, "switch", "-qC", "symlink-input", base)
        (repo / "control-target").write_text("default_version = '0.2.0'\n")
        (repo / "pg_jsontailor.control").unlink()
        os.symlink("control-target", repo / "pg_jsontailor.control")
        run(repo, "add", "control-target", "pg_jsontailor.control")
        run(repo, "commit", "-qm", "symlink product input")
        symlink_commit = run(repo, "rev-parse", "HEAD")
        if verifier.product_hashes(repo, symlink_commit) is not None:
            raise AssertionError("non-regular product input was accepted")

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
        (repo / "Makefile").write_text(canonical_makefile)
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
                stdout = ("0.2.0\n2:2\ntrue:false:dump restore health\nUPDATE 1\nt\n"
                          "part_detail:3,part_item:3,part_root:3\n"
                          "partition-root:partition-detail:partition-item\nt\n"
                          "UPDATE 1\npre-regen-write:partition_restore.part_root_high\n"
                          "UPDATE 1\nMERGE 1\nMERGE 1\n"
                          "restored-update:merge-update:partition_restore.part_root_high:"
                          "partition_restore.part_item_high\n"
                          "DELETE 1\nMERGE 1\nINSERT 0 1\nt\n0:0\n\nOK:true\n")
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


def dump_restore_matrix_cleanup_checks() -> None:
    harness = load_dump_restore_matrix()

    class FailingBootstrap:
        def __init__(self):
            self.calls = []

        def drop(self, database):
            self.calls.append(("drop", database))
            if database in {"custom", "maintenance"}:
                raise RuntimeError("injected drop failure")

        def sql(self, database, sql):
            self.calls.append(("roles", database))
            raise RuntimeError("injected role failure")

    targets = ("plain", "custom", "source", "maintenance")
    preserving = FailingBootstrap()
    harness.cleanup(preserving, targets, "postgres", True)
    expected = [("drop", value) for value in targets] + [("roles", "postgres")]
    if preserving.calls != expected:
        raise AssertionError("PGJT-49 cleanup stopped after an injected failure")
    reporting = FailingBootstrap()
    try:
        harness.cleanup(reporting, targets, "postgres", False)
    except RuntimeError as error:
        if "custom" not in str(error) or "maintenance" not in str(error) or "roles" not in str(error):
            raise AssertionError("PGJT-49 cleanup omitted collected failures") from error
    else:
        raise AssertionError("PGJT-49 cleanup accepted injected failures")
    if reporting.calls != expected:
        raise AssertionError("PGJT-49 cleanup did not attempt every target")


def main() -> int:
    coverage_manifest_checks()
    repeatability_harness_checks()
    subtransaction_matrix_checks()
    deterministic_isolation_checks()
    benchmark_checks()
    jsonl_checks()
    pilot_check()
    release_policy_checks()
    dump_restore_command_checks()
    dump_restore_matrix_cleanup_checks()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
