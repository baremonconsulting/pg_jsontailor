# 10. PostgREST and the proxy

## Goal

Expose the generated API only through the tested HTTP boundary.

## Prerequisites

Use PostgREST 16.5 on a private endpoint, the reviewed application role,
`db-max-rows=1000`, role `statement_timeout` and TLS/authentication at ingress.
Raw PostgREST must be unreachable externally.

## Steps

Start PostgREST privately, then start `tools/http_boundary.py` with matching
upstream URL, request/depth/response bounds, connection cap and timeout. Route
public traffic only to the boundary. Verify:

1. GET and bounded keyset reads;
2. PATCH with exactly one `_id=eq.<value>` and a complete document;
3. DELETE rejection on views and deletion through
   `POST /rpc/<view>__delete_document`;
4. rejection of upsert controls, duplicate/extra identity filters and excessive
   limits without an upstream call.

PATCH is forwarded with `handling=strict, max-affected=1`.

## Expected result

Allowed requests preserve PostgREST status and safe bodies. Rejected routes do
not reach upstream and never expose SQL text or credentials.

## Common errors

An externally reachable raw PostgREST endpoint bypasses the route contract.
Missing `db-max-rows`, timeouts or exact identity filtering makes deployment
NO-GO. This is a tested reference deployment, not general certification.

## Reference

See [HTTP boundary](../HTTP_BOUNDARY.md), [resource limits](../RESOURCE_LIMITS.md)
and the [synthetic acceptance plan](../../pilot/synthetic/ACCEPTANCE_PLAN.md).

