# Published reference evidence

This directory contains the raw evidence behind [`docs/BENCHMARKS.md`](../../docs/BENCHMARKS.md).

- `azure-04d053e/` was measured from commit `04d053e9f5f06c2bee6f1f14a8afa3871d8cf3b8` and includes the native MERGE groups.
- `azure-acecaae/` was measured from commit `acecaae3b62ab7e4ff833cc5e5ec2bd6eef4dc0d` as the earlier implementation baseline.

Each directory preserves the environment, every measured execution, captured plans and the derived summary. Both runs used PostgreSQL 18.6 on the same Azure VM, with separate database clusters and the settings recorded in `environment.json`.

The benchmark creates and drops its target database. Reproduce it only with a dedicated disposable database name.
