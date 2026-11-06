EXTENSION = pg_jsontailor
DATA = sql/pg_jsontailor--0.2.0.sql

REGRESS = basic
REGRESS_OPTS = --inputdir=tests --outputdir=tests/output
ISOLATION = concurrency pgjt_43_concurrency
ISOLATION_OPTS = --inputdir=tests --outputdir=tests/output_iso

PG_CONFIG ?= pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)

pg_regress_clean_files = tests/output/ tests/output_iso/

