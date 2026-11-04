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

The current reference run used commit `e81a432399f924056e5a07e543541c45bfc5c072`. It completed 1,650 of 1,650 measurements with PASS status. A baseline run of `acecaae3b62ab7e4ff833cc5e5ec2bd6eef4dc0d` used the same VM and a separate PostgreSQL 18.6 cluster.

## Set-based reconciliation result

| Items | Operation | Median | p95 | Direct SQL median |
| ---: | --- | ---: | ---: | ---: |
| 10 | complete replacement | 1.922 ms | 1.963 ms | 0.121 ms |
| 100 | complete replacement | 5.626 ms | 5.725 ms | 0.520 ms |
| 1,000 | read | 4.228 ms | 4.379 ms | 2.913 ms |
| 1,000 | insert | 22.934 ms | 23.182 ms | 5.989 ms |
| 1,000 | root-only update | 25.174 ms | 25.293 ms | 0.051 ms |
| 1,000 | complete replacement | 39.452 ms | 39.651 ms | 5.877 ms |
| 1,000 | ETag-checked delete | 11.168 ms | 11.229 ms | 1.974 ms |

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
