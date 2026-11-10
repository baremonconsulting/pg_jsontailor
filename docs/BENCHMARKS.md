# Current-candidate benchmark protocol

Final exact-SHA measurements on PostgreSQL 17, 18 and 19 are pending the merged
security/runtime freeze. There are no current performance numbers to publish.
Fixtures prove verification algorithms, not installation, host provenance or
performance. A later commit invalidates exact-SHA evidence. Do not relabel an
older run or substitute product-equivalence fingerprints for the required SHA.

The comparison is the current candidate against **lower-contract direct SQL**,
not another implementation revision. Direct SQL does not provide complete
document validation, ETag checks, the generated locking/RLS contract or returned
document construction. The object in this workload has `etag=false`; checked
objects, partitioned children and deferrable/default-preserving fallback paths
need separate qualification. Results are diagnostic, not a customer SLO.

## Installation trust

Use a private, disposable PostgreSQL installation and cluster for each major.
Never install into a shared prefix. The operator supplies an Ed25519 signing key
and signer identity; the reviewer/release owner supplies the trusted public key
independently of the result bundle. Protect the private key and retain the trust
root separately in the protected evidence store. A self-supplied bundle key is
not approval. Signing vouches for the recorded native execution; a digest alone
does not prove a host or actor.

`bench/attest_installation.py` requires a clean Git checkout at the immutable
candidate. It records commands, timestamps, durations, exit codes and logs,
installs that candidate, and checks every installed control/SQL file against
the exact Git blobs. Two fresh databases compare the installed catalogue
functions and representative root-only/object-and-array generated DDL against
an oracle loaded directly from the candidate SQL, not from the installed
extension. Both temporary databases are dropped. The canonical, versioned
installation record is Ed25519-signed and binds the full installation log.

```sh
python3 bench/attest_installation.py \
  --pg-config /disposable/pg18/bin/pg_config \
  --disposable-prefix /disposable/pg18 \
  --private-key /protected/benchmark-signing.pem \
  --trusted-key /approved/benchmark-public.pem \
  --signer 'Named measurement operator' \
  --output /evidence/installation-pg18
```

Set `PGHOST`, `PGPORT` and `PGUSER` for the private cluster before running this
command. The benchmark independently rechecks the signed record, installed
files, live catalogue and generated DDL before measuring. The artifact verifier
and release-consumer API each authenticate installation independently, using
the expected exact candidate and the externally trusted key. Stale installation,
altered SQL/catalogue/DDL, a wrong candidate, signature or log digest fails closed.

## Reviewed workload and semantics

The workload is exactly sizes `0,1,10,100,1000` × operations
`read,insert,root_update,update,delete,merge_insert,merge_update,merge_delete` ×
implementations `direct,duality`. Removing a size or operation is invalid even
when all remaining artifacts agree. Statement text/count is checked against the
reviewed workload, not the producer's declared inventory.

Each constituent checks operation preconditions and child cardinality, relational
and document state after execution, the chosen MERGE branch and affected
cardinality, and complete rollback restoration. These assertions run outside
the timed statements. Each mutation is rolled back. Every constituent retains its
`EXPLAIN (ANALYZE, TIMING OFF, BUFFERS, WAL, FORMAT JSON)` plans. The independent
verifier checks real plan roots/nodes, statement inventory, integer block/WAL
counters, planning and execution reconciliation, calibration selection,
constituent chronology and batch totals. Semantic snapshots are content-addressed
to avoid duplicating large documents.

The nightly six-mutation campaign requires successful compilation/application
and passing control suites. A kill requires the named behavioural witness or
exact control SQLSTATE and verified mutant read-back. An unrelated suite failure,
SQLSTATE or infrastructure error is a harness failure, not a kill. The separate
stale-return witness remains separately required.

## Sampling and statistics

Each of three sessions starts a new persistent PostgreSQL backend, recording its
PID and start time. Before warm-up, each group calibrates one shared batch size
for both implementations. It tries the reviewed powers of two from 1 to 32 in
order and retains three complete pilot batches per implementation at every
visited level. The first level where both median batch totals reach 1 ms is
frozen. Missing levels, unequal arms, invalid timings or cap exhaustion fail.
Calibration is never repeated in response to a failed stability gate.

Every constituent is a complete independent transaction: precondition snapshot,
the exact reviewed `EXPLAIN ANALYZE` workload, resulting-state snapshot, rollback
and restoration snapshot. Direct and duality constituents alternate within each
batch. Plans, semantic witnesses and integer counters are retained per
constituent; batching never repeats mutations against progressively changed state.

Each group starts with ten paired warm-up batches. After ten, compare the median
aggregate execution time of the last five with the preceding five,
independently for each implementation. Both must differ by at most 10%. If
either fails, add five batches and check again, up to 30; continued instability
is FAIL. The threshold is unchanged, but the estimand is explicitly batch
aggregate stability rather than individual-operation stability.

Each accepted session/group then records at least 50 measured batches. No
constituent is trimmed. Report median, minimum, maximum and nearest-rank p95
(`sorted[ceil(0.95*N)-1]`) separately for raw individual operations and for
per-operation batch means. A p95 of batch means is always labelled as such and
is never presented as individual tail latency. The comparison ratio uses batch
mean medians. Diagnose variability; do not turn it into a production promise.

```sh
python3 bench/duality_overhead.py \
  --pg-bin /disposable/pg18/bin --pg-host /disposable/socket \
  --pg-port 55433 --pg-user benchmark \
  --database jsontailor_disposable_benchmark \
  --installation /evidence/installation-pg18/installation.json \
  --trusted-key /approved/benchmark-public.pem \
  --measurement-key /protected/benchmark-signing.pem \
  --warmups 10 --repetitions 50 \
  --output /evidence/current-pg18 --drop-database-after
```

Warning: the selected database is dropped and recreated. Only use a dedicated
disposable database. Output artifacts are never overwritten. For an installation
and semantic smoke check before freeze, add `--semantic-check-only`; this emits
no publishable timing artifacts and cannot pass the full benchmark verifier.

## Evidence and release consumption

Retain `measurements.csv`, `plans.json`, `semantics.json`, `summary.json`,
`environment.json`, `installation.json`, `installation.log`, `manifest.json`
and `measurement.json`
together. The SHA-256 manifest has an exact artifact inventory. Environment
metadata records the exact source SHA/clean state, invocation, timestamps,
PostgreSQL version (including prerelease suffix), effective server settings,
CPU, memory, storage, host load, Python, connection settings and session identities.

The measurement operator signs the completed manifest with the protected
Ed25519 key identified by the independently supplied `--trusted-key`.
`measurement.json` binds the complete manifest, installation attestation,
candidate SHA, run identity/timestamps and producer/workload/verifier hashes.
It is outside the manifest to avoid a circular digest. A signature proves
origin and integrity under the approved operator key, not that a dishonest
operator measured truthfully; independent review and key custody remain
mandatory. An installation signature alone is not measurement evidence.
Missing, stale or changed completed-measurement signatures fail verification.

The fixture creates the required `(order_id,item_id)` child index and must
pass its generated `__check()` before any samples are collected. A timing
run on an unhealthy fixture is not qualified evidence.

```sh
python3 bench/verify_results.py /evidence/current-pg18 \
  --candidate FULL_IMMUTABLE_SHA --trusted-key /approved/benchmark-public.pem
python3 tools/verify_release_candidate.py \
  --benchmark-evidence /evidence/current-pg17 \
  --benchmark-evidence /evidence/current-pg18 \
  --benchmark-evidence /evidence/current-pg19 \
  --benchmark-key /approved/benchmark-public.pem
```

Without the three-major matrix, repository candidate checks explicitly report
the final benchmark as PENDING; that is not release approval. The release gate
must invoke `verify_benchmark_matrix` with the frozen SHA and protected trust
root. Retain approved records under the release evidence store for that SHA,
with installation records and logs in each major's benchmark directory. Keep
approvals/trust roots independent of the producer. Candidate validation never
satisfies protected-main release authority or authorises publication.
