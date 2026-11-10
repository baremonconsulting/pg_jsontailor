# 10. PostgREST and the proxy

## Goal

Expose only the reviewed document API through the fail-closed HTTP boundary.

## Prerequisites

PostgREST 16.5, reviewed application role, trusted TLS/authentication ingress,
and the same dedicated service UID for PostgREST and the boundary. Clients have
no database credentials or arbitrary-SQL route. Raw PostgREST has no TCP listener.
Ingress must enforce header and body timeouts, a whole-request deadline,
request buffering, authenticated rate limits and per-tenant quotas. The proxy's
per-recv timeout is not a whole-request deadline. Python 3 and curl are required
for the synthetic probes. Use only a disposable synthetic mapping for write probes.

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

`db-max-rows = 1000` is the actual enforcing control for collection row count.
The boundary does not invent a missing `limit` and a client cannot bypass this
database-facing cap with a `Range` larger than 1000. On staging, through the
real ingress and authentication, verify that a request without `limit` and an
oversized `Range` each return at most 1000 rows, an explicit smaller limit is
honoured, and the role-level statement timeout returns the documented safe
error. Repeat these row-bound and timeout checks before every REST pilot. Use
only synthetic data; never expose an uncapped control service.

Configuration/start commands are service-manager deployment templates. Request
status expectations below are verified in CI only by `tests/run_postgrest.py`
(`PGJT-48/http-get`, `http-complete-patch`, `http-delete-rpc`) and
`tests/http_security_contract.py` (`PGJT-113/http-query-preferences`). The database
documentation smoke does not certify a live customer HTTP stack; repeat these
checks on staging with its actual authentication and ingress configuration.

```sh
curl --fail --silent --show-error 'http://127.0.0.1:3050/orders_dv?_id=eq.42'
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  http://127.0.0.1:3050/shop.orders)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -X POST http://127.0.0.1:3050/rpc/sql)" = 404
```

The four request examples below create and remove one synthetic document; do
not substitute a customer document. The final RPC uses the ETag returned by
PATCH, not the earlier read. With authentication enabled, add the deployment's
approved header to every curl invocation; never write credentials to public logs.

```sh
set -euo pipefail
umask 077
http_probe="$(mktemp -d)"
trap 'rm -f "$http_probe/insert.json" "$http_probe/read.json" "$http_probe/patch.json" "$http_probe/patched.json" "$http_probe/delete.json"; rmdir "$http_probe"' EXIT
test "$(curl --silent --show-error --output "$http_probe/insert.json" --write-out '%{http_code}' \
  -H 'Content-Type: application/json' -H 'Prefer: return=representation' \
  --data '{"data":{"status":"recipe-http","customer":{"customerId":7},"items":[]}}' \
  http://127.0.0.1:3050/orders_dv)" = 201
document_id="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[0]["_id"])' "$http_probe/insert.json")"
# Read: 200, one row containing data and _metadata.etag.
test "$(curl --silent --show-error --output "$http_probe/read.json" --write-out '%{http_code}' \
  "http://127.0.0.1:3050/orders_dv?_id=eq.${document_id}")" = 200
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]))[0]["data"];d["status"]="recipe-patched";print(json.dumps({"data":d}))' \
  "$http_probe/read.json" > "$http_probe/patch.json"
# Complete-document PATCH with one identity: 200 and the new ETag.
test "$(curl --silent --show-error --output "$http_probe/patched.json" --write-out '%{http_code}' \
  -X PATCH -H 'Content-Type: application/json' -H 'Prefer: return=representation' \
  --data-binary "@$http_probe/patch.json" \
  "http://127.0.0.1:3050/orders_dv?_id=eq.${document_id}")" = 200
# Rejected upsert: 400, no upstream call and no document change.
test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  -H 'Content-Type: application/json' --data-binary "@$http_probe/patch.json" \
  'http://127.0.0.1:3050/orders_dv?on_conflict=_id')" = 400
python3 -c 'import json,sys;r=json.load(open(sys.argv[1]))[0];print(json.dumps({"expected_id":r["_id"],"expected_etag":r["data"]["_metadata"]["etag"]}))' \
  "$http_probe/patched.json" > "$http_probe/delete.json"
# RPC delete: 200, the deleted document; do not use DELETE on the view route.
test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  -H 'Content-Type: application/json' --data-binary "@$http_probe/delete.json" \
  http://127.0.0.1:3050/rpc/orders_dv__delete_document)" = 200
```

Real error example: a PATCH without `_metadata.etag` returns HTTP 428 / PT428.
Its cause is a missing replacement token; read the current complete document,
apply the intended change and submit that retained token. Do not manufacture a
current-token subquery to bypass the client's precondition. If a probe fails
after creation, retain its synthetic identity privately and reconcile/delete it
through the reviewed RPC before rerunning; a timeout may follow COMMIT.

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

