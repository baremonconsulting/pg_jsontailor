# Public HTTP boundary

The supported public PostgREST deployment uses `tools/http_boundary.py`.
PostgREST itself must have no public TCP listener: use its
[Unix socket configuration](https://docs.postgrest.org/en/stable/references/configuration.html#server-unix-socket)
in a directory owned by the service user with mode `0700`, socket mode `0600`.
The proxy checks ownership and rejects world-accessible directories or sockets.
Do not place an untrusted local user in the service account or socket group.

```ini
# postgrest.conf: service-private configuration, never publicly downloadable
db-schemas = "api"
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
no helper EXECUTE grants except the reviewed delete capability.

For every error response, including a deferred failure at COMMIT, the proxy
preserves the final HTTP status and an explicitly allow-listed SQLSTATE.
Message, details, hint and context are replaced by fixed safe fields; unknown
codes become `BOUNDARY_ERROR`. All upstream error headers are discarded.
Upstream connection, protocol and response-size failures produce a safe 502,
never a pass-through response. Responses are fully buffered, with a default
16 MiB bound. A response-limit failure may follow a committed write: clients
must reconcile state before retrying. This is not an idempotency guarantee.

The default connection cap is 16 and the socket/upstream timeout is 10 seconds.
Chunked/compressed request bodies and ambiguous framing are rejected. Only
JSON request bodies are accepted. The proxy emits no diagnostic request logs.
Preserve full PostgreSQL/PostgREST diagnostics only in restricted server logs,
with an explicit retention owner; do not publish them as CI artifacts.

Direct SQL sessions and standalone generated DDL do **not** sanitise native
PostgreSQL diagnostics. An application using either must supply its own trusted
error boundary. No blanket PL/pgSQL exception handler or per-row subtransaction
is introduced for HTTP sanitisation.
