# 15. RLS and visibility

## Goal

Rehearse tenant isolation across root, referenced object and child rows.

## Prerequisites

A disposable database with extension installed; deployment owner may create
roles and SET ROLE. The names below must not already exist. Use a fresh
`psql -X -v ON_ERROR_STOP=1` session. Customer clients receive no database
credentials or arbitrary-SQL interface. Tenant identity here is a database role,
not an untrusted client-set session variable.

## Steps

This isolated transaction creates two nonowner, non-superuser NOBYPASSRLS
roles and rolls them back. Source privileges remain necessary for invoker
execution; RLS controls rows, while the HTTP/network boundary controls which
interfaces clients can reach.
For each tenant, the checklist is: point document read, projected array read,
INSERT with a visible object key, full replacement, rejected cross-tenant
UPDATE/DELETE and explicit-argument RPC delete. Run them under the actual
nonowner application role, not merely an owner with a tenant session variable.

```sql
BEGIN;
CREATE ROLE pgjt_recipe_tenant_a NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE pgjt_recipe_tenant_b NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE SCHEMA recipe_rls;
CREATE TABLE recipe_rls.customers(id bigint PRIMARY KEY,tenant name NOT NULL,name text NOT NULL);
CREATE TABLE recipe_rls.orders(id bigint PRIMARY KEY,tenant name NOT NULL DEFAULT current_user,
 customer_id bigint REFERENCES recipe_rls.customers(id),status text NOT NULL);
CREATE TABLE recipe_rls.items(id bigint PRIMARY KEY,parent bigint NOT NULL REFERENCES recipe_rls.orders(id),
 tenant name NOT NULL DEFAULT current_user,label text NOT NULL);
CREATE INDEX items_parent_id ON recipe_rls.items(parent,id);
INSERT INTO recipe_rls.customers VALUES
 (1,'pgjt_recipe_tenant_a','A'),(2,'pgjt_recipe_tenant_b','B');
INSERT INTO recipe_rls.orders VALUES
 (1,'pgjt_recipe_tenant_a',1,'A'),(2,'pgjt_recipe_tenant_b',2,'B');
INSERT INTO recipe_rls.items VALUES
 (11,1,'pgjt_recipe_tenant_a','A'),(22,2,'pgjt_recipe_tenant_b','B');
ALTER TABLE recipe_rls.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_rls.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE recipe_rls.items ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant ON recipe_rls.customers USING (tenant=current_user) WITH CHECK (tenant=current_user);
CREATE POLICY tenant ON recipe_rls.orders USING (tenant=current_user) WITH CHECK (tenant=current_user);
CREATE POLICY tenant ON recipe_rls.items USING (tenant=current_user) WITH CHECK (tenant=current_user);
-- Executes immediately, returns void and displays no DDL. This disposable
-- rehearsal is not pilot deployment; retain/review exact SQL first for pilots.
SELECT jsontailor.create_duality_view('recipe_rls.documents',
'{"root":{"table":"recipe_rls.orders","key":"id","fields":{"status":"status"},"insert":true,"update":true,"delete":true},
"object":{"name":"customer","table":"recipe_rls.customers","key":"id","root_fk":"customer_id","fields":{"id":"id","name":"name"},"etag":false},
"array":{"name":"items","table":"recipe_rls.items","key":"id","parent_fk":"parent","order_by":"id","fields":{"id":"id","label":"label"},"insert":true,"update":true,"delete":true}}'::jsonb);
GRANT USAGE ON SCHEMA recipe_rls TO pgjt_recipe_tenant_a,pgjt_recipe_tenant_b;
GRANT SELECT,INSERT,UPDATE,DELETE ON recipe_rls.orders,recipe_rls.items,recipe_rls.documents
 TO pgjt_recipe_tenant_a,pgjt_recipe_tenant_b;
GRANT SELECT ON recipe_rls.customers TO pgjt_recipe_tenant_a,pgjt_recipe_tenant_b;
SELECT jsontailor.grant_array_reader('recipe_rls.documents','pgjt_recipe_tenant_a');
SELECT jsontailor.grant_array_reader('recipe_rls.documents','pgjt_recipe_tenant_b');
SELECT jsontailor.grant_delete_rpc('recipe_rls.documents','pgjt_recipe_tenant_a');
SELECT jsontailor.grant_delete_rpc('recipe_rls.documents','pgjt_recipe_tenant_b');
SET LOCAL ROLE pgjt_recipe_tenant_a;
SELECT count(*)=1 AND min(_id)=1 AS tenant_a_visible FROM recipe_rls.documents;
SELECT count(*)=1 AS tenant_a_customer FROM recipe_rls.customers;
SELECT count(*)=1 AS tenant_a_items FROM recipe_rls.items;
SELECT data#>>'{customer,id}'='1' AND data#>>'{items,0,id}'='11'
 AND jsonb_array_length(data->'items')=1 AS tenant_a_document
FROM recipe_rls.documents WHERE _id=1;
UPDATE recipe_rls.documents SET data=jsonb_set(data,'{status}','"A-updated"'::jsonb) WHERE _id=1;
UPDATE recipe_rls.documents SET data=jsonb_set(data,'{status}','"forbidden"'::jsonb) WHERE _id=2;
DELETE FROM recipe_rls.documents WHERE _id=2;
INSERT INTO recipe_rls.documents(data) VALUES
 ('{"_id":3,"status":"A-new","customer":{"id":1},"items":[{"id":33,"label":"A-new"}]}'::jsonb);
SELECT recipe_rls.documents__delete_document(3,
 (SELECT data#>>'{_metadata,etag}' FROM recipe_rls.documents WHERE _id=3))->>'status'='A-new'
 AS tenant_a_rpc_deleted;
SET LOCAL ROLE pgjt_recipe_tenant_b;
SELECT count(*)=1 AND min(_id)=2 AS tenant_b_visible FROM recipe_rls.documents;
SELECT data->>'status'='B' AS tenant_b_unchanged FROM recipe_rls.documents WHERE _id=2;
SELECT data#>>'{customer,id}'='2' AND data#>>'{items,0,id}'='22'
 AND jsonb_array_length(data->'items')=1 AS tenant_b_document
FROM recipe_rls.documents WHERE _id=2;
UPDATE recipe_rls.documents SET data=jsonb_set(data,'{status}','"B-updated"'::jsonb) WHERE _id=2;
UPDATE recipe_rls.documents SET data=jsonb_set(data,'{status}','"forbidden"'::jsonb) WHERE _id=1;
DELETE FROM recipe_rls.documents WHERE _id=1;
INSERT INTO recipe_rls.documents(data) VALUES
 ('{"_id":4,"status":"B-new","customer":{"id":2},"items":[{"id":44,"label":"B-new"}]}'::jsonb);
SELECT recipe_rls.documents__delete_document(4,
 (SELECT data#>>'{_metadata,etag}' FROM recipe_rls.documents WHERE _id=4))->>'status'='B-new'
 AS tenant_b_rpc_deleted;
RESET ROLE;
SELECT status='A-updated' AS a_preserved FROM recipe_rls.orders WHERE id=1;
SELECT status='B-updated' AS b_preserved FROM recipe_rls.orders WHERE id=2;
SELECT count(*)=2 AS children_preserved FROM recipe_rls.items;
ROLLBACK;
```

Run the security matrix's cross-tenant object-reference INSERT/replacement,
hidden-child and atomic rollback cases as additional acceptance. A root-only
policy is insufficient; schema design must also prevent cross-tenant references.
Test direct source and arbitrary-SQL HTTP routes separately using recipe 10:
404 without forwarding. RLS tests alone do not prove network isolation.
Documents and ETags are visibility-relative: they cover only rows visible to
the current role and policies, not a global privileged snapshot. Hidden children
can be absent from a document and roles can see different ETags. Re-test after
policy changes; never treat an ETag as authorisation. If a row is visible but
RLS prevents its required write lock, `42501 row-level security prevents locking root`
means the SELECT and write-policy/lock permissions disagree; repair the reviewed
policy, not the invoker execution model.

## Expected result

Tenant visibility checks are true, authorised UPDATEs affect one row,
cross-tenant UPDATE/DELETE affect zero, and both RPC delete checks are true.
Both tenant status checks and child-count preservation are true.
Required source grants remain present; no SECURITY DEFINER bypass is introduced.

## Stop, common errors and recovery

Any cross-tenant visibility/mutation, missing positive control or unexpected
error means NO-GO. ROLLBACK removes the entire disposable fixture/roles.
Never fix a failed test by disabling RLS or revoking necessary invoker grants.
For real deployments stop ingress and restore reviewed policies/grants.

## Evidence and reference

Retain actual role attributes, policy definitions, source-state before/after,
positive/negative results and network evidence privately. See
[application role](05-application-role.md), [security model](../SECURITY_MODEL.md)
and [pilot acceptance](14-pilot-acceptance.md).

