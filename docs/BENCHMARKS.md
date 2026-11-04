# Reference benchmark

The included benchmark compares generated document views with direct relational SQL in the same PostgreSQL instance. It measures server execution time with prebuilt JSON payloads and rolls back every mutation.

## Reference environment

- PostgreSQL 18.6;
- Linux x86-64;
- 3 vCPUs, AMD EPYC 7542;
- 3.8 GiB RAM;
- JIT disabled;
- one persistent local session;
- three warm-ups and 30 measured repetitions per group.

## Set-based reconciliation result

| Items | Operation | Earlier median | 0.2.0 median | Improvement |
| ---: | --- | ---: | ---: | ---: |
| 10 | complete replacement | 28.3 ms | 5.6 ms | 5.0× |
| 100 | complete replacement | 223.3 ms | 14.0 ms | 15.9× |
| 1,000 | complete replacement | 3,486.0 ms | 101.9 ms | 34.2× |
| 1,000 | insert | 200.4 ms | 50.7 ms | 4.0× |

For a one-child document, representative absolute medians were 0.129 ms for a read, 1.381 ms for an insert, 3.454 ms for a root-only update, 6.313 ms for a child replacement and 3.681 ms for a delete.

Direct SQL is not semantically equivalent: the document path additionally validates the complete document, checks the ETag, locks the configured state, applies RLS coverage checks and constructs the returned document.

These measurements are diagnostic results, not a production capacity claim. They exclude network latency, TLS, PostgREST, connection-pool contention, WAL pressure and simultaneous writers. Define an item-count limit and p95 write SLO for each pilot, then run a representative concurrent workload.

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
