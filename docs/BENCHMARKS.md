# Reference benchmark

The included benchmark compares generated document views with direct relational SQL in the same PostgreSQL instance. It measures server execution time with prebuilt JSON payloads and rolls back every mutation.

## Last published reference environment

- PostgreSQL 18.6;
- Azure Linux x86-64;
- 4 vCPUs;
- 15 GiB RAM;
- JIT disabled;
- one persistent local session;
- three warm-ups and 30 measured repetitions per group.

The current release-candidate measurements used commit `06d0f000a21d2e08aa14985e988800c325b33060`. PostgreSQL 17.11, 18.6 and 19rc1 each completed 2,640 of 2,640 measurements with PASS status across 40 groups, including native MERGE actions. The PostgreSQL 18 [reference evidence](../bench/reference/azure-06d0f00-pg18/) is the published performance reference; the matching [PostgreSQL 17](../bench/reference/azure-06d0f00-pg17/) and [PostgreSQL 19](../bench/reference/azure-06d0f00-pg19/) evidence records cross-version behaviour on the same Azure VM. All three sets pass the independent artifact verifier.

Release-candidate verification separately determines whether these measurements are transferable to a later candidate. The recorded commit must be an ancestor and `sql/`, `pg_jsontailor.control` and `Makefile` must be byte-identical through the candidate. If any defined product input changed, this run remains historical evidence and a fresh measurement is required; its `source_commit` must never be relabelled.

An intermediate run at `afa643c27c70eef49b3358f60de538820801e66a`, after moving catalogue guards to statement triggers but before set-wise array validation, is retained as [optimization evidence](../bench/reference/azure-afa643c/). For 1,000 child items the final set-wise validation reduced median insert, root-only update, complete replacement, MERGE insert and MERGE update time by 69.0%, 67.7%, 58.9%, 69.1% and 59.3% respectively relative to that intermediate build. At zero and one child it adds roughly 8–25% to those paths; the optimization is intentionally targeted at larger arrays.

The earlier `04d053e9f5f06c2bee6f1f14a8afa3871d8cf3b8` run predates strict type conversion, current catalogue guards, `timestamptz` normalization and lifecycle tooling. Its [historical evidence](../bench/reference/azure-04d053e/) remains reproducible but is not semantically equivalent to the current write path.

## Set-based reconciliation result

| Items | Operation | Median | p95 | Direct SQL median |
| ---: | --- | ---: | ---: | ---: |
| 10 | complete replacement | 2.783 ms | 2.852 ms | 0.131 ms |
| 100 | complete replacement | 7.514 ms | 7.683 ms | 0.527 ms |
| 1,000 | read | 4.608 ms | 4.640 ms | 2.984 ms |
| 1,000 | insert | 35.319 ms | 35.928 ms | 9.514 ms |
| 1,000 | root-only update | 34.564 ms | 34.859 ms | 0.061 ms |
| 1,000 | complete replacement | 49.511 ms | 49.980 ms | 5.944 ms |
| 1,000 | ETag-checked delete | 13.070 ms | 13.869 ms | 2.186 ms |

## Native MERGE result

The MERGE groups execute native PostgreSQL MERGE against both the generated document view and relational targets. The document path retains validation, ETag, locking, RLS and stored-document semantics.

| Items | MERGE action | Median | p95 | Relational MERGE median |
| ---: | --- | ---: | ---: | ---: |
| 0 | INSERT | 1.292 ms | 1.342 ms | 0.079 ms |
| 0 | UPDATE | 1.440 ms | 1.534 ms | 0.057 ms |
| 0 | ETag-checked DELETE | 0.690 ms | 0.718 ms | 0.081 ms |
| 10 | INSERT | 1.821 ms | 1.875 ms | 0.313 ms |
| 10 | UPDATE | 2.796 ms | 2.889 ms | 0.140 ms |
| 10 | ETag-checked DELETE | 0.906 ms | 0.936 ms | 0.091 ms |
| 100 | INSERT | 4.869 ms | 5.054 ms | 1.133 ms |
| 100 | UPDATE | 7.544 ms | 7.664 ms | 0.531 ms |
| 100 | ETag-checked DELETE | 2.089 ms | 2.124 ms | 0.163 ms |
| 1,000 | INSERT | 32.137 ms | 32.584 ms | 6.643 ms |
| 1,000 | UPDATE | 51.707 ms | 52.541 ms | 6.173 ms |
| 1,000 | ETag-checked DELETE | 20.105 ms | 20.486 ms | 3.793 ms |

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
