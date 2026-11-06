setup {
  CREATE EXTENSION pg_jsontailor;
  CREATE SCHEMA jt_iso47;
  CREATE TABLE jt_iso47.root(id integer PRIMARY KEY,label text NOT NULL)
    PARTITION BY RANGE(id);
  CREATE TABLE jt_iso47.root_low PARTITION OF jt_iso47.root
    FOR VALUES FROM(0) TO(100);
  CREATE TABLE jt_iso47.root_high PARTITION OF jt_iso47.root
    FOR VALUES FROM(100) TO(200);
  INSERT INTO jt_iso47.root VALUES(150,'before');
  SELECT jsontailor.create_duality_view(
    'jt_iso47.documents',jsonb_build_object('root',jsonb_build_object(
      'table','jt_iso47.root','key','id','fields',jsonb_build_object('label','label'),
      'update',true)));
}

teardown { DROP SCHEMA jt_iso47 CASCADE; DROP EXTENSION pg_jsontailor; }

session "writer"
step "begin_writer" { BEGIN; }
step "write_document" {
  UPDATE jt_iso47.documents
  SET data=jsonb_set(data,ARRAY['label'],to_jsonb('written'::text))
  WHERE _id=150;
}
step "commit_writer" { COMMIT; }

session "partition_ddl"
step "begin_ddl" { BEGIN; }
step "detach_high" {
  ALTER TABLE jt_iso47.root DETACH PARTITION jt_iso47.root_high;
}
step "commit_detach" { COMMIT; }
step "rollback_detach" { ROLLBACK; }

session "verifier"
step "verify_detached" {
  SELECT 'PGJT-47/concurrent-partition-ddl' test_id,
    NOT EXISTS(SELECT 1 FROM pg_inherits
      WHERE inhrelid='jt_iso47.root_high'::regclass) detached,
    (SELECT label FROM jt_iso47.root_high WHERE id=150) label;
}
step "verify_attached" {
  SELECT 'PGJT-47/concurrent-partition-ddl' test_id,
    EXISTS(SELECT 1 FROM pg_inherits
      WHERE inhrelid='jt_iso47.root_high'::regclass) attached,
    (SELECT label FROM jt_iso47.root WHERE id=150) label;
}

permutation "begin_writer" "write_document" "begin_ddl" "detach_high" "commit_writer" "commit_detach" "verify_detached"
permutation "begin_ddl" "detach_high" "begin_writer" "write_document" "rollback_detach" "commit_writer" "verify_attached"
