# Testing

The repository verification layers include:

- `make installcheck` for PGXS regression and isolation tests;
- core, validation, integrity, MERGE, security, partition and runtime-limit
  Python suites under `tests/`;
- `tests/run_postgrest.py` for the live REST contract;
- dump/restore, executable documentation, public-contract and standalone
  no-extension suites;
- `pilot/synthetic/run_pilot.py` for an isolated synthetic rehearsal;
- `bench/duality_overhead.py` and `bench/verify_results.py` for measured and
  independently recomputed benchmark evidence.

Extended tools create disposable schemas or databases. Review connection
settings before running them outside an isolated development environment.

Every push to `main` and pull request to `main` runs installation, regression,
isolation, functional, security, reconciliation, MERGE and restore suites on
PostgreSQL 17, 18 and the pinned PostgreSQL 19 release candidate. PostgREST
16.5 runs on PostgreSQL 18 per pull request and weekly on all three majors.
Repository checks enforce the candidate version, current-only benchmark
protocol and public-source contract. CodeQL and dependency review use
read-only default permissions. Pilot and final exact-SHA benchmark execution
remain explicit integration or measurement scenarios.
