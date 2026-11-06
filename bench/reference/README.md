# Published reference evidence

This directory contains the raw evidence behind [`docs/BENCHMARKS.md`](../../docs/BENCHMARKS.md).

- `azure-039a555-pg17/`, `azure-039a555-pg18/` and `azure-039a555-pg19/` are the complete cross-version release-candidate measurements from commit `039a555b55f89b849c98ab918ba279889fbf38bf`. PostgreSQL 18 is the published performance reference; PostgreSQL 17 and 19 are compatibility evidence from the same VM and workload.
- `azure-e5b41a5-pg17/`, `azure-e5b41a5-pg18/` and `azure-e5b41a5-pg19/` are the preceding complete cross-version release-candidate measurements.
- `azure-a9a455e-pg17/`, `azure-a9a455e-pg18/` and `azure-a9a455e-pg19/` are the preceding cross-version release-candidate measurements.
- `azure-11e6d74/` is the preceding PostgreSQL 18 reference after set-wise validation was implemented.
- `azure-afa643c/` is the intermediate PostgreSQL 18 optimization run before set-wise validation.
- `azure-04d053e/` was measured from commit `04d053e9f5f06c2bee6f1f14a8afa3871d8cf3b8` and includes the native MERGE groups.
- `azure-acecaae/` was measured from commit `acecaae3b62ab7e4ff833cc5e5ec2bd6eef4dc0d` as the earlier implementation baseline.

Each directory preserves the environment, every measured execution, captured plans and the derived summary. The current three-version set used PostgreSQL 17.11, 18.6 and 19rc1 in separate clusters on the same Azure VM; exact settings are recorded in each `environment.json`.

The PostgreSQL 18 release reference also contains `fingerprint-v2.json`. This is
a derived, reviewable migration from the preserved schema-v1 hashes in
`environment.json`; it records the original hashes, original environment Git
blob, source commit, policy implementation and schema-v2 hashes. Measurements
and their original environment metadata are not relabelled. A change to the
schema-v2 fingerprint meaning requires a new schema number and migration record.

The benchmark drops and recreates its target database before measurement. It leaves the database available for inspection by default and drops it after artifact validation when `--drop-database-after` is supplied. Reproduce it only with a dedicated disposable database name.
