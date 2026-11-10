# Public HTTP boundary

The tested reference PostgREST deployment uses `tools/http_boundary.py`.
PostgREST itself must have no public TCP listener: use its
[Unix socket configuration](https://docs.postgrest.org/en/stable/references/configuration.html#server-unix-socket)
in a directory owned by the service user with mode `0700`, socket mode `0600`.
The proxy checks ownership and rejects world-accessible directories or sockets.
Do not place an untrusted local user in the service account or socket group.

```ini
# postgrest.conf: service-private configuration, never publicly downloadable
db-schemas = "api"
db-max-rows = 1000
server-unix-socket = "/run/jsontailor-private/upstream.sock"
server-unix-socket-mode = "600"
```

```sh
python3 tools/http_boundary.py \
  --upstream-socket /run/jsontailor-private/upstream.sock --view orders_dv
```

Terminate TLS and enforce authentication, request rate and per-tenant quotas at
the trusted ingress, forwarding only to the proxy's loopback listener. Never
route a fallback or health probe to raw PostgREST from the public network.
Expose the admin server only on a separate protected interface or Unix socket.
Deployment acceptance must prove the raw upstream is unreachable from the
actual public client network, not merely inspect a configuration file.

Only explicitly named document view routes and their POST delete RPCs are
forwarded. OpenAPI, the root endpoint, binding views and operational RPCs are
not public routes. Set PostgREST's exposed schemas to the API schema only;
keep `jsontailor` and source schemas outside it. Application roles must have
only the reviewed array-reader and, when needed, delete EXECUTE capabilities.
Array document projection requires the owner-only `__bounded_array(bigint)` gate;
the view owner grants it through `jsontailor.grant_array_reader`. Keep trigger,
guard and health-check helpers owner-only; never grant all functions. The
cardinality helper remains inaccessible as a public RPC because the proxy routes
only the named document view and delete RPC. See
[`RESOURCE_LIMITS.md`](RESOURCE_LIMITS.md) for direct standalone grants and migration.

The proxy rejects DELETE on document-view routes; delete through the generated
POST RPC. PATCH requires exactly one `_id=eq.<value>` filter and is forwarded
with `handling=strict, max-affected=1`. `on_conflict`, conflict-resolution
preferences and excessive limits are rejected. SQL `DELETE` or `UPDATE`
without `WHERE` still affects every visible document, and the delete RPC needs
DELETE on the view. Set a role-level `statement_timeout` and keep raw PostgREST
unreachable from outside the trusted boundary.

`db-max-rows` is the enforcing row bound for collection reads. The boundary's
query checks are not a substitute for it: a request with no `limit`, or with a
`Range` larger than the configured maximum, must still return no more than
`db-max-rows`. Before every REST pilot, repeat the no-limit, explicit-limit and
oversized-Range probes against staging through the real ingress, and repeat the
statement-timeout probe. An uncapped PostgREST control may be used only with
synthetic data on an isolated test service; it must never be publicly routed.

For every error response, including a deferred failure at COMMIT, the proxy
preserves the final HTTP status and an explicitly allow-listed SQLSTATE.
Message, details, hint and context are replaced by fixed safe fields; unknown
codes become `BOUNDARY_ERROR`. All upstream error headers are discarded.
Upstream connection, protocol and response-size failures produce a safe 502,
never a pass-through response. Responses are fully buffered, with a default
16 MiB bound. A short upstream body relative to its declared length, invalid
JSON success body or framing failure produces a safe 502, never a truncated
2xx response. A response-limit failure, disconnect or timeout may follow a
committed write: clients must reconcile state before retrying. This is not
an idempotency guarantee.

The default connection cap is 16 and the socket/upstream timeout is 10 seconds.
Chunked/compressed request bodies and ambiguous framing are rejected. Only
JSON request bodies are accepted. The proxy emits no diagnostic request logs.
Preserve full PostgreSQL/PostgREST diagnostics only in restricted server logs,
with an explicit retention owner; do not publish them as CI artifacts.

Direct SQL sessions and standalone generated DDL do **not** sanitise native
PostgreSQL diagnostics. An application using either must supply its own trusted
error boundary. No blanket PL/pgSQL exception handler or per-row subtransaction
is introduced for HTTP sanitisation.
