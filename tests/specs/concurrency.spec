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
  CREATE TABLE jt_iso.reference(id integer PRIMARY KEY,label text NOT NULL);
  CREATE TABLE jt_iso.object_root(id integer PRIMARY KEY,
    reference_id integer NOT NULL REFERENCES jt_iso.reference(id),value text NOT NULL);
  INSERT INTO jt_iso.reference VALUES(7,'shared');
  INSERT INTO jt_iso.object_root VALUES(1,7,'one'),(2,7,'two');
  SELECT jsontailor.create_duality_view(
    'jt_iso.object_documents',
    jsonb_build_object(
      'root',jsonb_build_object('table','jt_iso.object_root','key','id',
        'fields',jsonb_build_object('value','value'),'insert',true,'delete',true),
      'object',jsonb_build_object('name','reference','table','jt_iso.reference','key','id',
        'root_fk','reference_id','fields',jsonb_build_object('id','id','label','label')))
  );
  CREATE TABLE jt_iso.object_etags AS
    SELECT _id,data#>>ARRAY['_metadata','etag'] AS etag FROM jt_iso.object_documents;
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

session "deleter"
step "begin_helper_delete" { BEGIN; }
step "helper_delete" { DO $delete$ BEGIN PERFORM jt_iso.object_documents__delete_document(1,(SELECT etag FROM jt_iso.object_etags WHERE _id=1)); END $delete$; }
step "begin_direct_delete" { BEGIN; }
step "direct_delete" { DELETE FROM jt_iso.object_documents WHERE _id=2 AND data#>>ARRAY['_metadata','etag']=(SELECT etag FROM jt_iso.object_etags WHERE _id=2); }
step "rollback_delete" { ROLLBACK; }

session "fk_writer"
step "begin_fk_writer" { BEGIN; }
step "insert_same_reference" { INSERT INTO jt_iso.object_root VALUES(3,7,'new'); }
step "rollback_fk_writer" { ROLLBACK; }

session "object_writer"
step "begin_object_writer" { BEGIN; }
step "update_shared_object" { UPDATE jt_iso.reference SET label='changed' WHERE id=7; }
step "rollback_object_writer" { ROLLBACK; }

permutation "begin_writer" "write_root" "begin_waiter" "write_stale_document" "commit_writer" "rollback_waiter"
permutation "begin_owner" "lock_owner" "begin_array_waiter" "write_raced_array" "claim_child" "commit_owner" "rollback_array_waiter"
permutation "begin_helper_delete" "helper_delete" "begin_fk_writer" "insert_same_reference" "rollback_fk_writer" "begin_object_writer" "update_shared_object" "rollback_delete" "rollback_object_writer"
permutation "begin_direct_delete" "direct_delete" "begin_fk_writer" "insert_same_reference" "rollback_fk_writer" "begin_object_writer" "update_shared_object" "rollback_delete" "rollback_object_writer"
