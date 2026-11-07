"""The benchmark must satisfy the same index contract as a supported mapping."""
from bench.installation import require
from tools.strict_results import loads

CHILD_INDEX_SQL = 'CREATE INDEX ON bench_dv.items(order_id,item_id);'


def assert_fixture_health(query):
    result = loads(query('SELECT bench_dv.orders_dv__check();'))
    require(isinstance(result, dict) and result.get('status') == 'OK',
            'benchmark fixture health check failed')
