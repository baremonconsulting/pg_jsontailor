# Reference benchmark

Generated row triggers no longer place the complete write path inside a
PL/pgSQL exception block. Bulk root-only replacement therefore uses one
top-level transaction ID rather than one subtransaction ID per document. The
CI regression records elapsed time, time per document and XID consumption for
100- and 1,000-document transactions. Partitioned child writes retain narrow
exception blocks solely to translate PostgreSQL's missing-partition error to
the documented `PT422`; measure those workloads separately.

Any generator change invalidates the previous reference measurement. Publish
a fresh reference set from the release candidate; do not relabel an older set.

The included benchmark compares generated document views with direct relational SQL in the same PostgreSQL instance. It measures server execution time with prebuilt JSON payloads and rolls back every mutation.

## Last published reference environment

- PostgreSQL 18.6;
- Azure Linux x86-64;
- 4 vCPUs;
- 15 GiB RAM;
- JIT disabled;
- one persistent local session;
- three warm-ups and 30 measured repetitions per group.

The current release-candidate measurements used commit `a9a455ee64767d5a0af397fa37688665c046e48b`. PostgreSQL 17.11, 18.6 and 19rc1 each completed 2,640 of 2,640 measurements with PASS status across 40 groups, including native MERGE actions. The PostgreSQL 18 [reference evidence](../bench/reference/azure-a9a455e-pg18/) is the published performance reference; the matching [PostgreSQL 17](../bench/reference/azure-a9a455e-pg17/) and [PostgreSQL 19](../bench/reference/azure-a9a455e-pg19/) evidence records cross-version behaviour on the same Azure VM. All three sets pass the independent artifact verifier.

Release-candidate verification separately determines whether these measurements are transferable to a later candidate. New evidence records SHA-256 fingerprints for every file under `sql/`, `pg_jsontailor.control` and `Makefile`; the candidate must have the identical fingerprint set. This remains valid after a reviewed squash merge even when the measured feature commit is no longer an ancestor or locally reachable. Older evidence without fingerprints retains the stricter ancestor-and-byte-identity rule. If any defined product input changed, the run remains historical evidence and a fresh measurement is required; its `source_commit` must never be relabelled.

The artifact verifier validates the stored results, environment metadata and
declared product fingerprints. The original invocation, environment record and
run logs provide native execution provenance; validating a stored artifact does
not independently attest the execution host or actor.

An intermediate run at `afa643c27c70eef49b3358f60de538820801e66a`, after moving catalogue guards to statement triggers but before set-wise array validation, is retained as [optimization evidence](../bench/reference/azure-afa643c/). Compared with the completed set-wise build at [`11e6d74`](../bench/reference/azure-11e6d74/), 1,000-child median insert, root-only update, complete replacement, MERGE insert and MERGE update time fell by 69.0%, 67.7%, 58.9%, 69.1% and 59.3%. At zero and one child that optimization adds roughly 8–25% to those paths; it is intentionally targeted at larger arrays. These percentages describe the two named historical builds, not the current reference.

The earlier `04d053e9f5f06c2bee6f1f14a8afa3871d8cf3b8` run predates strict type conversion, current catalogue guards, `timestamptz` normalization and lifecycle tooling. Its [historical evidence](../bench/reference/azure-04d053e/) remains reproducible but is not semantically equivalent to the current write path.

## Set-based reconciliation result

| Items | Operation | Median | p95 | Direct SQL median |
| ---: | --- | ---: | ---: | ---: |
| 10 | complete replacement | 2.791 ms | 2.853 ms | 0.129 ms |
| 100 | complete replacement | 7.716 ms | 7.922 ms | 0.522 ms |
| 1,000 | read | 4.230 ms | 4.284 ms | 3.004 ms |
| 1,000 | insert | 35.112 ms | 35.377 ms | 9.530 ms |
| 1,000 | root-only update | 34.067 ms | 34.358 ms | 0.061 ms |
| 1,000 | complete replacement | 48.725 ms | 49.277 ms | 5.902 ms |
| 1,000 | ETag-checked delete | 12.354 ms | 12.692 ms | 2.171 ms |

## Native MERGE result

The MERGE groups execute native PostgreSQL MERGE against both the generated document view and relational targets. The document path retains validation, ETag, locking, RLS and stored-document semantics.

| Items | MERGE action | Median | p95 | Relational MERGE median |
| ---: | --- | ---: | ---: | ---: |
| 0 | INSERT | 1.206 ms | 1.241 ms | 0.086 ms |
| 0 | UPDATE | 1.394 ms | 1.426 ms | 0.055 ms |
| 0 | ETag-checked DELETE | 0.661 ms | 0.693 ms | 0.079 ms |
| 10 | INSERT | 1.788 ms | 1.844 ms | 0.296 ms |
| 10 | UPDATE | 2.756 ms | 2.791 ms | 0.137 ms |
| 10 | ETag-checked DELETE | 0.998 ms | 1.047 ms | 0.093 ms |
| 100 | INSERT | 4.952 ms | 5.087 ms | 1.119 ms |
| 100 | UPDATE | 7.486 ms | 7.635 ms | 0.522 ms |
| 100 | ETag-checked DELETE | 2.143 ms | 2.169 ms | 0.163 ms |
| 1,000 | INSERT | 31.526 ms | 31.855 ms | 6.633 ms |
| 1,000 | UPDATE | 51.020 ms | 51.485 ms | 6.111 ms |
| 1,000 | ETag-checked DELETE | 19.254 ms | 19.798 ms | 3.305 ms |

Read medians remain close to the historical build. Write medians must not be compared as if the contracts were identical: the current path adds strict JSON type and lossless numeric checks, statement-level schema guards, deterministic `timestamptz` rendering and stronger lifecycle metadata. Use the intermediate optimization run above to evaluate the set-wise validation change in isolation.

Delete results are not equivalent to the old baseline. The current path locks the root, ETag-covered referenced object and child rows, reads the document again and validates its post-lock ETag before mutation. The baseline did not provide that contract.

Direct SQL is not semantically equivalent: the document path additionally validates the complete document, checks the ETag, locks the configured state, applies RLS coverage checks and constructs the returned document.

These measurements are diagnostic results, not a production capacity claim. They exclude network latency, TLS, PostgREST, connection-pool contention, sustained WAL pressure and simultaneous writers. Define an item-count limit and p95 write SLO for each pilot, then run a representative concurrent workload. A second shared-host run showed materially higher variance, which is why the isolated Azure run is the published reference.

Run a measurement with explicit connection, workload and output settings:

```sh
python3 bench/duality_overhead.py \
  --pg-bin /usr/lib/postgresql/18/bin \
  --pg-host /var/run/postgresql \
  --pg-port 5432 \
  --pg-user "$(id -un)" \
  --database jsontailor_duality_benchmark \
  --sizes 0,1,10,100,1000 \
  --warmups 3 \
  --repetitions 30 \
  --output results/duality-benchmark-reference \
  --drop-database-after
```

When the source was copied without its `.git` directory, add `--source-commit <40-character-commit>`; otherwise the script refuses to run without reliable provenance. In a Git checkout the commit is detected automatically, and a conflicting explicit value is rejected.

The PostgreSQL role must be able to create and drop the dedicated benchmark database. With peer authentication, pass the operating-system account returned by `id -un`; otherwise pass the configured benchmark role. The script rejects maintenance database names, drops and recreates the target database before measurement, and leaves it available for inspection unless `--drop-database-after` is supplied.

The output path may be absent or may be an existing directory that does not already contain benchmark artifacts. This permits a wrapper to place its log in the directory before starting the measurement while still preventing accidental replacement of earlier evidence. Preserve `measurements.csv`, `plans.json`, `summary.json` and `environment.json` together. The environment file records the exact source commit, dirty-tree state, connection settings and workload configuration.

Validate any completed run independently:

```sh
python3 bench/verify_results.py results/duality-benchmark-reference
```

Comparisons between implementations also require the earlier commit and its raw output; summary ratios alone are not a reproducible baseline. Run `python3 tools/verify_repository_consistency.py` before publishing changed reference data or release metadata.
