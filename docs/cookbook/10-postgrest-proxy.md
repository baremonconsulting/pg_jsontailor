# 10. PostgREST and the proxy

## Goal

Expose only the reviewed document API through the fail-closed HTTP boundary.

## Prerequisites

PostgREST 16.5, reviewed application role, trusted TLS/authentication ingress,
and the same dedicated service UID for PostgREST and the boundary. Clients have
no database credentials or arbitrary-SQL route. Raw PostgREST has no TCP listener.

## Steps

Create the private socket directory as the service UID (the parent /run path
must be provisioned by the administrator):

```sh
install -d -m 0700 /run/jsontailor-private
```

Use this service-private configuration in addition to the reviewed database URI,
authentication and pool configuration. Never publish credentials:

```ini
db-schemas = "api"
db-max-rows = 1000
db-pool = 10
server-unix-socket = "/run/jsontailor-private/upstream.sock"
server-unix-socket-mode = "600"
```

Start PostgREST first and require its socket to exist with the service UID and
mode 0600 before starting the boundary:

```sh
postgrest /etc/jsontailor/postgrest.conf
```

The service manager runs PostgREST separately, waits for socket readiness, then
starts this command under the same UID:

```sh
python3 tools/http_boundary.py \
  --upstream-socket /run/jsontailor-private/upstream.sock --view orders_dv \
  --host 127.0.0.1 --port 3050 --max-request-bytes 1048576 \
  --max-json-depth 32 --max-response-bytes 16777216 \
  --max-connections 16 --timeout 10
```

Route trusted ingress only to loopback port 3050. The following synthetic
probes assume the configured authentication permits the test caller; include
the deployment's approved Authorization header where required:

```sh
curl --fail --silent --show-error 'http://127.0.0.1:3050/orders_dv?_id=eq.42'
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  http://127.0.0.1:3050/shop.orders)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST http://127.0.0.1:3050/rpc/sql)" = 404
```

From the actual customer network, probe every historical/raw PostgREST TCP
address and port: any HTTP response, even 401/404, means reachable and NO-GO.
Set the reviewed deployment address explicitly and run this command from that
network (do not substitute the boundary address):

```sh
set -euo pipefail
: "${RAW_POSTGREST_HOST:?set the reviewed raw PostgREST host}"
: "${RAW_POSTGREST_PORT:?set the reviewed raw PostgREST port}"
probe_error="$(mktemp)"
trap 'rm -f "$probe_error"' EXIT
set +e
code="$(curl --silent --show-error --connect-timeout 5 --max-time 10 \
  --output /dev/null --write-out '%{http_code}' \
  "http://${RAW_POSTGREST_HOST}:${RAW_POSTGREST_PORT}/" 2>"$probe_error")"
curl_status=$?
set -e
if [ "$code" != 000 ]; then
  echo "NO-GO: raw PostgREST returned HTTP ${code}" >&2
  exit 1
fi
if [ "$curl_status" -eq 7 ] || \
   { [ "$curl_status" -eq 28 ] && grep -q 'Connection timed out' "$probe_error"; }; then
  echo 'PASS: raw PostgREST connection failed from the customer network'
else
  echo 'INCONCLUSIVE: no HTTP status, but failure did not prove network isolation' >&2
  cat "$probe_error" >&2
  exit 2
fi
```

PASS means a reviewed connection-refused or connection-timeout failure, not
merely a nonzero curl exit. Any HTTP status is NO-GO; DNS, URL, TLS, truncated
response and other failures are inconclusive. Record origin, command, target
and curl diagnostic. A loopback probe is not customer-network evidence.
If that environment is unavailable, record NOT_RUN and do not approve pilot
network isolation.

## Expected result

Allowed authenticated document requests work; source/arbitrary-SQL routes are
404 without upstream forwarding. Missing/unsafe socket prevents startup;
upstream loss returns a safe 502, never a TCP fallback. PATCH enforces one exact
identity: exactly one `_id=eq.<value>` and strict max-affected=1. DELETE uses
only the reviewed POST RPC.

## Stop, common errors and recovery

An externally reachable raw listener, unsafe socket, missing timeout/quota or
unexpected route is NO-GO. Stop ingress, restore reviewed service configuration
and rerun positive and negative checks before reopening it.

## Evidence and reference

Retain private config hashes, service ownership/modes, local/live socket tests
and actual-client-network result. See [boundary](../HTTP_BOUNDARY.md) and
[limits](../RESOURCE_LIMITS.md). Local tests are not production certification.

