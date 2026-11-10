#!/usr/bin/env python3
"""Negative checks for public documentation and redistributed SQL notices."""
from __future__ import annotations

import io
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_generated_sql_notice import (EXPECTED_GENERATOR_SHA256, bundle_preamble,
                                       verify_archive, verify_bundle, verify_notice, verify_source)
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
    for marker in ("`db-max-rows` is the enforcing row bound", "no `limit`",
                   "`Range` larger than", "Before every REST pilot"):
        assert marker in boundary, "missing HTTP row-bound contract: " + marker
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
        (api, upgrading, boundary.replace("`db-max-rows` is the enforcing row bound", "row limit unknown"), sql),
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
        "cookbook/10-postgrest-proxy.md": ("exactly one `_id=eq.<value>`", "Raw PostgREST",
                                             "actual enforcing control for collection row count"),
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
    assert "| 0A000 `document writes require READ COMMITTED isolation` |" in troubleshooting
    assert "| 401 anonymous / 403 authenticated |" in troubleshooting
    assert "unknown `limits` key" in troubleshooting
    # PGJT-143 deliberately adds three exact specification-error rows.
    assert troubleshooting.count("N/A — generator") == 9
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
        "stored child limit exceeded", "document byte limit exceeded",
        "source relation role changed; regenerate the duality view",
        "source relation or column binding changed; regenerate the duality view",
        "root.key is not a single-column primary key", "root.table must have two parts",
        "unknown key root.typo", "invalid mapping root.fields.x",
        "delete RPC requires DELETE on the duality view",
        "row-level security prevents locking root", "document writes require READ COMMITTED isolation",
        "root insert is disabled", "root update is disabled", "root delete is disabled",
        "array insert is disabled", "array update is disabled", "array delete is disabled",
        "object fields do not match referenced row",
        "there is no unique or exclusion constraint matching the ON CONFLICT specification",
        'null value in column "order_id" of relation "order_items" violates not-null constraint',
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
    assert "PostgreSQL 17 and 18 are tested released majors" in readme
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
                   "actual customer network", "actual enforcing control for collection row count",
                   "without `limit`", "oversized `Range`", "statement timeout"):
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


REVIEW_DOCUMENT_MARKERS = {
    "README.md": ("## Who it is for", "## What gets created", "ddl-review-smoke:start",
                  "INSERT ignores edited non-key object", "Packagers", "must include `LICENSE`",
                  "Oracle, ORDS, PostgreSQL, PostgREST and other names belong to their owners",
                  "used only descriptively; no affiliation or endorsement is implied",
                  "notice lives in the SQL script, not in the generated database objects",
                  "PostgreSQL 17 and 18 are tested released majors"),
    "docs/PILOT_READINESS.md": (
        "Before accepting any customer material, including a schema-only dump",
        "generator environment and its owner", "customer decides anonymisation in writing",
        "treat material as personal data", "third-party AI tools or online services",
        "unless the agreement explicitly permits it", "vulnerability-reporting route",
        "pilot-gate approver/date and PG17/18 CI run URLs", "installed extension SQL SHA-256",
        "request buffering and rate limit", "signed agreement and customer-material/data-handling prerequisites are mandatory",
        "cannot waive legal requirements",
        "No item is marked complete by this repository", "At PostgreSQL 19 GA"),
    "docs/DEPLOYMENT_RECORD.md": (
        "Pilot-gate approver, date and PG17/18 CI run URLs", "Per-customer standalone bundle SHA-256",
        "Generator hash verified before each export", "Installed extension SQL SHA-256 verified before each export",
        "Generator environment and named owner", "Ingress header/body deadline, request buffering and rate limit",
        "Customer security contact and Baremon security contact", "vulnerability-reporting route"),
    "docs/cookbook/08-no-extension.md": (
        "### Managed service", "consultant runbook", "Python 3", "checkout of the pinned candidate",
        "administrative role must be allowed to `SET ROLE target_owner`",
        "Defer the provider's automatic major-version upgrades", "After a planned PostgreSQL-major upgrade",
        "Generator SHA-256", "installed extension SQL file", "Expected output: each statement reports `GRANT`",
        "complete notice with every copy of `generated.sql`", "whole bundle", "script, not in database objects"),
    "docs/cookbook/15-rls.md": (
        "Source privileges remain necessary", "visibility-relative", "Hidden children",
        "42501 row-level security prevents locking root", "grant_delete_rpc",
        "tenant_a_rpc_deleted", "tenant_b_rpc_deleted", "tenant_a_document", "tenant_b_document"),
    "docs/cookbook/10-postgrest-proxy.md": (
        "header and body timeouts", "whole-request deadline", "request buffering",
        "authenticated rate limits", "verified in CI only", "tests/run_postgrest.py",
        "tests/http_security_contract.py", "# Read: 200", "# Complete-document PATCH",
        "# Rejected upsert: 400", "# RPC delete: 200", "HTTP 428 / PT428",
        "actual enforcing control for collection row count", "without `limit`",
        "oversized `Range`", "before every REST pilot"),
    "docs/cookbook/02-map-tables.md": (
        "CREATE SCHEMA recipe_mapping", "CREATE TABLE recipe_mapping.jobs", "recipe_mapping.documents",
        "nullable child `parent_fk`", "NO ACTION", "invalid or unsupported root.fields.x"),
    "docs/cookbook/README.md": (
        "## Reading order and databases", "Separate `generator`", "SET ROLE app_role",
        "generated-ddl-outline.md", "Independent reader trial", "reader who did not author it",
        "without outside help", "passed an independent PostgreSQL 18.6 reader trial"),
    "docs/cookbook/07-write-etags.md": ("`\\gset` stores a one-row SELECT's named columns as psql variables",),
    "docs/cookbook/06-read-list-filter.md": ("JOIN api.orders_dv AS document", "WHERE status='approved'",),
    "docs/cookbook/13-limits.md": ("Set `stored_children` to the agreed pilot maximum",),
    "docs/cookbook/14-pilot-acceptance.md": ("../PILOT_READINESS.md", "../DEPLOYMENT_RECORD.md"),
    "docs/RESOURCE_LIMITS.md": (
        "One oversized root aborts an entire document-materialising collection query",
        "point read of another root", "SELECT child_count,count(*) AS roots", "monitor it against",
        "`stored_children` limit"),
    "docs/cookbook/11-schema-change.md": (
        "Dropping or changing the type of a mapped column is normally blocked by binding",
        "drop the exact generated mapping objects", "apply the relational change", "restore reviewed grants"),
    "docs/SECURITY_MODEL.md": ("Only the schema owner should hold `CREATE` on the API schema",),
    "docs/cookbook/troubleshooting.md": (
        "complete server message and SQLSTATE in the customer environment only",
        "redact DETAIL lines and values", "agreed private channel", "oversized-source-data"),
    "SUPPORT.md": (
        "Keep full server messages in the customer environment", "redact DETAIL lines and values",
        "agreed private channel", "not raw customer diagnostics"),
    "examples/standalone/README.md": (
        "complete notice with every copy of `generated.sql`", "whole bundle",
        "deployment, contractor access and backups", "script, not in generated database objects",
        "Packagers include `LICENSE`"),
    "docs/GLOSSARY.md": ("INSERT ignores edited non-key object fields", "**Binding view**",
                         "**Bundle**", "**Capability**", "**Regeneration**", "**Invoker rights**"),
    "CHANGELOG.md": ("This unreleased version", "separately signed written pilot terms", "pinned pre-release build"),
}


def review_documents_contract(documents: dict[str, str]) -> None:
    for path, markers in REVIEW_DOCUMENT_MARKERS.items():
        content = " ".join(documents[path].split())
        for marker in markers:
            assert marker in content, f"{path}: missing reviewed pilot requirement: {marker}"
    readme = documents["README.md"]
    assert readme.index("## Install") < readme.index("## Quick start") < readme.index("## Status")
    assert "supported released majors" not in readme
    assert "P0:" not in documents["docs/cookbook/README.md"]
    assert "P1:" not in documents["docs/cookbook/README.md"]


def review_documents_negatives() -> None:
    """PGJT-133/136-145: each new operational/legal marker is independently required."""
    documents = {path: (ROOT / path).read_text() for path in REVIEW_DOCUMENT_MARKERS}
    review_documents_contract(documents)
    normalised = {path: " ".join(value.split()) for path, value in documents.items()}
    for path, markers in REVIEW_DOCUMENT_MARKERS.items():
        for marker in markers:
            changed = dict(normalised)
            changed[path] = changed[path].replace(marker, "REMOVED_REQUIREMENT")
            try:
                review_documents_contract(changed)
            except AssertionError:
                continue
            raise AssertionError("removed reviewed requirement accepted: " + path + "/" + marker)
    # All Markdown documents reachable directly from README/cookbook appear in the map.
    def targets(path: Path) -> set[Path]:
        links = re.findall(r"\]\(([^)]+)\)", path.read_text())
        return {(path.parent / link.split("#", 1)[0]).resolve()
                for link in links if "://" not in link and link.split("#", 1)[0].endswith(".md")}
    mapped = targets(ROOT / "docs/README.md") | {(ROOT / "docs/README.md").resolve()}
    for path in [ROOT / "README.md", *sorted((ROOT / "docs/cookbook").glob("*.md"))]:
        for target in targets(path):
            assert target.is_file(), f"broken documentation link from {path}: {target}"
            assert target in mapped, f"documentation map omits {target.relative_to(ROOT)}"
        for script in re.findall(r"^```sh\n(.*?)^```[ \t]*$", path.read_text(), re.MULTILINE | re.DOTALL):
            result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            assert result.returncode == 0, f"invalid shell block in {path}: {result.stderr}"


DOCUMENT_STATEMENTS = {
    "docs/SEMANTICS.md": (
        "On insert, non-key object fields are ignored rather than written.",
        "with object.etag=false, non-key object fields are ignored.",
        "mapped child parent_fk must therefore be nullable; a NOT NULL foreign key produces native SQLSTATE 23502.",
    ),
    "docs/PILOT_READINESS.md": (
        "creation may succeed without it, but __check() then reports PT422.",
    ),
}


def documentation_statement_contract(documents: dict[str, str], expected_hashes: dict[str, str]) -> None:
    outline = documents["docs/cookbook/generated-ddl-outline.md"]
    recorded = re.search(r"`([0-9a-f]{64})`\s+on PostgreSQL 17,.*?"
                         r"`([0-9a-f]{64})`\s+on PostgreSQL 18 and 19", outline, re.DOTALL)
    assert recorded is not None, "outline major-specific hash inventory differs"
    actual = {"17": recorded[1], "18": recorded[2], "19": recorded[2]}
    assert actual == expected_hashes, "outline hashes differ from independently pinned DDL hashes"
    for path, statements in DOCUMENT_STATEMENTS.items():
        words = " ".join(documents[path].replace("`", "").split())
        for statement in statements:
            assert statement in words, path + ": removed behavioural statement: " + statement


def documentation_statement_negatives() -> None:
    """PGJT-146/documentation-statements: shared with the coverage verifier."""
    from documentation_smoke import ORDERS_DDL_SHA256
    paths = ["docs/cookbook/generated-ddl-outline.md", *DOCUMENT_STATEMENTS]
    documents = {path: (ROOT / path).read_text() for path in paths}
    documentation_statement_contract(documents, ORDERS_DDL_SHA256)
    for old_hash in set(ORDERS_DDL_SHA256.values()):
        changed = dict(documents)
        path = "docs/cookbook/generated-ddl-outline.md"
        changed[path] = changed[path].replace(old_hash, "0" * 64)
        try:
            documentation_statement_contract(changed, ORDERS_DDL_SHA256)
        except AssertionError:
            continue
        raise AssertionError("outline hash mutation accepted")
    normalised = {path: " ".join(text.replace("`", "").split())
                  for path, text in documents.items()}
    for path, statements in DOCUMENT_STATEMENTS.items():
        for statement in statements:
            changed = dict(normalised)
            # Keep Markdown code ticks in the outline, whose hashes are parsed as code spans.
            changed["docs/cookbook/generated-ddl-outline.md"] = documents["docs/cookbook/generated-ddl-outline.md"]
            changed[path] = changed[path].replace(statement, "REMOVED_STATEMENT")
            try:
                documentation_statement_contract(changed, ORDERS_DDL_SHA256)
            except AssertionError:
                continue
            raise AssertionError("behavioural statement mutation accepted: " + statement)


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
        generator_hash = EXPECTED_GENERATOR_SHA256[('0.2.0', 18)]
        script = bundle_preamble('0.2.0', 18, 'api.orders_dv', notice.decode(), generator_hash) + 'SELECT 1;\n\nCOMMIT;\n'
        (folder / "generated.sql").write_text(script)
        verify_bundle(folder)
        # PGJT-135: a correctly framed bundle with an altered generator hash is rejected.
        (folder / "generated.sql").write_text(script.replace(generator_hash, '0' * 64))
        rejected(lambda: verify_bundle(folder))
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
            bundle_preamble('0.2.0', 18, 'api.orders_dv', licence.decode(), generator_hash) + 'SELECT 1;\n\nCOMMIT;\n')
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
    review_documents_negatives()
    documentation_statement_negatives()
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
    print("PGJT-133-145/reviewed-pilot-documents: PASS")
    print("PGJT-146/documentation-statements: PASS")
