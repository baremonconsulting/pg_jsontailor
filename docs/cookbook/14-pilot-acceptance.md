# 14. Pilot acceptance

## Goal

Produce a reproducible technical rehearsal and explicit customer GO record.

## Prerequisites

Clean pinned candidate, matching installed files, PG17/18/pinned-PG19 matrix,
PostgREST 16.5 and separate disposable databases. Set PG_BIN, POSTGREST, PGHOST,
PGPORT and PGUSER to the reviewed environment. No customer data is required.

## Steps

Run from repository root, once for each configured PostgreSQL build:

```sh
set -euo pipefail
umask 077
test -z "$(git status --porcelain)"
pilot_evidence="$(mktemp -d)"
git rev-parse HEAD > "$pilot_evidence/source.commit"
"$PG_BIN/psql" -XAt -v ON_ERROR_STOP=1 -c 'SELECT version();' > "$pilot_evidence/postgresql.txt"
python3 pilot/synthetic/run_pilot.py --pg-bin "$PG_BIN" --postgrest "$POSTGREST" \
  --pg-host "$PGHOST" --pg-port "$PGPORT" --pg-user "$PGUSER" \
  --output "$pilot_evidence/run" --result "$pilot_evidence/results.jsonl"
sha256sum "$pilot_evidence/results.jsonl" > "$pilot_evidence/results.sha256"
```

Attach exact source/archive/bundle/spec hashes, review SQL and observed manifest,
functional/security/HTTP results, backup/restore and rollback/reapply evidence.
For every required cell record PASS, FAIL or NOT_RUN with command and reason.
Record actual PostgreSQL 19 build and preserve its release-candidate qualification;
a passing test is not GA qualification. All three majors remain required.

## Expected result

A technical rehearsal passes; separately obtain customer-approved p95/SLO,
RTO/RPO, acceptance dataset, support/security/rollback owners and pilot terms.
Neither synthetic PASS nor completed Jira authorises customer data, release or
production. Human/customer approval is still mandatory.

## Stop, common errors and recovery

Any failed required gate, missing evidence, untested restore/rollback or
unverified actual-client-network isolation means no customer GO. NOT_RUN is not
PASS and needs explicit named human disposition. Stop ingress and use the tested
rollback rather than improvising.

## Evidence and reference

Keep raw manifests/diagnostics private. Customer samples/diagnostics follow the
written pilot access, retention deadline and deletion owner; they are not copied
to release evidence. Release evidence follows [EVIDENCE](../EVIDENCE.md) and
[release authority](../RELEASE_AUTHORITY.md), including two-year restricted
diagnostic retention. See [deployment record](../DEPLOYMENT_RECORD.md).

