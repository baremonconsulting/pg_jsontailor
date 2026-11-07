#!/usr/bin/env python3
"""Verify Jira requirement and public-claim test traceability."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads as strict_json_loads, result_rows, validate


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/coverage_manifest.json"
JIRA = re.compile(r"PGJT-[1-9][0-9]*")
TEST_ID = re.compile(r"PGJT-[1-9][0-9]*/[a-z0-9][a-z0-9-]*")
CLAIM_ID = re.compile(r"PGJT-[1-9][0-9]*/CLAIM-[A-Z0-9-]+")
KINDS = {"functional", "isolation", "tooling", "integration", "benchmark",
         "policy", "partial"}
EXPECTED_CLAIMS = {
    "PGJT-40/CLAIM-SUPPORTED-VERSIONS",
    "PGJT-40/CLAIM-OPTIMISTIC-CONCURRENCY",
    "PGJT-40/CLAIM-RLS-INVOKER",
    "PGJT-40/CLAIM-LIFECYCLE",
    "PGJT-40/CLAIM-TYPES",
    "PGJT-40/CLAIM-BINDING-DRIFT",
    "PGJT-40/CLAIM-MERGE",
    "PGJT-40/CLAIM-PARTITIONS",
    "PGJT-40/CLAIM-DUMP-RESTORE",
    "PGJT-40/CLAIM-BENCHMARK",
    "PGJT-40/CLAIM-POSTGREST",
    "PGJT-44/CLAIM-TYPE-SEMANTICS",
    "PGJT-45/CLAIM-DOCUMENT-SHAPES",
    "PGJT-45/CLAIM-OPERATION-SEMANTICS",
    "PGJT-46/CLAIM-RUNTIME-PRIVILEGES",
    "PGJT-46/CLAIM-RLS-SECURITY",
    "PGJT-47/CLAIM-PARTITION-ROLES",
    "PGJT-47/CLAIM-PARTITION-LIFECYCLE",
    "PGJT-47/CLAIM-PARTITION-RESTORE",
    "PGJT-65/CLAIM-EXECUTABLE-QUICKSTART",
    "PGJT-66/CLAIM-SCHEMA-MIGRATION",
    "PGJT-67/CLAIM-PRECISE-CONTRACT",
    "PGJT-68/CLAIM-STANDALONE",
    "PGJT-72/CLAIM-GENERATED-SQL-NOTICES",
}
EXPECTED_SECTION_DIGESTS = {
    "requirements": "1278b3cce362429fd53d608b2cb72a1f383e3f68929c209f932c56773e256deb",
    "claims": "75956af0052559c996e51d1e00b455db83c4a5a8d37a8c9562fcde0525fb7e6e",
    "manual_evidence": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "supplemental_evidence": "912bfa4e1b74b10258737fa0f233540d2eac879ec4e19bd6f863f06a6f4a5988",
    "runtime_evidence": "35c8187b9a30bf5ac59eab28ad8c84e1b72ddee7acdb5b0abd73b23d020e918e",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def section_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def source(entry: dict, *, label: str) -> str:
    path = entry.get("file")
    marker = entry.get("marker")
    require(isinstance(path, str) and path and not Path(path).is_absolute(),
            f"{label}: invalid source path")
    candidate = ROOT / path
    require(candidate.is_file(), f"{label}: source does not exist: {path}")
    require(isinstance(marker, str) and marker,
            f"{label}: source marker is missing")
    content = candidate.read_text(errors="strict")
    require(marker in content, f"{label}: marker is absent from {path}: {marker}")
    return path


def verify(manifest: dict) -> None:
    require(manifest.get("schema_version") == 1, "unsupported coverage schema")
    supported = manifest.get("supported_postgresql")
    require(supported == ["17", "18", "19"], "supported PostgreSQL matrix differs")
    requirements = manifest.get("requirements")
    claims = manifest.get("claims")
    manual = manifest.get("manual_evidence", [])
    supplemental = manifest.get("supplemental_evidence", [])
    runtime = manifest.get("runtime_evidence", [])
    require(isinstance(requirements, list) and requirements, "requirements are missing")
    require(isinstance(claims, list) and claims, "claims are missing")
    require(isinstance(manual, list), "manual evidence must be a list")
    require(isinstance(supplemental, list), "supplemental evidence must be a list")
    require(isinstance(runtime, list), "runtime evidence must be a list")

    jira_ids: set[str] = set()
    test_ids: set[str] = set()
    for item in requirements:
        jira = item.get("jira")
        test_id = item.get("test_id")
        require(isinstance(jira, str) and JIRA.fullmatch(jira) is not None,
                f"invalid Jira requirement: {jira!r}")
        require(jira not in jira_ids, f"duplicate Jira requirement: {jira}")
        jira_ids.add(jira)
        require(isinstance(test_id, str) and TEST_ID.fullmatch(test_id) is not None,
                f"{jira}: invalid test ID: {test_id!r}")
        require(test_id.startswith(jira + "/"),
                f"{jira}: test ID must use the Jira key as its prefix")
        require(test_id not in test_ids, f"duplicate test ID: {test_id}")
        test_ids.add(test_id)
        require(item.get("kind") in KINDS, f"{jira}: invalid evidence kind")
        versions = item.get("postgresql")
        require(isinstance(versions, list) and versions
                and all(version in supported for version in versions),
                f"{jira}: invalid PostgreSQL scope")
        if item.get("kind") == "partial":
            require(isinstance(item.get("gap"), str) and item["gap"],
                    f"{jira}: partial evidence must identify its tracked gap")
        source(item, label=jira)

    expected = {f"PGJT-{number}" for number in range(2, 39)}
    require(jira_ids == expected,
            "historical Jira coverage differs: missing="
            + repr(sorted(expected - jira_ids)) + ", extra="
            + repr(sorted(jira_ids - expected)))

    for item in manual:
        test_id = item.get("test_id")
        require(isinstance(test_id, str) and TEST_ID.fullmatch(test_id) is not None,
                f"invalid manual test ID: {test_id!r}")
        require(test_id not in test_ids, f"duplicate test ID: {test_id}")
        test_ids.add(test_id)
        tracking = item.get("tracking_jira")
        require(isinstance(tracking, str) and JIRA.fullmatch(tracking) is not None,
                f"{test_id}: manual evidence requires a tracking Jira key")
        require(item.get("status") == "manual", f"{test_id}: invalid manual status")
        source(item, label=test_id)

    supplemental_ids: set[str] = set()
    for item in supplemental:
        test_id = item.get("test_id")
        require(isinstance(test_id, str) and TEST_ID.fullmatch(test_id) is not None,
                f"invalid supplemental test ID: {test_id!r}")
        require(test_id not in test_ids, f"duplicate test ID: {test_id}")
        test_ids.add(test_id)
        supplemental_ids.add(test_id)
        source(item, label=test_id)

    claim_ids: set[str] = set()
    for claim in claims:
        claim_id = claim.get("claim_id")
        require(isinstance(claim_id, str) and CLAIM_ID.fullmatch(claim_id) is not None,
                f"invalid public claim ID: {claim_id!r}")
        require(claim_id not in claim_ids, f"duplicate public claim ID: {claim_id}")
        claim_ids.add(claim_id)
        source(claim, label=claim_id)
        tests = claim.get("tests")
        require(isinstance(tests, list) and tests, f"{claim_id}: no evidence tests")
        unknown = set(tests) - test_ids
        require(not unknown, f"{claim_id}: unknown evidence tests: {sorted(unknown)}")
        if "gap" in claim:
            require(isinstance(claim["gap"], str) and claim["gap"],
                    f"{claim_id}: empty gap declaration")
    require(claim_ids == EXPECTED_CLAIMS,
            "public claim inventory differs: missing="
            + repr(sorted(EXPECTED_CLAIMS - claim_ids)) + ", extra="
            + repr(sorted(claim_ids - EXPECTED_CLAIMS)))

    runtime_ids: set[str] = set()
    runtime_expected = {item["test_id"] for item in requirements} | supplemental_ids
    for item in runtime:
        test_id = item.get("test_id")
        require(test_id in runtime_expected, f"unknown runtime test ID: {test_id!r}")
        require(test_id not in runtime_ids, f"duplicate runtime test ID: {test_id}")
        runtime_ids.add(test_id)
        require(item.get("scope") in {"repository", "postgresql", "postgrest"},
                f"{test_id}: invalid runtime scope")
        suite = item.get("suite")
        require(isinstance(suite, str) and suite,
                f"{test_id}: runtime suite is missing")
        has_field = "field" in item or "value" in item
        require(("field" in item) == ("value" in item),
                f"{test_id}: runtime field and value must be declared together")
        if has_field:
            require(isinstance(item["field"], str) and item["field"],
                    f"{test_id}: invalid runtime result field")
    require(runtime_ids == runtime_expected,
            "runtime evidence inventory differs: missing="
            + repr(sorted(runtime_expected - runtime_ids)) + ", extra="
            + repr(sorted(runtime_ids - runtime_expected)))
    for name, expected_digest in EXPECTED_SECTION_DIGESTS.items():
        require(section_digest(manifest.get(name)) == expected_digest,
                f"{name} differs from the reviewed PGJT-40 contract")


def attest(manifest: dict, scope: str, postgresql: str | None,
           results: dict[str, Path], passed_suites: set[str]) -> dict:
    verify(manifest)
    require(scope in {"repository", "postgresql", "postgrest"},
            "invalid attestation scope")
    if scope in {"postgresql", "postgrest"}:
        require(postgresql in manifest["supported_postgresql"],
                "PostgreSQL version is required for database attestation")
    else:
        require(postgresql is None, "repository attestation cannot declare PostgreSQL")
    parsed = {suite: validate(result_rows(path), suite) for suite, path in results.items()}
    evidence = {item["test_id"]: item for item in manifest["requirements"]}
    evidence.update({item["test_id"]: item
                     for item in manifest.get("supplemental_evidence", [])})
    observed: list[dict] = []
    for item in manifest["runtime_evidence"]:
        if item["scope"] != scope:
            continue
        suite = item["suite"]
        if "field" not in item:
            require(suite in passed_suites,
                    f"{item['test_id']}: passed suite was not attested: {suite}")
        else:
            require(suite in parsed,
                    f"{item['test_id']}: runtime result was not supplied: {suite}")
            matching = [row for row in parsed[suite]
                        if row.get(item["field"]) == item["value"]]
            require(len(matching) == 1,
                    f"{item['test_id']}: expected one runtime result, found {len(matching)}")
            require(matching[0].get("status") == "PASS",
                    f"{item['test_id']}: runtime result did not pass")
        definition = evidence[item["test_id"]]
        record = {"test_id": item["test_id"], "status": "PASS",
                  "coverage": definition.get("kind", "supplemental"),
                  "scope": scope, "postgresql": postgresql,
                  "suite": suite}
        if definition.get("kind") == "partial":
            record["gap"] = definition["gap"]
        observed.append(record)
    require(observed, f"no runtime evidence applies to scope {scope}")
    limitations = [
        {"test_id": row["test_id"], "coverage": row["coverage"],
         "gap": row["gap"]}
        for row in observed if row["coverage"] == "partial"
    ]
    manual_ids = {item["test_id"] for item in manifest.get("manual_evidence", [])}
    limitations.extend(
        {"claim_id": claim["claim_id"], "coverage": "manual",
         "gap": claim["gap"]}
        for claim in manifest["claims"]
        if set(claim["tests"]) & manual_ids
    )
    return {"status": "PASS", "scope": scope, "postgresql": postgresql,
            "tests": observed, "limitations": limitations}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("repository", "postgresql", "postgrest"))
    parser.add_argument("--postgresql")
    parser.add_argument("--result", action="append", default=[], metavar="SUITE=PATH")
    parser.add_argument("--passed-suite", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = strict_json_loads(MANIFEST.read_text())
    verify(manifest)
    if args.scope:
        results: dict[str, Path] = {}
        for value in args.result:
            require("=" in value, f"invalid --result value: {value}")
            suite, path = value.split("=", 1)
            require(suite and suite not in results, f"duplicate result suite: {suite}")
            results[suite] = Path(path)
        output = attest(manifest, args.scope, args.postgresql,
                        results, set(args.passed_suite))
    else:
        require(not args.postgresql and not args.result and not args.passed_suite,
                "runtime arguments require --scope")
        output = {"status": "PASS", "requirements": len(manifest["requirements"]),
                  "claims": len(manifest["claims"]),
                  "manual_evidence": len(manifest.get("manual_evidence", [])),
                  "runtime_evidence": len(manifest.get("runtime_evidence", []))}
    encoded = json.dumps(output, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
