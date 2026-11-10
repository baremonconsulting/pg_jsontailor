# Pilot readiness

This checklist defines the minimum evidence for a controlled `pg_jsontailor`
pilot. It is not a production certification.

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
conversion semantics, use only an irreversibly anonymised sample covered by a
written customer agreement; a schema dump alone cannot demonstrate application
data quality. Record the approved fields, environment, recipients, retention
deadline and deletion owner before receiving the sample.

## 2. Pilot scope record

Execute a separate written pilot agreement with the customer before
implementation and attach or reference its agreed Pilot scope record. Record:

- exact schemas, mappings, operations and PostgreSQL versions in scope;
- excluded features and unsupported source shapes;
- maximum child count and payload size;
- target p50 and p95 latency, throughput and concurrency;
- RLS, roles, PostgREST and network boundary;
- acceptance dataset and rollback owner;
- private support channel, response expectations and pilot end date;
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

Managed PostgreSQL installations apply only the reviewed, verified bundle from
`tools/export_standalone.py`; raw `duality_view_ddl` output is inspection-only.
Generation and regeneration occur in a controlled environment with the same
schema and PostgreSQL major, followed by catalogue and application smoke tests.

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
