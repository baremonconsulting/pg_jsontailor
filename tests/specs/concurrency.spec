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
  CREATE TABLE jt_iso.array_root(id integer PRIMARY KEY, value text NOT NULL);
  CREATE TABLE jt_iso.array_child(id integer PRIMARY KEY,
    parent integer NOT NULL REFERENCES jt_iso.array_root(id), value text NOT NULL);
  INSERT INTO jt_iso.array_root VALUES (1,'one'),(2,'two');
  INSERT INTO jt_iso.array_child VALUES (1,1,'old');
  SELECT jsontailor.create_duality_view(
    'jt_iso.array_documents',
    jsonb_build_object(
      'root',jsonb_build_object('table','jt_iso.array_root','key','id',
        'fields',jsonb_build_object('value','value'),'update',true),
      'array',jsonb_build_object('name','items','table','jt_iso.array_child','key','id',
        'parent_fk','parent','fields',jsonb_build_object('id','id','value','value'),
        'insert',true,'update',true,'delete',true))
  );
  CREATE FUNCTION jt_iso.pause_child_update() RETURNS trigger LANGUAGE plpgsql AS $$
  BEGIN
    IF NEW.id=1 THEN PERFORM pg_advisory_xact_lock(8675309); END IF;
    RETURN NEW;
  END$$;
  CREATE TRIGGER pause_child_update BEFORE UPDATE ON jt_iso.array_child
    FOR EACH ROW EXECUTE FUNCTION jt_iso.pause_child_update();
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

step "begin_array_waiter" { BEGIN; }
step "write_raced_array" { UPDATE jt_iso.array_documents SET data=jsonb_set(data,ARRAY['items'],jsonb_build_array(jsonb_build_object('id',1,'value','changed'),jsonb_build_object('id',99,'value','requested'))) WHERE _id=1; }
step "rollback_array_waiter" { ROLLBACK; }

session "owner"
step "begin_owner" { BEGIN; }
step "lock_owner" { DO $lock$ BEGIN PERFORM pg_advisory_xact_lock(8675309); END $lock$; }
step "claim_child" { INSERT INTO jt_iso.array_child VALUES(99,2,'foreign'); }
step "commit_owner" { COMMIT; }

permutation "begin_writer" "write_root" "begin_waiter" "write_stale_document" "commit_writer" "rollback_waiter"
permutation "begin_owner" "lock_owner" "begin_array_waiter" "write_raced_array" "claim_child" "commit_owner" "rollback_array_waiter"
