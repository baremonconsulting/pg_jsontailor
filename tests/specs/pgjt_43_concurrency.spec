setup {
  CREATE EXTENSION pg_jsontailor;
  CREATE SCHEMA jt_iso43;
  CREATE TABLE jt_iso43.results(test_id text PRIMARY KEY, sqlstate text NOT NULL);

  CREATE TABLE jt_iso43.refs(id integer PRIMARY KEY,label text NOT NULL);
  CREATE TABLE jt_iso43.roots(id integer PRIMARY KEY,
    ref_id integer NOT NULL REFERENCES jt_iso43.refs(id),label text NOT NULL);
  INSERT INTO jt_iso43.refs VALUES(7,'seven'),(8,'eight');
  INSERT INTO jt_iso43.roots VALUES(1,7,'one'),(2,7,'two');
  SELECT jsontailor.create_duality_view(
    'jt_iso43.documents',jsonb_build_object(
      'root',jsonb_build_object('table','jt_iso43.roots','key','id',
        'fields',jsonb_build_object('label','label'),'update',true),
      'object',jsonb_build_object('name','reference','table','jt_iso43.refs','key','id',
        'root_fk','ref_id','fields',jsonb_build_object('id','id','label','label'),'etag',true)));
  CREATE TABLE jt_iso43.docs AS SELECT _id,data FROM jt_iso43.documents;

  CREATE TABLE jt_iso43.rpc_root(id integer PRIMARY KEY,label text NOT NULL);
  INSERT INTO jt_iso43.rpc_root VALUES(1,'original');
  SELECT jsontailor.create_duality_view(
    'jt_iso43.rpc_documents',jsonb_build_object('root',jsonb_build_object(
      'table','jt_iso43.rpc_root','key','id','fields',jsonb_build_object('label','label'),
      'delete',true)));
  CREATE TABLE jt_iso43.rpc_etag AS
    SELECT data#>>ARRAY['_metadata','etag'] etag FROM jt_iso43.rpc_documents WHERE _id=1;

  CREATE TABLE jt_iso43.def_root(id integer PRIMARY KEY,value text NOT NULL);
  CREATE TABLE jt_iso43.def_child(id integer,
    parent integer NOT NULL REFERENCES jt_iso43.def_root(id),value text NOT NULL,
    CONSTRAINT def_child_pkey PRIMARY KEY(id) DEFERRABLE INITIALLY IMMEDIATE,
    CONSTRAINT def_child_value_key UNIQUE(value));
  INSERT INTO jt_iso43.def_root VALUES(1,'one'),(2,'two');
  INSERT INTO jt_iso43.def_child VALUES(10,1,'v10'),(11,1,'v11'),(100,2,'v100');
  SELECT jsontailor.create_duality_view(
    'jt_iso43.def_documents',jsonb_build_object(
      'root',jsonb_build_object('table','jt_iso43.def_root','key','id',
        'fields',jsonb_build_object('value','value'),'update',true),
      'array',jsonb_build_object('name','items','table','jt_iso43.def_child','key','id',
        'parent_fk','parent','fields',jsonb_build_object('id','id','value','value'),
        'insert',true,'update',true,'delete',true)));
  CREATE TABLE jt_iso43.def_doc AS SELECT data FROM jt_iso43.def_documents WHERE _id=1;
}

teardown { DROP SCHEMA jt_iso43 CASCADE; DROP EXTENSION pg_jsontailor; }

session "document_writer"
step "begin_current" { BEGIN; }
step "write_current" {
  UPDATE jt_iso43.documents SET data=jsonb_set(data,ARRAY['label'],to_jsonb('current'::text))
  WHERE _id=1;
}
step "commit_current" { COMMIT; }
step "begin_new" { BEGIN; }
step "write_new" {
  UPDATE jt_iso43.documents SET data=jsonb_set(
    jsonb_set(data,ARRAY['label'],to_jsonb('new-object'::text)),
    ARRAY['reference'],jsonb_build_object('id',8,'label','eight')) WHERE _id=2;
}
step "commit_new" { COMMIT; }

session "object_writer"
step "update_current_object" { UPDATE jt_iso43.refs SET label='current-waited' WHERE id=7; }
step "update_new_object" { UPDATE jt_iso43.refs SET label='new-waited' WHERE id=8; }

session "root_blocker"
step "begin_root_blocker" { BEGIN; }
step "lock_root" { UPDATE jt_iso43.roots SET label=label WHERE id=2; }
step "commit_root_blocker" { COMMIT; }

session "stale_writer"
step "write_stale_object" {
  DO $body$ BEGIN
    UPDATE jt_iso43.documents SET data=jsonb_set(
      (SELECT data FROM jt_iso43.docs WHERE _id=2),
      ARRAY['label'],to_jsonb('must-not-commit'::text))
    WHERE _id=2;
    INSERT INTO jt_iso43.results VALUES('PGJT-34/stale-object-etag','00000');
  EXCEPTION WHEN OTHERS THEN
    INSERT INTO jt_iso43.results VALUES('PGJT-34/stale-object-etag',SQLSTATE);
  END $body$;
}

session "stale_object_writer"
step "change_object_before_lock" { UPDATE jt_iso43.refs SET label='changed-before-lock' WHERE id=7; }

session "ddl_writer"
step "begin_ddl" { BEGIN; }
step "replace_rpc_source" {
  ALTER TABLE jt_iso43.rpc_root RENAME TO rpc_root_old;
  CREATE TABLE jt_iso43.rpc_root(LIKE jt_iso43.rpc_root_old INCLUDING ALL);
  INSERT INTO jt_iso43.rpc_root VALUES(1,'replacement');
}
step "commit_ddl" { COMMIT; }

session "rpc_deleter"
step "delete_during_replacement" {
  DO $body$ BEGIN
    PERFORM jt_iso43.rpc_documents__delete_document(
      1,(SELECT etag FROM jt_iso43.rpc_etag));
    INSERT INTO jt_iso43.results VALUES('PGJT-37/delete-rpc-source-identity','00000');
  EXCEPTION WHEN OTHERS THEN
    INSERT INTO jt_iso43.results VALUES('PGJT-37/delete-rpc-source-identity',SQLSTATE);
  END $body$;
}

session "child_owner"
step "begin_child_owner" { BEGIN; }
step "claim_child_key" { INSERT INTO jt_iso43.def_child VALUES(200,2,'claimed'); }
step "commit_child_owner" { COMMIT; }

session "deferrable_writer"
step "write_claimed_child" {
  DO $body$ DECLARE d jsonb; BEGIN
    SELECT data INTO d FROM jt_iso43.def_doc;
    d:=jsonb_set(d,ARRAY['value'],to_jsonb('must-roll-back'::text));
    d:=jsonb_set(d,ARRAY['items'],(d->'items')||jsonb_build_array(
      jsonb_build_object('id',200,'value','candidate')));
    UPDATE jt_iso43.def_documents SET data=d WHERE _id=1;
    INSERT INTO jt_iso43.results VALUES('PGJT-38/deferrable-child-key-race','00000');
  EXCEPTION WHEN OTHERS THEN
    INSERT INTO jt_iso43.results VALUES('PGJT-38/deferrable-child-key-race',SQLSTATE);
  END $body$;
}

session "verifier"
step "verify_current_object" {
  SELECT 'PGJT-34/current-object-lock' test_id,
    (SELECT label FROM jt_iso43.refs WHERE id=7) value;
}
step "verify_new_object" {
  SELECT 'PGJT-34/new-object-lock' test_id,
    (SELECT label FROM jt_iso43.refs WHERE id=8) value;
}
step "verify_stale_object" {
  SELECT test_id,sqlstate,
    (SELECT label FROM jt_iso43.roots WHERE id=2) root_value,
    (SELECT label FROM jt_iso43.refs WHERE id=7) object_value
  FROM jt_iso43.results WHERE test_id='PGJT-34/stale-object-etag';
}
step "verify_rpc_identity" {
  SELECT test_id,sqlstate,
    (SELECT count(*) FROM jt_iso43.rpc_root_old) old_rows,
    (SELECT count(*) FROM jt_iso43.rpc_root) replacement_rows
  FROM jt_iso43.results WHERE test_id='PGJT-37/delete-rpc-source-identity';
}
step "verify_deferrable" {
  SELECT test_id,sqlstate,
    (SELECT value FROM jt_iso43.def_root WHERE id=1) root_value,
    (SELECT count(*) FROM jt_iso43.def_child WHERE parent=1) own_children,
    (SELECT parent FROM jt_iso43.def_child WHERE id=200) claimed_parent
  FROM jt_iso43.results WHERE test_id='PGJT-38/deferrable-child-key-race';
}

permutation "begin_current" "write_current" "update_current_object" "commit_current" "verify_current_object"
permutation "begin_new" "write_new" "update_new_object" "commit_new" "verify_new_object"
permutation "begin_root_blocker" "lock_root" "write_stale_object" "change_object_before_lock" "commit_root_blocker" "verify_stale_object"
permutation "begin_ddl" "replace_rpc_source" "delete_during_replacement" "commit_ddl" "verify_rpc_identity"
permutation "begin_child_owner" "claim_child_key" "write_claimed_child" "commit_child_owner" "verify_deferrable"
