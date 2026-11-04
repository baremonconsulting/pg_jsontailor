setup {
  CREATE EXTENSION pg_jsontailor;
  CREATE SCHEMA jt_iso;
  CREATE TABLE jt_iso.root(id integer PRIMARY KEY, value text NOT NULL);
  INSERT INTO jt_iso.root VALUES (1,'initial');
  SELECT jsontailor.create_duality_view(
    'jt_iso.documents',
    jsonb_build_object('root',jsonb_build_object(
      'table','jt_iso.root','key','id','fields',jsonb_build_object('value','value'),'update',true))
  );
  CREATE TABLE jt_iso.client_document AS
    SELECT data FROM jt_iso.documents WHERE _id=1;
}
teardown { DROP SCHEMA jt_iso CASCADE; DROP EXTENSION pg_jsontailor; }

session "writer"
step "begin_writer" { BEGIN; }
step "write_root" { UPDATE jt_iso.root SET value='concurrent' WHERE id=1; }
step "commit_writer" { COMMIT; }

session "waiter"
step "begin_waiter" { BEGIN; }
step "write_stale_document" { UPDATE jt_iso.documents SET data=(SELECT data FROM jt_iso.client_document) WHERE _id=1; }
step "rollback_waiter" { ROLLBACK; }

permutation "begin_writer" "write_root" "begin_waiter" "write_stale_document" "commit_writer" "rollback_waiter"
