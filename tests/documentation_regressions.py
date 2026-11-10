#!/usr/bin/env python3
"""Negative checks for public documentation and redistributed SQL notices."""
from __future__ import annotations

import io
import hashlib
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_generated_sql_notice import bundle_preamble, verify_archive, verify_bundle, verify_notice, verify_source
from verify_standalone_results import EXPECTED_IDS, verify as verify_results
from source_provenance import archive_provenance, source_identity


def api_contract(api: str, upgrading: str, boundary: str, sql: str) -> None:
    """Bind public capability claims to the installed generator's API inventory."""
    actual = re.findall(r"^CREATE FUNCTION jsontailor\.([a-z_]+)\(", sql, re.MULTILINE)
    documented = re.findall(r"^jsontailor\.([a-z_]+)\(", api, re.MULTILINE)
    assert len(actual) == 7 and len(set(actual)) == 7, "extension API inventory changed"
    assert sorted(documented) == sorted(actual), "documented extension API differs"
    assert "exposes seven functions" in api, "extension API count differs"
    assert "api.orders_dv__bounded_array(bigint)" in api, "missing generated read gate"
    assert "DROP FUNCTION api.orders_dv__bounded_array(bigint);" in upgrading, "recreation leaves read gate"
    for content in (api, upgrading):
        for marker in ("grant_array_reader", "grant_delete_rpc", "reviewed provenance",
                       "owner-only", "PT413"):
            assert marker in content, f"missing capability contract: {marker}"
    assert "no helper EXECUTE grants except" not in boundary, "obsolete HTTP helper policy"
    assert "reviewed array-reader" in boundary and "grant_array_reader" in boundary, "missing HTTP read capability"
    assert "generated-function grants to other recipients" not in api, "blanket helper replay promised"
    assert "view and generated-function grants" not in upgrading, "blanket upgrade replay promised"


def capability_claim_negatives() -> None:
    api = (ROOT / "docs/API.md").read_text()
    upgrading = (ROOT / "docs/UPGRADING.md").read_text()
    boundary = (ROOT / "docs/HTTP_BOUNDARY.md").read_text()
    sql = (ROOT / "sql/pg_jsontailor--0.2.0.sql").read_text()
    api_contract(api, upgrading, boundary, sql)
    mutations = (
        (api.replace("exposes seven functions", "exposes four functions"), upgrading, boundary, sql),
        (api.replace("jsontailor.grant_array_reader(view_name", "jsontailor.missing(view_name"), upgrading, boundary, sql),
        (api.replace("api.orders_dv__bounded_array(bigint)", "missing"), upgrading, boundary, sql),
        (api, upgrading.replace("DROP FUNCTION api.orders_dv__bounded_array(bigint);", ""), boundary, sql),
        (api, upgrading.replace("reviewed provenance", "all grants"), boundary, sql),
        (api.replace("PT413", "OTHER"), upgrading, boundary, sql),
        (api, upgrading, boundary.replace("reviewed array-reader", "unrestricted"), sql),
        (api, upgrading, boundary, sql + "\nCREATE FUNCTION jsontailor.unreviewed() RETURNS void;"),
    )
    for mutation in mutations:
        try:
            api_contract(*mutation)
        except AssertionError:
            continue
        raise AssertionError("invalid capability documentation was accepted")


def claims() -> None:
    readme = (ROOT / "README.md").read_text()
    fixture = (ROOT / "examples/shop_schema.sql").read_text()
    assert readme.index("Warning: use a disposable database only") < readme.index("examples/shop_schema.sql")
    assert fixture.index("DISPOSABLE DATABASE ONLY") < fixture.index("DROP SCHEMA")
    assert "examples/orders_dv.spec.json" in readme
    semantics = (ROOT / "docs/SEMANTICS.md").read_text()
    api = (ROOT / "docs/API.md").read_text()
    upgrading = (ROOT / "docs/UPGRADING.md").read_text()
    for marker in ("first 128 bits of SHA-256", "32 uppercase hexadecimal",
                   "not an authentication token", "object.etag=false", "object key",
                   "visibility-relative", "Hidden child", "DELETE 0",
                   "triggers cannot inspect"):
        assert marker in semantics, f"missing precise public claim: {marker}"
    for marker in ("Generated columns are not supported", "root.fields", "parent_fk",
                   "child key", "cannot require or inspect a client-supplied predicate"):
        assert marker in api, f"missing mapping/deletion restriction: {marker}"
    assert "DROP FUNCTION api.orders_dv__delete_document(bigint, text);" in upgrading
    assert "p.proname = ANY" not in upgrading
    for path in ("README.md", "docs/API.md", "docs/SEMANTICS.md", "docs/UPGRADING.md",
                 "SECURITY.md", "SUPPORT.md"):
        content = (ROOT / path).read_text()
        assert "NOCHECK" not in content, f"misleading shorthand: {path}"
        assert not re.search(r"\b(?:normaliz\w*|stabiliz\w*|behavior\w*|synchroniz\w*)\b",
                             content, re.IGNORECASE), f"non-British public prose: {path}"


def pilot_readiness_documents() -> None:
    """PGJT-116-125 user-facing contracts remain complete and conservative."""
    cookbook = ROOT / "docs/cookbook"
    recipes = sorted(cookbook.glob("[0-9][0-9]-*.md"))
    assert len(recipes) == 15, "cookbook recipe inventory differs"
    for recipe in recipes:
        content = recipe.read_text()
        for marker in ("## Goal", "## Prerequisites", "## Steps"):
            assert marker in content, f"{recipe.name}: missing recipe section {marker}"
        lowered = content.lower()
        for marker in ("expected result", "common errors", "reference"):
            assert marker in lowered, f"{recipe.name}: missing recipe contract {marker}"
    required = {
        "README.md": ("Users and operators", "Maintainers"),
        "SECURITY_MODEL.md": ("Residual risks", "Minimum capabilities"),
        "DEPLOYMENT_RECORD.md": ("RPO and RTO", "Raw PostgREST exposure"),
        "cookbook/08-no-extension.md": ("same PostgreSQL major", "No provider"),
        "cookbook/10-postgrest-proxy.md": ("exactly one `_id=eq.<value>`", "Raw PostgREST"),
        "cookbook/troubleshooting.md": ("PT412", "PT428", "42501", "unknown `limits` key"),
        "cookbook/generated-ddl-outline.md": ("four views, seven functions and six", "inspection-only"),
    }
    for relative, markers in required.items():
        text = (ROOT / "docs" / relative).read_text()
        for marker in markers:
            assert marker in text, f"{relative}: missing {marker}"
    troubleshooting = (ROOT / "docs/cookbook/troubleshooting.md").read_text()
    assert "| 40P01 deadlock |" in troubleshooting
    assert "| 500 (tested PostgREST contract) |" in troubleshooting
    assert "| 0A000 isolation | Writes are not READ COMMITTED | Use READ COMMITTED | 400 |" in troubleshooting
    assert "| 401 anonymous / 403 authenticated |" in troubleshooting
    assert "unknown `limits` key" in troubleshooting
    assert troubleshooting.count("N/A — generator") == 6
    assert "409/503" not in troubleshooting
    required_messages = (
        "document value cannot be converted to the mapped SQL type",
        "invalid or unsupported root.fields.x",
        "root.table is not an ordinary or partitioned table",
        "generated object name exceeds 63 bytes",
        "array.parent_fk requires a validated enforced non-deferrable NO ACTION/RESTRICT foreign key",
        "permission denied for function orders_dv__bounded_array",
        "resource limit must be a supported integer",
        "_id column requires _id in the document",
    )
    for message in required_messages:
        assert message in troubleshooting, "missing exact troubleshooting diagnostic: " + message
    assert "../API.md#supported-mapped-column-types" in troubleshooting
    for cause in ("unvalidated", "not enforced", "deferrable", "CASCADE"):
        assert cause in troubleshooting, "missing foreign-key cause: " + cause
    assert "ON DELETE NO ACTION" in troubleshooting
    assert "ON UPDATE/DELETE" not in troubleshooting
    security_model = (ROOT / "docs/SECURITY_MODEL.md").read_text()
    for marker in ("different tenant", "cross-tenant foreign-key targets",
                   "creation can succeed", "actual application role"):
        assert marker in security_model, "missing PGJT-125 residual: " + marker
    no_extension = (ROOT / "docs/cookbook/08-no-extension.md").read_text()
    assert "SET ROLE target_owner; CREATE SCHEMA api" in no_extension
    for marker in ("pg_dump --schema-only", "source-schema.sql",
                   "Move to a new product version", "reviewed-remove-orders.sql",
                   "standalone-orders-vnext/generated.sql", "reviewed-grants.sql",
                   "reapply the retained previous"):
        assert marker in no_extension, "missing PGJT-116 runbook step: " + marker
    pilot = (ROOT / "docs/PILOT_READINESS.md").read_text()
    for marker in ("irreversibly anonymised", "written customer agreement",
                   "Pilot scope record", "private support channel",
                   "verified deletion owner"):
        assert marker in pilot, "missing PGJT-123 pilot control: " + marker
    support = (ROOT / "SUPPORT.md").read_text()
    assert "private support channel" in support and "Pilot scope record" in support
    release_authority = (ROOT / "docs/RELEASE_AUTHORITY.md").read_text()
    assert "Customer pilot samples and diagnostics are not release evidence" in release_authority
    for marker in ("including `DELETE`", "helper functions remain owner-only",
                   "Revoke broad view defaults", "exact function ACLs"):
        assert marker in security_model, "missing default-privilege residual: " + marker
    readme_status = (ROOT / "README.md").read_text()
    for marker in ("separately signed written pilot terms", "best-effort",
                   "without warranty"):
        assert marker in readme_status, "missing PGJT-123 status wording: " + marker
    security_policy = (ROOT / "SECURITY.md").read_text()
    security_words = " ".join(security_policy.split())
    assert "best-effort basis" in security_words and "without a warranty" in security_words
    readme = (ROOT / "README.md").read_text()
    assert "PostgreSQL 17 and 18 are the supported released majors" in readme
    assert "high-contention write-heavy" in readme.lower()
    assert "protected standalone bundle" in readme
    assert "preliminary figures" not in readme.lower()
    for marker in ("### Before you start", '"root"', '"_metadata"',
                   "docs/SQL_SHAPES.md", "docs/TESTING.md"):
        assert marker in readme, "README entry path missing " + marker
    ddl_outline = (ROOT / "docs/cookbook/generated-ddl-outline.md").read_text()
    for marker in ("Annotated abridged DDL", "CREATE VIEW", "CREATE FUNCTION",
                   "CREATE TRIGGER", "REVOKE ALL", "COMMENT ON VIEW"):
        assert marker in ddl_outline, "generated DDL outline missing " + marker


def pilot_closure_contract(documents: dict[str, str]) -> None:
    """PGJT-126..130: reject deployment/security cookbook regressions."""
    api = documents["docs/API.md"]
    assert "Run DDL returned by `duality_view_ddl`" not in api
    review = documents["docs/cookbook/03-review-ddl.md"]
    for marker in ("encode(convert_to", "bytes.fromhex", "sha256sum", "reviewed.commit",
                   "Extension installed", "No-extension target", "does not consume the saved file"):
        assert marker in review, "missing reviewed-byte/deployment contract: " + marker
    for path in ("examples/quickstart.sql", "docs/UPGRADING.md",
                 "docs/cookbook/04-create-and-verify.md", "docs/cookbook/15-rls.md"):
        content = documents[path].lower()
        assert "immediately" in content and ("displays no" in content or "nothing" in content), path
    rls = documents["docs/cookbook/15-rls.md"]
    assert "Deny direct source-table access" not in rls
    for marker in ("NOBYPASSRLS", "Source privileges remain necessary", "no database",
                   "arbitrary-SQL", "cross-tenant", "ROLLBACK"):
        assert marker in rls, "missing RLS boundary: " + marker
    proxy = documents["docs/cookbook/10-postgrest-proxy.md"]
    assert "upstream URL" not in proxy
    for marker in ("--upstream-socket", "--view orders_dv", "server-unix-socket-mode",
                   "0700", "0600", "RAW_POSTGREST_HOST", "RAW_POSTGREST_PORT",
                   "actual customer network", "NOT_RUN"):
        assert marker in proxy, "missing private-socket gate: " + marker
    pilot = documents["docs/cookbook/14-pilot-acceptance.md"]
    for marker in ("All three majors remain required", "release-candidate qualification",
                   "NOT_RUN", "Customer samples/diagnostics", "two-year"):
        assert marker in pilot, "missing pilot gate: " + marker
    assert "at least two years" in documents["docs/RELEASE_AUTHORITY.md"]
    manifest = documents["examples/orders_deployed_manifest.sql"]
    for marker in ("READ ONLY", "REPEATABLE READ", "pg_get_viewdef", "pg_get_functiondef",
                   "pg_get_triggerdef", "tgenabled", "aclexplode", "acldefault", "attacl",
                   "obj_description", "orders_dv__check", "observed_counts"):
        assert marker in manifest, "missing deployed-state evidence: " + marker


def pilot_closure_negatives() -> None:
    paths = ("docs/API.md", "docs/cookbook/03-review-ddl.md", "examples/quickstart.sql",
             "docs/UPGRADING.md", "docs/cookbook/04-create-and-verify.md",
             "docs/cookbook/15-rls.md", "docs/cookbook/10-postgrest-proxy.md",
             "docs/cookbook/14-pilot-acceptance.md", "docs/RELEASE_AUTHORITY.md",
             "examples/orders_deployed_manifest.sql")
    documents = {path: (ROOT / path).read_text() for path in paths}
    pilot_closure_contract(documents)
    for path, old, new in (
        ("docs/API.md", "", "Run DDL returned by `duality_view_ddl`"),
        ("docs/cookbook/03-review-ddl.md", "bytes.fromhex", "str.strip"),
        ("docs/cookbook/15-rls.md", "NOBYPASSRLS", "BYPASSRLS"),
        ("docs/cookbook/10-postgrest-proxy.md", "--upstream-socket", "--upstream-url"),
        ("examples/orders_deployed_manifest.sql", "tgenabled", "omitted"),
        ("examples/orders_deployed_manifest.sql", "aclexplode", "omitted"),
    ):
        changed = dict(documents)
        changed[path] = changed[path] + new if not old else changed[path].replace(old, new)
        try:
            pilot_closure_contract(changed)
        except AssertionError:
            continue
        raise AssertionError("pilot closure mutation accepted: " + path + "/" + old)


def rejected(callback) -> None:
    try:
        callback()
    except (RuntimeError, FileNotFoundError, KeyError):
        return
    raise AssertionError("invalid generated-SQL package was accepted")


def notices() -> None:
    licence = (ROOT / "LICENSE").read_bytes()
    notice = (ROOT / "GENERATED_SQL_NOTICE").read_bytes()
    verify_notice(notice, licence)
    verify_source(ROOT)
    # Copyright, permission and both disclaimers are independently mandatory.
    for fragment in (b"Copyright (c) 2026, Baremon s.r.o.", b"Permission to use, copy, modify, and distribute",
                     b"IN NO EVENT SHALL THE COPYRIGHT HOLDERS", b"THE COPYRIGHT HOLDERS SPECIFICALLY DISCLAIM"):
        rejected(lambda: verify_notice(notice.replace(fragment, b""), licence))
    rejected(lambda: verify_notice(b"PostgreSQL License\n", licence))
    with tempfile.TemporaryDirectory(prefix="pgjt72-negative-") as temporary:
        folder = Path(temporary)
        (folder / "generated.sql").write_text("SELECT 1;\n")
        rejected(lambda: verify_bundle(folder))
        shutil.copyfile(ROOT / "GENERATED_SQL_NOTICE", folder / "GENERATED_SQL_NOTICE")
        rejected(lambda: verify_bundle(folder))
        script = bundle_preamble('0.2.0', 18, 'api.orders_dv', notice.decode()) + 'SELECT 1;\n\nCOMMIT;\n'
        (folder / "generated.sql").write_text(script)
        verify_bundle(folder)
        for fragment in ('-- pg_jsontailor standalone bundle\n',
                         '-- Copyright (c) 2026, Baremon s.r.o.\n',
                         'BEGIN;\n', 'SET LOCAL search_path = pg_catalog, pg_temp;\n',
                         '    IF target_major <> 18 THEN\n', 'COMMIT;\n'):
            (folder / "generated.sql").write_text(script.replace(fragment, '', 1))
            rejected(lambda: verify_bundle(folder))
        (folder / "generated.sql").write_text(script + "-- jsontailor.duality_view_inventory()\n")
        rejected(lambda: verify_bundle(folder))
        # Keeping only the legal paragraphs must not omit the surrounding notice.
        (folder / "GENERATED_SQL_NOTICE").write_bytes(licence)
        (folder / "generated.sql").write_text(
            bundle_preamble('0.2.0', 18, 'api.orders_dv', licence.decode()) + 'SELECT 1;\n\nCOMMIT;\n')
        rejected(lambda: verify_bundle(folder))
        for omitted in (None, "GENERATED_SQL_NOTICE", "LICENSE", "examples/standalone/README.md"):
            archive_path = folder / "source.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name in ("LICENSE", "GENERATED_SQL_NOTICE", "examples/standalone/README.md"):
                    if name != omitted:
                        data = (ROOT / name).read_bytes()
                        member = tarfile.TarInfo("pg_jsontailor-0.2.0/" + name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
            if omitted is None:
                verify_archive(archive_path)
            else:
                rejected(lambda: verify_archive(archive_path))


def standalone_attestation() -> None:
    with tempfile.TemporaryDirectory(prefix="pgjt68-negatives-") as temporary:
        folder = Path(temporary)
        paths = {}
        import json
        for major in ("17", "18", "19"):
            rows = [{"name": name, "status": "PASS", "postgresql": major,
                     "source_commit": "a" * 40, "dirty_tree": False,
                     "source_provenance": "git-checkout",
                     "generated_sql_sha256": "b" * 64} for name in sorted(EXPECTED_IDS)]
            path = folder / (major + ".jsonl")
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            paths[major] = path
        verify_results(paths, "a" * 40)
        rejected(lambda: verify_results({"17": paths["17"], "18": paths["18"]}, "a" * 40))
        rejected(lambda: verify_results(paths, "c" * 40))
        original = paths["17"].read_text()
        rejected_rows = [original.splitlines()[1:], original.splitlines() + [original.splitlines()[0]],
                         original.replace('"PASS"', '"FAIL"').splitlines(),
                         original.replace('"dirty_tree": false', '"dirty_tree": true').splitlines(),
                         original.replace('"postgresql": "17"', '"postgresql": "18"').splitlines()]
        for mutation in rejected_rows:
            paths["17"].write_text("\n".join(mutation) + "\n")
            rejected(lambda: verify_results(paths, "a" * 40))
        paths["17"].write_text(original)
        for major, path in paths.items():
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            for row in rows:
                row.update(source_provenance="archive-declared", source_archive_sha256="c" * 64,
                           source_manifest_sha256="d" * 64)
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        verify_results(paths, "a" * 40, archive_sha256="c" * 64)
        rejected(lambda: verify_results(paths, "a" * 40))
        rejected(lambda: verify_results(paths, "a" * 40, archive_sha256="e" * 64))
        rejected(lambda: source_identity(ROOT, "a" * 40))
        altered = paths["17"].read_text().replace('"d' + 'd' * 63 + '"', '"e' + 'e' * 63 + '"')
        paths["17"].write_text(altered)
        rejected(lambda: verify_results(paths, "a" * 40, archive_sha256="c" * 64))


def archive_identity() -> None:
    with tempfile.TemporaryDirectory(prefix="pgjt68-archive-negative-") as temporary:
        folder = Path(temporary)
        extracted = folder / "source"
        extracted.mkdir()
        (extracted / "example.sql").write_text("SELECT 1;\n")
        archive_path = folder / "source.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(extracted, arcname="candidate")
        checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        result = archive_provenance(extracted, archive_path, "a" * 40, checksum)
        assert result["source_provenance"] == "archive-declared"
        rejected(lambda: source_identity(extracted))
        rejected(lambda: archive_provenance(extracted, archive_path, "a" * 40, "b" * 64))
        (extracted / "example.sql").write_text("SELECT 2;\n")
        rejected(lambda: archive_provenance(extracted, archive_path, "a" * 40, checksum))
        (extracted / "example.sql").write_text("SELECT 1;\n")
        (extracted / "unexpected.sql").write_text("SELECT 1;\n")
        rejected(lambda: archive_provenance(extracted, archive_path, "a" * 40, checksum))


if __name__ == "__main__":
    claims()
    pilot_readiness_documents()
    pilot_closure_negatives()
    capability_claim_negatives()
    notices()
    standalone_attestation()
    archive_identity()
    print("PGJT-67/documentation-claims: PASS")
    print("PGJT-72/notice-packaging-negatives: PASS")
    print("PGJT-68/attestation-negatives: PASS")
    print("PGJT-75/capability-documentation-negatives: PASS")
    print("PGJT-116-125/pilot-documentation-contract: PASS")
    print("PGJT-126-130/pilot-closure-negatives: PASS")
