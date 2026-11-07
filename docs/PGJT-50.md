# Generated SQL property and mutation testing

The scheduled PostgreSQL 18 job runs a bounded, reproducible test campaign outside
the pull-request gate. `property_generated_sql.py` uses seed `50050` and 24 cases.
Its JSON Lines evidence records the seed, case
count and a digest of generated case identifiers.

The properties cover accepted and rejected specifications, quoted source
identifiers, generated JSON documents, and identifier size boundaries. The
mutation harness generates SQL from the shipped example specification, first
accepts the unmodified control, and then requires representative mutations of
ETag checks, locking, RLS error paths, type checks, binding guards and atomic
reconciliation to be rejected by their semantic invariant.

The daily bounded campaign runs on PostgreSQL 18 and publishes the
`generated-sql-pg18` artifact. The normal PostgreSQL 17/18/19 matrix remains
independent and retains its weekly schedule. The nightly artifact contains the
property and mutation JSON Lines files plus the PostgreSQL log.
Any surviving mutant, rejected control, failed property or missing artifact fails
the scheduled workflow.

Local execution uses an installed extension and a disposable database:

```sh
python3 tests/property_generated_sql.py --database jsontailor_test \
  --seed 50050 --cases 24 --output /tmp/property.jsonl
python3 tests/mutate_generated_sql.py --database jsontailor_test \
  --repo . \
  --output /tmp/mutation.jsonl
python3 tools/verify_jsonl_results.py /tmp/property.jsonl /tmp/mutation.jsonl
```
