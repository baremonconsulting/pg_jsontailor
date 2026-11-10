# 13. Limits and timeouts

## Goal

Inspect limits and prove oversized input fails atomically.

## Prerequisites

Disposable shipped mapping with default 1 MiB request limit. Run as reviewed
application role in a clean psql session. Never use customer data in this probe.

## Steps

```sql
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SHOW statement_timeout;
SHOW lock_timeout;
SELECT octet_length(convert_to(data::text,'UTF8'))<1048576 AS within_budget
FROM api.orders_dv WHERE _id=42;
DO $probe$
DECLARE before_doc jsonb;
BEGIN
 SELECT data INTO before_doc FROM api.orders_dv WHERE _id=42;
 BEGIN
  UPDATE api.orders_dv SET data=jsonb_set(data,'{status}',to_jsonb(repeat('x',1048577)))
  WHERE _id=42;
  RAISE EXCEPTION 'expected PT413';
 EXCEPTION WHEN SQLSTATE 'PT413' THEN NULL;
 END;
 IF (SELECT data FROM api.orders_dv WHERE _id=42) IS DISTINCT FROM before_doc THEN
  RAISE EXCEPTION 'resource rejection changed data';
 END IF;
END
$probe$;
ROLLBACK;
```

## Expected result

5s and 1s settings, within_budget true and successful DO proving PT413 with no
persistent change. At HTTP ingress use request/depth bounds no higher than the
reviewed mapping; PostgREST db-max-rows=1000, pool at most 10 and boundary
connections 16/timeout 10 seconds are starting points, not measured SLOs.

## Stop, common errors and recovery

Unexpected success, timeout, non-PT413 error or changed data is NO-GO. Roll back.
Reduce/split payload or repair oversized source data through reviewed operations;
never blindly retry PT413 or raise limits without measurement.

## Evidence and reference

Record actual spec limits, effective role/service settings, representative plan
and p95/concurrency results. See [resource limits](../RESOURCE_LIMITS.md).

