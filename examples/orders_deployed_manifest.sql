-- Read-only observed catalogue evidence for the complete shipped orders mapping.
-- Run as owner: psql -XqAt -v ON_ERROR_STOP=1 -f this-file > private.json
-- Not a desired-state generator, deployment script or automatic drift verdict.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH
view_ids(name) AS (VALUES ('api.orders_dv'),('api.orders_dv__root_binding'),
 ('api.orders_dv__object_binding'),('api.orders_dv__array_binding')),
function_ids(signature) AS (VALUES ('api.orders_dv__bounded_array(bigint)'),
 ('api.orders_dv__insert()'),('api.orders_dv__update()'),('api.orders_dv__delete()'),
 ('api.orders_dv__guard()'),('api.orders_dv__check()'),
 ('api.orders_dv__delete_document(bigint,text)')),
views AS (
 SELECT i.name,c.oid,c.relowner,c.relacl,
 jsonb_build_object('identity',i.name,'definition',pg_get_viewdef(c.oid,true),
 'kind',c.relkind,'options',c.reloptions,'owner',pg_get_userbyid(c.relowner),
 'raw_acl',c.relacl,'effective_acl',
 (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.grantor,a.grantee,a.privilege_type)
  FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a),
 'columns',(SELECT jsonb_agg(jsonb_build_object('name',a.attname,
 'raw_acl',a.attacl,'effective_acl',(SELECT jsonb_agg(to_jsonb(x)
 ORDER BY x.grantor,x.grantee,x.privilege_type)
 FROM aclexplode(a.attacl) x),
 'comment',col_description(c.oid,a.attnum)) ORDER BY a.attnum)
 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'comment',obj_description(c.oid,'pg_class')) AS state
 FROM view_ids i LEFT JOIN pg_class c ON c.oid=to_regclass(i.name)),
functions AS (
 SELECT i.signature,p.oid,jsonb_build_object('identity',i.signature,
 'definition',pg_get_functiondef(p.oid),'owner',pg_get_userbyid(p.proowner),
 'security_definer',p.prosecdef,'config',p.proconfig,'raw_acl',p.proacl,
 'effective_acl',(SELECT jsonb_agg(to_jsonb(a)
 ORDER BY a.grantor,a.grantee,a.privilege_type)
 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a),
 'comment',obj_description(p.oid,'pg_proc')) AS state
 FROM function_ids i LEFT JOIN pg_proc p ON p.oid=to_regprocedure(i.signature)),
triggers AS (
 SELECT t.tgname,jsonb_build_object('name',t.tgname,
 'definition',pg_get_triggerdef(t.oid,true),'enabled',t.tgenabled,
 'function',t.tgfoid::regprocedure::text,
 'comment',obj_description(t.oid,'pg_trigger')) AS state
 FROM pg_trigger t WHERE t.tgrelid='api.orders_dv'::regclass AND NOT t.tgisinternal),
sources AS (
 SELECT c.oid,jsonb_build_object('identity',c.oid::regclass::text,
 'owner',pg_get_userbyid(c.relowner),'rls',c.relrowsecurity,
 'force_rls',c.relforcerowsecurity,'raw_acl',c.relacl,
 'effective_acl',(SELECT jsonb_agg(to_jsonb(a)
 ORDER BY a.grantor,a.grantee,a.privilege_type)
 FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a),
 'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.policyname)
 FROM pg_policies p WHERE p.schemaname=n.nspname AND p.tablename=c.relname)) AS state
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.oid IN ('shop.orders'::regclass,'shop.customers'::regclass,
 'shop.order_items'::regclass))
SELECT jsonb_build_object(
 'server_version',version(),'database',current_database(),'captured_by',current_user,
 'mapping','api.orders_dv','expected_counts',jsonb_build_object('views',4,'functions',7,'triggers',6),
 'observed_counts',jsonb_build_object('views',(SELECT count(oid) FROM views),
 'functions',(SELECT count(oid) FROM functions),'triggers',(SELECT count(*) FROM triggers)),
 'views',(SELECT jsonb_agg(state ORDER BY name) FROM views),
 'functions',(SELECT jsonb_agg(state ORDER BY signature) FROM functions),
 'triggers',(SELECT jsonb_agg(state ORDER BY tgname) FROM triggers),
 'sources',(SELECT jsonb_agg(state ORDER BY oid::regclass::text) FROM sources),
 'health',api.orders_dv__check());
COMMIT;

