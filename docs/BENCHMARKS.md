# Reference benchmark

The included benchmark compares generated document views with direct relational SQL in the same PostgreSQL instance. It measures server execution time with prebuilt JSON payloads and rolls back every mutation.

## Current reference environment

- PostgreSQL 18.6;
- Azure Linux x86-64;
- 4 vCPUs;
- 15 GiB RAM;
- JIT disabled;
- one persistent local session;
- three warm-ups and 30 measured repetitions per group.

The current reference run used commit `04d053e9f5f06c2bee6f1f14a8afa3871d8cf3b8`. It completed 2,640 of 2,640 measurements with PASS status across 40 groups, including native MERGE actions. A baseline run of `acecaae3b62ab7e4ff833cc5e5ec2bd6eef4dc0d` used the same VM and a separate PostgreSQL 18.6 cluster.

## Set-based reconciliation result

| Items | Operation | Median | p95 | Direct SQL median |
| ---: | --- | ---: | ---: | ---: |
| 10 | complete replacement | 1.913 ms | 1.930 ms | 0.128 ms |
| 100 | complete replacement | 5.589 ms | 5.656 ms | 0.519 ms |
| 1,000 | read | 4.205 ms | 4.224 ms | 2.874 ms |
| 1,000 | insert | 26.433 ms | 26.769 ms | 9.419 ms |
| 1,000 | root-only update | 25.180 ms | 25.314 ms | 0.054 ms |
| 1,000 | complete replacement | 39.613 ms | 39.773 ms | 5.866 ms |
| 1,000 | ETag-checked delete | 11.323 ms | 11.475 ms | 2.065 ms |

## Native MERGE result

The MERGE groups execute native PostgreSQL MERGE against both the generated document view and relational targets. The document path retains validation, ETag, locking, RLS and stored-document semantics.

| Items | MERGE action | Median | p95 | Relational MERGE median |
| ---: | --- | ---: | ---: | ---: |
| 0 | INSERT | 0.481 ms | 0.519 ms | 0.074 ms |
| 0 | UPDATE | 0.729 ms | 0.748 ms | 0.055 ms |
| 0 | ETag-checked DELETE | 0.488 ms | 0.526 ms | 0.084 ms |
| 10 | INSERT | 0.918 ms | 0.956 ms | 0.298 ms |
| 10 | UPDATE | 1.908 ms | 1.938 ms | 0.136 ms |
| 10 | ETag-checked DELETE | 0.734 ms | 0.764 ms | 0.100 ms |
| 100 | INSERT | 3.281 ms | 3.368 ms | 1.119 ms |
| 100 | UPDATE | 5.598 ms | 5.645 ms | 0.519 ms |
| 100 | ETag-checked DELETE | 1.885 ms | 1.911 ms | 0.166 ms |
| 1,000 | INSERT | 22.712 ms | 22.979 ms | 6.515 ms |
| 1,000 | UPDATE | 41.855 ms | 42.133 ms | 6.104 ms |
| 1,000 | ETag-checked DELETE | 17.215 ms | 17.367 ms | 2.778 ms |

Against `acecaae` on the same Azure VM, complete replacement improved by 7.6% at zero children, 30.9% at one, 26.2% at ten, 14.3% at 100 and 7.3% at 1,000. Insert medians increased by 18.1–38.7% depending on document size; this is the main path selected for further profiling. Read medians remained within approximately ±5%.

Delete results are not equivalent to the old baseline. The current path locks the root, ETag-covered referenced object and child rows, reads the document again and validates its post-lock ETag before mutation. The baseline did not provide that contract.

Direct SQL is not semantically equivalent: the document path additionally validates the complete document, checks the ETag, locks the configured state, applies RLS coverage checks and constructs the returned document.

These measurements are diagnostic results, not a production capacity claim. They exclude network latency, TLS, PostgREST, connection-pool contention, sustained WAL pressure and simultaneous writers. Define an item-count limit and p95 write SLO for each pilot, then run a representative concurrent workload. A second shared-host run showed materially higher variance, which is why the isolated Azure run is the published reference.

Run a measurement with explicit connection, workload and output settings:

```sh
python3 bench/duality_overhead.py \
  --pg-bin /usr/lib/postgresql/18/bin \
  --pg-host /var/run/postgresql \
  --pg-port 5432 \
  --pg-user benchmark_user \
  --database jsontailor_duality_benchmark \
  --sizes 0,1,10,100,1000 \
  --warmups 3 \
  --repetitions 30 \
  --output results/duality-benchmark-reference
```

The target database is dropped and recreated, so use a dedicated disposable name. Preserve `measurements.csv`, `plans.json`, `summary.json` and `environment.json` together with the exact source commit when publishing results. Comparisons between implementations also require the earlier commit and its raw output; summary ratios alone are not a reproducible baseline.
