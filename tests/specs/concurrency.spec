setup { CREATE TABLE jt_iso_probe(id integer PRIMARY KEY, value integer NOT NULL); INSERT INTO jt_iso_probe VALUES (1,0); }
teardown { DROP TABLE jt_iso_probe; }

session "writer"
step "begin_writer" { BEGIN; }
step "write_row" { UPDATE jt_iso_probe SET value=1 WHERE id=1; }
step "commit_writer" { COMMIT; }

session "waiter"
step "begin_waiter" { BEGIN; }
step "wait_row" { UPDATE jt_iso_probe SET value=2 WHERE id=1; }
step "commit_waiter" { COMMIT; }

permutation "begin_writer" "write_row" "begin_waiter" "wait_row" "commit_writer" "commit_waiter"
