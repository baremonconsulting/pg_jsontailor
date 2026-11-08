#!/usr/bin/env python3
"""Benchmark generated duality-view operations against direct relational SQL."""
from __future__ import annotations

import argparse
import csv
import errno
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.verify_results import ARTIFACTS, FIELDS, verify_results, verify_semantics, stable, statistics_for
from bench.workload import SIZES as REVIEWED_SIZES, OPERATIONS, IMPLEMENTATIONS, statements
from bench.installation import canonical, require, sha256, verify_attestation
from bench.measurement_attestation import attest_measurement
from bench.fixture_health import CHILD_INDEX_SQL, assert_fixture_health
from bench.attest_installation import PROBE_SCHEMA, catalogue_and_ddl, installed_files
from bench.diagnostic_evidence import DiagnosticJournal
from bench.benchmark_execution import explain
from bench.batching_protocol import (BATCH_SIZES, CALIBRATION_BATCHES, PROTOCOL_VERSION,
    TARGET_BATCH_MS, arm_order, evidence_key, level_qualifies)
from tools.strict_results import loads

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--pg-bin", type=Path,
                    default=Path(os.environ["PG_JSONTAILOR_PG_BIN"])
                    if os.environ.get("PG_JSONTAILOR_PG_BIN")
                    else Path(shutil.which("pg_config") or "/usr/bin/pg_config").parent)
parser.add_argument("--database", default="jsontailor_duality_benchmark")
parser.add_argument("--sizes", default="0,1,10,100,1000")
parser.add_argument("--warmups", type=int, default=10)
parser.add_argument("--repetitions", type=int, default=50)
parser.add_argument('--installation', type=Path, required=True)
parser.add_argument('--trusted-key', type=Path, required=True)
parser.add_argument('--measurement-key', type=Path,
                    help='protected Ed25519 operator key for the completed measurement')
parser.add_argument('--semantic-check-only', action='store_true',
                    help='run each semantic/plan probe once; never emit publishable timing evidence')
parser.add_argument('--diagnostic-fault', choices=['between-arms', 'statement',
                    'semantic-read-back', 'rollback', 'restoration'],
                    help=argparse.SUPPRESS)
parser.add_argument("--output", type=Path)
parser.add_argument("--pg-host", default=os.environ.get("PGHOST", str(ROOT / ".run" / "socket")))
parser.add_argument("--pg-port", default=os.environ.get("PGPORT", "55433"))
parser.add_argument("--pg-user", default=os.environ.get("PGUSER", os.environ.get("USER", "postgres")))
parser.add_argument("--source-commit", help="exact source commit when running outside a Git checkout")
parser.add_argument("--drop-database-after", action="store_true",
                    help="drop the disposable benchmark database after artifacts are written")
args = parser.parse_args()
PG = args.pg_bin
DB = args.database
try:
    SIZES = tuple(int(value) for value in args.sizes.split(","))
except ValueError as error:
    parser.error(f"--sizes must be a comma-separated integer list: {error}")
WARMUPS = args.warmups
REPETITIONS = args.repetitions
RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
OUT = args.output or ROOT / "results" / f"duality-benchmark-{RUN_ID}"
ENV = {**os.environ, "PGHOST": args.pg_host, "PGPORT": str(args.pg_port), "PGUSER": args.pg_user}
STARTED = datetime.now(timezone.utc).isoformat()

if SIZES != REVIEWED_SIZES:
    parser.error('--sizes must exactly match the reviewed workload 0,1,10,100,1000')
if WARMUPS != 10 or REPETITIONS < 50:
    parser.error('current evidence requires 10 warm-ups and at least 50 paired measured samples')
if not DB or DB in {"postgres", "template0", "template1"}:
    parser.error("--database must name a dedicated disposable database")
if not args.semantic_check_only and args.measurement_key is None:
    parser.error('--measurement-key is required for publishable timing evidence')


def run(args, input_text=None, check=True):
    result = subprocess.run([str(a) for a in args], input=input_text, text=True,
                            capture_output=True, env=ENV)
    if check and result.returncode:
        raise RuntimeError(f"failed {args}:\n{result.stdout}\n{result.stderr}")
    return result


def sql(text, tuples=False):
    flags = "-XqAt" if tuples else "-Xq"
    return run([PG / "psql", flags, "-v", "ON_ERROR_STOP=1", "-d", DB], text).stdout


def lit(value):
    return "'" + value.replace("'", "''") + "'"


class PersistentPsql:
    """Keep one backend so PL/pgSQL compilation and SPI plans can be reused."""
    def __init__(self):
        self.process = subprocess.Popen(
            [str(PG / "psql"), "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", DB],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=ENV, bufsize=1)

    def execute(self, script):
        marker = "__JT_END_" + uuid.uuid4().hex + "__"
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(script + f"\n\\echo {marker}\n")
        self.process.stdin.flush()
        lines=[]
        for line in self.process.stdout:
            if line.rstrip("\n") == marker:
                return "".join(lines)
            lines.append(line)
        raise RuntimeError("persistent psql exited before marker: " + "".join(lines)[-1000:])

    def close(self):
        if self.process.stdin: self.process.stdin.close()
        rc=self.process.wait(timeout=10)
        if rc: raise RuntimeError(f"persistent psql exited {rc}")


if OUT.exists() and not OUT.is_dir():
    parser.error(f"--output exists and is not a directory: {OUT}")
git_head = run(["git", "-C", ROOT, "rev-parse", "HEAD"], check=False).stdout.strip() or None
git_status_result = run(["git", "-C", ROOT, "status", "--porcelain"], check=False)
if args.source_commit and git_head and args.source_commit != git_head:
    parser.error("--source-commit differs from the current Git checkout")
source_commit = args.source_commit or git_head
if not source_commit:
    parser.error("--source-commit is required when running outside a Git checkout")
if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
    parser.error("source commit must be a full 40-character lowercase Git SHA-1")
source_dirty = bool(git_status_result.stdout) if git_head else None
require(source_dirty is False, 'benchmark source must be an immutable clean Git checkout')
installation = verify_attestation(args.installation, repo=ROOT, candidate=source_commit,
                                  trusted_key=args.trusted_key)
require(Path(installation['pg_config']).parent.resolve() == PG.resolve(),
        'benchmark binaries differ from the attested installation')

OUT.mkdir(parents=True, exist_ok=True)
collisions = [name for name in ARTIFACTS | {'manifest.json'} if (OUT / name).exists()]
if collisions:
    parser.error("--output already contains benchmark artifacts: " + ", ".join(collisions))
run([PG / "dropdb", "--if-exists", DB])
run([PG / "createdb", DB])
sql('CREATE EXTENSION pg_jsontailor;\n' + PROBE_SCHEMA)
def live_installation():
    return {'installed_files': installed_files(PG / 'pg_config', installation['source_files']),
            **catalogue_and_ddl(lambda text: sql(text, tuples=True))}
verify_attestation(args.installation, repo=ROOT, candidate=source_commit,
                   trusted_key=args.trusted_key, live=live_installation)
sql('DROP SCHEMA jt_install_probe CASCADE;')
shutil.copyfile(args.installation, OUT / 'installation.json')
shutil.copyfile(args.installation.with_name('installation.log'), OUT / 'installation.log')
diagnostic_phase = {'phase': 'database-setup', 'interrupted': None}
diagnostic = DiagnosticJournal(OUT, {
    'source_commit': source_commit,
    'source_dirty': source_dirty,
    'installation_sha256': sha256((OUT / 'installation.json').read_bytes()),
    'installation_log_sha256': sha256((OUT / 'installation.log').read_bytes()),
    'workload_sha256': sha256((ROOT / 'bench/workload.py').read_bytes()),
    'producer_sha256': sha256(Path(__file__).read_bytes()),
    'executor_sha256': sha256((ROOT / 'bench/benchmark_execution.py').read_bytes()),
    'batching_sha256': sha256((ROOT / 'bench/batching_protocol.py').read_bytes()),
    'diagnostic_verifier_sha256': sha256((ROOT / 'bench/diagnostic_evidence.py').read_bytes()),
    'command': [sys.executable, *sys.argv],
    'started_at': STARTED,
    'semantic_check_only': args.semantic_check_only,
})
original_excepthook = sys.excepthook
def diagnostic_excepthook(error_type, error, traceback):
    try:
        if not (isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EDQUOT, errno.EIO}):
            restoration = ('verified' if diagnostic_phase['phase'] == 'between-arms'
                           or diagnostic.last_kind == 'constituent-complete'
                           else 'failed' if diagnostic_phase['phase'] in {'rollback', 'restoration'}
                           else 'not-reached')
            diagnostic.fail(diagnostic_phase['phase'], error, restoration,
                            diagnostic_phase['interrupted'])
    except BaseException as diagnostic_error:
        print(f'diagnostic finalisation failed: {diagnostic_error}', file=sys.stderr)
    original_excepthook(error_type, error, traceback)
sys.excepthook = diagnostic_excepthook
sql("""
CREATE SCHEMA bench_dv;
CREATE TABLE bench_dv.customers(customer_id bigint PRIMARY KEY,name text NOT NULL);
CREATE TABLE bench_dv.orders(order_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,customer_id bigint REFERENCES bench_dv.customers,status text NOT NULL,created_at date NOT NULL);
CREATE TABLE bench_dv.items(item_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,order_id bigint NOT NULL REFERENCES bench_dv.orders,product text NOT NULL,quantity integer NOT NULL CHECK(quantity>0),unit_price numeric(12,2) NOT NULL);
CREATE TABLE bench_dv.payloads(size integer PRIMARY KEY,source_id bigint NOT NULL,insert_id bigint NOT NULL,root_doc jsonb,update_doc jsonb,insert_doc jsonb);
INSERT INTO bench_dv.customers VALUES(1,'Benchmark customer');
""")
sql(CHILD_INDEX_SQL)
for size in SIZES:
    source_id = 1000 + size
    insert_id = 100000 + size
    sql(f"INSERT INTO bench_dv.orders VALUES({source_id},1,'new',DATE '2026-11-03');\n" +
        (f"INSERT INTO bench_dv.items(item_id,order_id,product,quantity,unit_price) SELECT {source_id}*10000+g,{source_id},'Product '||g,1,10.25 FROM generate_series(1,{size}) g;" if size else ""))
spec = json.dumps({
    "root":{"table":"bench_dv.orders","key":"order_id","fields":{"status":"status","created":"created_at"},"insert":True,"update":True,"delete":True},
    "object":{"name":"customer","table":"bench_dv.customers","key":"customer_id","root_fk":"customer_id","fields":{"customerId":"customer_id","name":"name"},"etag":False},
    "array":{"name":"items","table":"bench_dv.items","key":"item_id","parent_fk":"order_id","order_by":"item_id","fields":{"itemId":"item_id","product":"product","qty":"quantity","price":"unit_price"},"insert":True,"update":True,"delete":True}
})
sql(f"SELECT jsontailor.create_duality_view('bench_dv.orders_dv',{lit(spec)}::jsonb);")
assert_fixture_health(lambda statement: sql(statement, tuples=True))
for size in SIZES:
    source_id = 1000 + size; insert_id = 100000 + size
    sql(f"""
INSERT INTO bench_dv.payloads(size,source_id,insert_id,root_doc,update_doc,insert_doc)
SELECT {size},{source_id},{insert_id},
       jsonb_set(v.data,'{{status}}','"root-updated"'),
       jsonb_set(jsonb_set(v.data,'{{status}}','\"updated\"'), '{{items}}',
         COALESCE((SELECT jsonb_agg(x.elem || jsonb_build_object('qty',(x.elem->>'qty')::integer+1) ORDER BY x.ord)
                   FROM jsonb_array_elements(v.data->'items') WITH ORDINALITY x(elem,ord)),'[]'::jsonb)),
       jsonb_build_object('_id',{insert_id},'status','inserted','created','2026-11-03','customer',jsonb_build_object('customerId',1),
         'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('itemId',{insert_id}*10000+x.ord,'product',x.elem->>'product','qty',2,'price',(x.elem->>'price')::numeric) ORDER BY x.ord)
                           FROM jsonb_array_elements(v.data->'items') WITH ORDINALITY x(elem,ord)),'[]'::jsonb))
FROM bench_dv.orders_dv v WHERE v._id={source_id};
""")
sql("ANALYZE bench_dv.orders; ANALYZE bench_dv.items; ANALYZE bench_dv.payloads;")

settings = loads(sql("SELECT jsonb_object_agg(name,setting) FROM pg_settings WHERE name IN "
    "('shared_buffers','work_mem','synchronous_commit','fsync','full_page_writes','server_version_num',"
    "'max_connections','effective_cache_size','checkpoint_timeout','wal_level','default_transaction_isolation',"
    "'TimeZone','lc_collate','lc_ctype');", tuples=True))
database_locale = loads(sql("SELECT jsonb_build_object('lc_collate',datcollate,'lc_ctype',datctype) "
                            "FROM pg_database WHERE datname=current_database();", tuples=True))
settings.update(database_locale)
settings.update(jit='off', max_parallel_workers_per_gather='0')
postgresql = sql('SHOW server_version;', tuples=True).strip()
postgresql_major = str(int(sql('SHOW server_version_num;', tuples=True).strip()) // 10000)
diagnostic.append('environment', {
    'postgresql': postgresql, 'postgresql_major': postgresql_major,
    'server_settings': settings, 'platform': platform.platform(),
    'cpu': Path('/proc/cpuinfo').read_text(), 'memory': Path('/proc/meminfo').read_text(),
    'storage': str(os.statvfs(OUT)), 'load': str(os.getloadavg()), 'database': DB,
    'pg_host': args.pg_host, 'pg_port': str(args.pg_port), 'pg_user': args.pg_user,
    'pg_bin': str(PG.resolve()), 'python': sys.version, 'sizes': list(SIZES),
    'operations': list(OPERATIONS), 'warmups': WARMUPS, 'maximum_warmups': 30,
    'stability_threshold': .10, 'repetitions': REPETITIONS, 'sessions': 3,
    'batching_protocol_version': PROTOCOL_VERSION, 'batch_sizes': list(BATCH_SIZES),
    'calibration_batches': CALIBRATION_BATCHES, 'target_batch_ms': TARGET_BATCH_MS,
    'semantic_check_only': args.semantic_check_only,
})




def snapshot(session, size):
    def relational(identity):
        return f"""(SELECT jsonb_build_object('_id',o.order_id,'status',o.status,'created',o.created_at,
          'customer',jsonb_build_object('customerId',c.customer_id,'name',c.name),
          'items',coalesce((SELECT jsonb_agg(jsonb_build_object('itemId',i.item_id,'product',i.product,
          'qty',i.quantity,'price',i.unit_price) ORDER BY i.item_id) FROM bench_dv.items i
          WHERE i.order_id=o.order_id),'[]'::jsonb)) FROM bench_dv.orders o LEFT JOIN
          bench_dv.customers c ON c.customer_id=o.customer_id WHERE o.order_id={identity})"""
    def document(identity):
        return f"(SELECT data-'_metadata' FROM bench_dv.orders_dv WHERE _id={identity})"
    source, inserted = 1000 + size, 100000 + size
    return loads(session.execute("SELECT jsonb_build_object('relational',jsonb_build_object('source'," +
        relational(source) + ",'inserted'," + relational(inserted) +
        "),'documents',jsonb_build_object('source'," + document(source) + ",'inserted'," + document(inserted) + '));'))


rows, saved_plans, semantic_samples, states, backends = [], {}, {}, {}, []
diagnostic_states = set()
diagnostic_fault_used = False
def inject_diagnostic_fault(phase, coordinates, *, next_statement=None):
    global diagnostic_fault_used
    if args.diagnostic_fault != phase or diagnostic_fault_used:
        return
    diagnostic_fault_used = True
    interrupted = dict(coordinates)
    if next_statement is not None:
        interrupted.update(statement=next_statement[0], sql=next_statement[1])
    diagnostic_phase.update(phase=phase, interrupted=interrupted)
    raise RuntimeError('forced producer diagnostic fault: ' + phase)

def intern(state):
    identity = sha256(canonical(state))
    states[identity] = state
    if identity not in diagnostic_states:
        diagnostic.append('semantic-state', {'sha256': identity, 'value': state})
        diagnostic_states.add(identity)
    return identity


def run_constituent(session, coordinates):
    implementation = coordinates['implementation']
    size, operation = coordinates['size'], coordinates['operation']
    diagnostic_phase.update(phase='constituent', interrupted=coordinates)
    diagnostic.append('constituent-start', coordinates)
    session.execute('BEGIN;')
    before = snapshot(session, size)
    before_id = intern(before)
    diagnostic.append('semantic-witness', {**coordinates, 'role': 'before', 'sha256': before_id})
    workload = statements(operation, implementation, size)
    def retain_statement(index, statement, plan):
        diagnostic.append('statement-complete', {**coordinates, 'statement': index,
            'sql': statement, 'plan': plan})
        if index + 1 < len(workload):
            inject_diagnostic_fault('statement', coordinates,
                                    next_statement=(index + 1, workload[index + 1]))
    def identify_statement(index, statement):
        diagnostic_phase.update(phase='statement', interrupted={**coordinates,
            'statement': index, 'sql': statement})
    result = explain(session, workload, retain_statement, identify_statement)
    diagnostic_phase.update(phase='semantic-read-back', interrupted=coordinates)
    inject_diagnostic_fault('semantic-read-back', coordinates)
    after = snapshot(session, size)
    after_id = intern(after)
    diagnostic.append('semantic-witness', {**coordinates, 'role': 'after', 'sha256': after_id})
    diagnostic_phase.update(phase='rollback', interrupted=coordinates)
    inject_diagnostic_fault('rollback', coordinates)
    session.execute('ROLLBACK;')
    diagnostic_phase.update(phase='restoration', interrupted=coordinates)
    inject_diagnostic_fault('restoration', coordinates)
    restored = snapshot(session, size)
    restored_id = intern(restored)
    diagnostic.append('semantic-witness', {**coordinates, 'role': 'rollback', 'sha256': restored_id})
    semantic = {'before': before, 'after': after, 'rollback': restored,
        'branch': operation.removeprefix('merge_') if operation.startswith('merge_') else 'not_merge'}
    verify_semantics(semantic, size, operation)
    key = evidence_key(coordinates['session'], size, operation, implementation,
                       coordinates['phase'], coordinates['level'], coordinates['batch'],
                       coordinates['constituent'])
    semantic_samples[key] = {'before': before_id, 'after': after_id,
                             'rollback': restored_id, 'branch': semantic['branch']}
    saved_plans[key] = {'statements': workload, 'plans': result.pop('plans')}
    row = {**coordinates, **result, 'status': 'PASS'}
    rows.append(row)
    diagnostic.append('constituent-complete', {**coordinates,
        'execution_ms': result['execution_ms'], 'planning_ms': result['planning_ms'],
        'shared_hit_blocks': result['shared_hit_blocks'],
        'shared_dirtied_blocks': result['shared_dirtied_blocks'],
        'wal_records': result['wal_records'], 'before': before_id, 'after': after_id,
        'rollback': restored_id, 'restoration': 'verified'})
    diagnostic_phase.update(phase='between-arms', interrupted=coordinates)
    inject_diagnostic_fault('between-arms', coordinates)
    return result


for session_number in range(1 if args.semantic_check_only else 3):
    session = PersistentPsql()
    try:
        session.execute('SET jit=off; SET max_parallel_workers_per_gather=0; SET statement_timeout=60000;')
        backend = loads(session.execute("SELECT jsonb_build_object('pid',pg_backend_pid(),"
            "'backend_start',(SELECT backend_start::text FROM pg_stat_activity WHERE pid=pg_backend_pid()));"))
        backends.append({'session': session_number, **backend})
        diagnostic.append('session-start', {'session': session_number, **backend})
        for size in SIZES:
            for operation in OPERATIONS:
                if args.semantic_check_only:
                    aggregate = {implementation: 0.0 for implementation in IMPLEMENTATIONS}
                    for pair_order, implementation in enumerate(arm_order(0, 0)):
                        result = run_constituent(session, {'session': session_number, 'size': size,
                            'operation': operation, 'implementation': implementation,
                            'phase': 'measured', 'level': 1, 'batch': 0, 'constituent': 0,
                            'batch_size': 1, 'pair_order': pair_order})
                        aggregate[implementation] += result['execution_ms']
                    diagnostic.append('smoke-group-complete', {'session': session_number,
                        'size': size, 'operation': operation, 'level': 1, 'batch': 0,
                        'phase': 'measured', 'totals': aggregate})
                    continue
                selected = None
                for level in BATCH_SIZES:
                    totals = {implementation: [] for implementation in IMPLEMENTATIONS}
                    for batch in range(CALIBRATION_BATCHES):
                        aggregate = {implementation: 0.0 for implementation in IMPLEMENTATIONS}
                        for constituent in range(level):
                            for pair_order, implementation in enumerate(arm_order(batch, constituent)):
                                result = run_constituent(session, {'session': session_number, 'size': size,
                                    'operation': operation, 'implementation': implementation,
                                    'phase': 'calibration', 'level': level, 'batch': batch,
                                    'constituent': constituent, 'batch_size': level,
                                    'pair_order': pair_order})
                                aggregate[implementation] += result['execution_ms']
                        for implementation in IMPLEMENTATIONS:
                            totals[implementation].append(aggregate[implementation])
                    diagnostic.append('calibration-level-complete', {'session': session_number,
                        'size': size, 'operation': operation, 'level': level, 'totals': totals})
                    if level_qualifies(totals):
                        selected = level
                        break
                if selected is None:
                    raise RuntimeError(f'calibration batch-size cap exhausted: {session_number}:{size}:{operation}')
                warmup_values = {implementation: [] for implementation in IMPLEMENTATIONS}
                warmup_count, batch = None, 0
                while warmup_count is None or batch < warmup_count + REPETITIONS:
                    phase = 'warmup' if warmup_count is None else 'measured'
                    aggregate = {implementation: 0.0 for implementation in IMPLEMENTATIONS}
                    for constituent in range(selected):
                        for pair_order, implementation in enumerate(arm_order(batch, constituent)):
                            result = run_constituent(session, {'session': session_number, 'size': size,
                                'operation': operation, 'implementation': implementation,
                                'phase': phase, 'level': selected, 'batch': batch,
                                'constituent': constituent, 'batch_size': selected,
                                'pair_order': pair_order})
                            aggregate[implementation] += result['execution_ms']
                    diagnostic.append('batch-complete', {'session': session_number, 'size': size,
                        'operation': operation, 'phase': phase, 'level': selected,
                        'batch': batch, 'totals': aggregate})
                    if phase == 'warmup':
                        for implementation in IMPLEMENTATIONS:
                            warmup_values[implementation].append(aggregate[implementation])
                    batch += 1
                    if phase == 'warmup' and batch >= 10 and batch % 5 == 0:
                        if all(stable(values) for values in warmup_values.values()):
                            warmup_count = batch
                        elif batch == 30:
                            raise RuntimeError(f'warm-up stability FAIL after 30 batches: {session_number}:{size}:{operation}')
    except BaseException:
        try:
            session.close()
        except BaseException as close_error:
            print(f'persistent psql close after failure also failed: {close_error}', file=sys.stderr)
        raise
    else:
        session.close()

if args.semantic_check_only:
    if args.drop_database_after:
        run([PG / 'dropdb', DB])
    diagnostic.discard_after_success()
    print(json.dumps({'status': 'PASS', 'semantic_probes': len(rows), 'candidate': source_commit,
                      'purpose': 'semantic/installation smoke only; not performance evidence'}))
    raise SystemExit(0)

with (OUT / 'measurements.csv').open('w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=FIELDS)
    writer.writeheader(); writer.writerows(rows)
(OUT / 'plans.json').write_text(json.dumps(saved_plans))
(OUT / 'semantics.json').write_text(json.dumps({'states': states, 'samples': semantic_samples}))
summary = []
for session_number in range(3):
    for size in SIZES:
        for operation in OPERATIONS:
            values = {}
            for impl in IMPLEMENTATIONS:
                measured = [r for r in rows if r['session'] == session_number and r['size'] == size
                    and r['operation'] == operation and r['implementation'] == impl
                    and r['phase'] == 'measured']
                batches = {}
                for row in measured:
                    batches.setdefault(row['batch'], []).append(row['execution_ms'])
                values[impl] = {
                    'individual': statistics_for([r['execution_ms'] for r in measured]),
                    'batch_mean': statistics_for([sum(group) / len(group)
                                                  for group in batches.values()]),
                }
            summary.append({'session': session_number, 'size': size, 'operation': operation, **values,
                'median_ratio': values['duality']['batch_mean']['median_ms']
                                / values['direct']['batch_mean']['median_ms']})
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
metadata = {'schema_version': 4, 'run_id': RUN_ID, 'postgresql': postgresql,
    'postgresql_major': postgresql_major,
    'platform': platform.platform(), 'cpu': Path('/proc/cpuinfo').read_text(),
    'memory': Path('/proc/meminfo').read_text(), 'storage': str(os.statvfs(OUT)), 'load': str(os.getloadavg()),
    'sizes': SIZES, 'operations': OPERATIONS, 'warmups': WARMUPS, 'maximum_warmups': 30,
    'stability_threshold': .10, 'repetitions': REPETITIONS, 'sessions': 3, 'session_backends': backends,
    'batching_protocol_version': PROTOCOL_VERSION, 'batch_sizes': BATCH_SIZES,
    'calibration_batches': CALIBRATION_BATCHES, 'target_batch_ms': TARGET_BATCH_MS,
    'server_settings': settings, 'database': DB, 'pg_host': args.pg_host, 'pg_port': str(args.pg_port),
    'pg_user': args.pg_user, 'pg_bin': str(PG.resolve()), 'command': [sys.executable, *sys.argv],
    'started_at': STARTED, 'finished_at': datetime.now(timezone.utc).isoformat(), 'python': sys.version,
    'direct_contract': 'lower-contract direct SQL', 'restart_policy': 'new persistent backend per session',
    'source_commit': source_commit, 'source_dirty': source_dirty}
(OUT / 'environment.json').write_text(json.dumps(metadata, indent=2))
verify_attestation(args.installation, repo=ROOT, candidate=source_commit, trusted_key=args.trusted_key)
(OUT / 'manifest.json').write_text(json.dumps({'schema_version': 1,
    'files': {name: sha256((OUT / name).read_bytes()) for name in sorted(ARTIFACTS)}}, indent=2))
attest_measurement(OUT, source_commit, args.measurement_key)
verify_results(OUT, candidate=source_commit, trusted_key=args.trusted_key,
               _producer_pre_discard=True)
diagnostic.discard_after_success()
verification = verify_results(OUT, candidate=source_commit, trusted_key=args.trusted_key)
if args.drop_database_after:
    run([PG / 'dropdb', DB])
print(json.dumps({'run_id': RUN_ID, 'output': str(OUT), **verification}, indent=2))
