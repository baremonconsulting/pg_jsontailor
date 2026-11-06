# Pilot readiness

This checklist defines the minimum evidence for a controlled `pg_jsontailor`
pilot. It is not a production certification.

## 1. Fit assessment

Obtain a schema-only dump from the candidate database and keep it within the
customer-approved environment. Confirm for every proposed document:

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

Generate DDL in a disposable database restored from the schema dump. Run the
generated `__check()` function and inspect `duality_view_inventory()`. Exercise
INSERT, complete replacement, ETag-checked DELETE and each intended MERGE
action using synthetic rows. If representative values are needed to validate
conversion semantics, use an anonymized, customer-approved sample; a schema
dump alone cannot demonstrate application data quality.

## 2. Pilot contract

Record before implementation:

- exact schemas, mappings, operations and PostgreSQL versions in scope;
- excluded features and unsupported source shapes;
- maximum child count and payload size;
- target p50 and p95 latency, throughput and concurrency;
- RLS, roles, PostgREST and network boundary;
- acceptance dataset and rollback owner;
- support channel, response expectations and pilot end date.

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

Managed PostgreSQL installations may execute reviewed output from
`duality_view_ddl` without a runtime extension dependency. Generation and
regeneration must occur in a controlled environment with the same schema and
PostgreSQL major version, followed by catalogue and application smoke tests.

Provide a pilot only from an immutable release tag. Archive the source, release
notes and checksums. Do not label historical benchmark numbers as current.
Fingerprint-bearing measurements may support a tagged release when the
release-candidate verifier confirms that every defined product input under
`sql/`, `pg_jsontailor.control` and `Makefile` is byte-identical to the measured
inputs. A reviewed product-equivalent squash therefore remains eligible even
when the measured feature commit is no longer an ancestor or locally reachable.
Legacy evidence without product fingerprints retains the stricter requirement
that its recorded source commit exists, is an ancestor of the tag and contains
byte-identical product inputs. Any product-input change requires fresh evidence.

Preserve the actual measurement `source_commit`; never relabel an older run as
if it were measured from the tag. Artifact and release-candidate verification
checks the stored results, metadata and declared product fingerprints. Native
execution provenance comes from the original benchmark invocation, environment
record and run logs; verification of the stored artifact does not independently
attest where or by whom the measurements executed.

Before go-live, confirm private security reporting through Baremon, named
operational ownership, backups, monitoring and a tested rollback window.
