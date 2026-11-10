# Pilot readiness

This checklist defines the minimum evidence for a controlled `pg_jsontailor`
pilot. It is not a production certification.

Before accepting any customer material, including a schema-only dump, execute
the separate written pilot agreement. Do not send customer-facing bundles,
documentation or access until the named owner records the pre-send gate below.
Synthetic internal rehearsals do not waive these conditions.

The agreement names the generator environment and its owner; use the customer's
environment by default and do not transfer material to a consultant environment
without written permission. The customer decides anonymisation in writing;
unless that decision and its effective controls are recorded, treat material as
personal data. Do not submit customer material to third-party AI tools or online
services unless the agreement explicitly permits it.

## 1. Fit assessment

Use synthetic definitions by default. If a schema-only dump or representative
sample is required, obtain written customer approval and keep it within the
approved environment. Confirm for every proposed document:

- one root, at most one referenced object and at most one child array;
- single-column primary and foreign keys;
- mapped types appear in the supported matrix in [`API.md`](API.md);
- no mapped domains, enums, JSON types, SQL arrays or classic inheritance;
- required object and child foreign keys are validated, enforced and
  non-deferrable, and the child foreign key uses `NO ACTION` or `RESTRICT`;
- all unmapped `NOT NULL` columns have usable defaults or are otherwise
  populated by the database;
- mapped names, operation flags, object ETag inclusion and array ordering are
  explicit in a source-controlled specification.
- every mapped child collection has an index beginning with
  `(parent_fk, order_by, key)`; creation may succeed without it, but
  `__check()` then reports PT422.

Generate DDL in a disposable database restored from the schema dump. Run the
generated `__check()` function and inspect `duality_view_inventory()`. Exercise
INSERT, complete replacement, ETag-checked DELETE and each intended MERGE
action using synthetic rows. If representative values are needed to validate
conversion semantics, receive them only under the written customer agreement
and the customer's written data decision. Do not assume a sample is
irreversibly anonymised: unless the customer confirms that treatment in writing, handle it
as personal data. A schema dump alone cannot demonstrate application data quality.
Record the approved fields, environment, recipients, retention deadline and
deletion owner before receiving the sample.

## 2. Pilot scope record

Execute a separate written pilot agreement with the customer before
implementation and attach or reference its agreed Pilot scope record. Record:

- exact schemas, mappings, operations and PostgreSQL versions in scope;
- excluded features and unsupported source shapes;
- maximum child count and payload size;
- target p50 and p95 latency, throughput and concurrency;
- RLS, roles, PostgREST and network boundary;
- acceptance dataset and rollback owner;
- named pilot contact and backup, private support channel, response expectations and pilot end date;
- vulnerability-reporting route, including the verified private security-reporting link;
- permitted diagnostic data, storage location, access list, retention deadline
  and verified deletion owner.

The relational tables remain the source of truth. The exit plan is to stop
traffic to generated views and retain the underlying tables; no proprietary
storage conversion is required.

## 3. Acceptance evidence

Use [`pilot/synthetic/ACCEPTANCE_PLAN.md`](../pilot/synthetic/ACCEPTANCE_PLAN.md)
as the base and add customer-specific cases. At minimum include:

- reads and ETag stability;
- allowed and forbidden inserts, replacements and deletes;
- stale and missing ETags;
- RLS-visible and RLS-hidden root, object and child rows;
- two concurrent writers changing the same document;
- concurrent writes through different documents that share an ETag-covered
  referenced object;
- dump/restore and post-restore health checks;
- grants and application-role execution through the intended API path;
- item-count limits and the agreed p95 SLO under representative concurrency.

## 4. Deployment and release gate

With the extension installed, save/hash/approve exact generated DDL and call
`create_duality_view()` in a stop-on-error transaction. It executes immediately,
returns void and displays no DDL. No-extension managed installations apply only
the reviewed, verified bundle from `tools/export_standalone.py`; raw
`duality_view_ddl` output is inspection-only in both paths.
Generation and regeneration occur in a controlled environment with the same
schema and PostgreSQL major, followed by catalogue and application smoke tests.
Retain the reviewed SQL bytes and the separate observed deployed-object manifest
from cookbook recipes 03–04. Regenerated desired-state SQL is not proof of the
actual deployed state. Validate PostgreSQL 17, 18 and the pinned 19 build; record
its pre-release qualification separately from GA support.

Provide a controlled pilot only from a pinned full commit and retained bundle
hash after the functional and security pilot gate. A public release still
requires an immutable tag, archived source, release notes and checksums. Do not
label historical benchmark numbers as current.
Final benchmark evidence requires the exact immutable candidate SHA on all
three PostgreSQL majors. Signed installation records bind installed control/SQL,
catalogue functions and representative generated DDL to that candidate. The
reviewer supplies the trusted signing key independently of the result bundle.
Product-equivalence fingerprints do not substitute for required exact-SHA runs.
See [the current-only benchmark protocol](BENCHMARKS.md). Final runs remain
pending the merged security/runtime freeze; no current performance numbers are
published. Preserve the actual source SHA and original logs without relabelling.

Before a pilot, complete the [deployment record](DEPLOYMENT_RECORD.md), confirm
private security reporting, named operational ownership, backups, monitoring,
RTO/RPO and a tested rollback window. A named support contact and pilot terms
are owner actions outside this repository.

The record must include pilot-gate approver/date and PG17/18 CI run URLs,
candidate archive and per-customer bundle SHA-256, generator hash and installed
extension SQL SHA-256 verified before export, plus ingress header/body deadline,
request buffering and rate limit. Rehearse recipe 8 on the same managed-service
type before the customer session; this is not provider qualification.

## 5. Pre-send owner gate (external evidence required)

Record each item as DONE with date, named owner and private evidence reference
in PGJT-132. The signed agreement and customer-material/data-handling
prerequisites are mandatory and cannot be waived. Any permitted waiver of
another operational item must identify its scope, rationale, approving owner
and compensating controls; it cannot waive legal requirements. Blank, pending
or synthetic test output is not approval. No item is marked complete by this
repository:

- signed agreement: purpose/scope, confidentiality, data-protection roles,
  licence-consistent as-is terms, lawful liability limits, feedback licence,
  duration/end, confirmed deletion and written-consent-only public reference;
  qualified legal review or written owner acceptance of the unreviewed-template risk;
- contact, backup, private channel, response expectations and personal-data decision;
- verify private vulnerability reporting is enabled and its link works;
- record the CRA position for time-limited pre-release testing;
- record D1/D5 in PGJT-120: pilot functional/security gate versus publication
  tag/benchmark gate, tested released 17/18 and pre-release 19 until GA, exact
  source SHA, CI URLs, approver/date and where review actually occurred;
- archive/bundle/generator hashes and managed-service internal rehearsal;
- consultant-led first session, one customer first; next only after success;
- capacity and pilot economics explicitly reviewed or dispositioned by their owners.

At PostgreSQL 19 GA, review and update version wording and the pinned CI build;
passing a pre-release test is not automatic GA qualification.
