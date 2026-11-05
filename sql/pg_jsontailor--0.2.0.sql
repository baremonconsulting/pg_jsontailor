\echo Use "CREATE EXTENSION pg_jsontailor" to load this file. \quit

SET LOCAL search_path = pg_catalog, pg_temp;

CREATE FUNCTION jsontailor.duality_view_ddl(view_name text, spec jsonb)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $generator$
DECLARE
    vp text[]; tp text[]; s text; j jsonb; k text; r record;
    cfg jsonb; rel oid; kind "char"; att smallint; typ text; ident "char"; generated "char";
    root_rel oid; object_rel oid; array_rel oid;
    fk_oid oid; fk_enforced boolean;
    root_pk smallint; object_pk smallint; array_pk smallint;
    root_schema text; root_table text; object_schema text; object_table text; array_schema text; array_table text;
    qview text; qrel text; qbinding text; qinsert text; qupdate text; qdelete text; qdelete_document text;
    root_pairs text := ''; object_pairs text := ''; array_pairs text := '';
    object_expr text; object_lookup text; array_expr text; order_expr text; payload text; etag_payload text;
    object_key_field text; array_key_field text;
    body text; delete_body text; ddl text; binding_ddl text := ''; names text[]; binding_columns text[]; binding_ordinals integer[]; binding_pk_name text; relation_checks text := ''; root_key_type text; root_touch_column text;
BEGIN
    IF view_name IS NULL OR spec IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='view_name and spec must not be SQL NULL';
    END IF;
    BEGIN vp:=parse_ident(view_name,true); EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='view_name must be a qualified schema.name';
    END;
    IF cardinality(vp)<>2 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='view_name must have exactly two parts'; END IF;
    IF octet_length(vp[2]||'__insert')>63 OR octet_length(vp[2]||'__update')>63 OR octet_length(vp[2]||'__delete')>63 OR octet_length(vp[2]||'__delete_document')>63 THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='generated object name exceeds 63 bytes';
    END IF;
    IF jsonb_typeof(spec) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='spec must be an object'; END IF;
    SELECT x INTO k FROM jsonb_object_keys(spec)x WHERE x<>ALL(ARRAY['root','object','array']) LIMIT 1;
    IF k IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('unknown specification key %s',k); END IF;
    IF jsonb_typeof(spec->'root') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='root must be an object'; END IF;
    IF spec?'object' AND jsonb_typeof(spec->'object') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='object must be an object'; END IF;
    IF spec?'array' AND jsonb_typeof(spec->'array') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array must be an object'; END IF;

    FOR s,j IN SELECT key,value FROM jsonb_each(spec) LOOP
        IF s='root' THEN names:=ARRAY['table','key','fields','insert','update','delete'];
        ELSIF s='object' THEN names:=ARRAY['name','table','key','root_fk','fields','etag'];
        ELSE names:=ARRAY['name','table','key','parent_fk','order_by','fields','insert','update','delete']; END IF;
        SELECT x INTO k FROM jsonb_object_keys(j)x WHERE x<>ALL(names) LIMIT 1;
        IF k IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('unknown key %s.%s',s,k); END IF;
        names:=CASE s WHEN 'root' THEN ARRAY['table','key','fields'] WHEN 'object' THEN ARRAY['name','table','key','root_fk','fields'] ELSE ARRAY['name','table','key','parent_fk','fields'] END;
        SELECT x INTO k FROM unnest(names)x WHERE NOT j?x LIMIT 1;
        IF k IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('missing key %s.%s',s,k); END IF;
        IF jsonb_typeof(j->'fields') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.fields must be an object',s); END IF;
        FOR k IN SELECT unnest(ARRAY['table','key','name','root_fk','parent_fk','order_by']) LOOP
            IF j?k AND jsonb_typeof(j->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.%s must be a string',s,k); END IF;
        END LOOP;
        FOR k IN SELECT unnest(ARRAY['insert','update','delete','etag']) LOOP
            IF j?k AND jsonb_typeof(j->k) IS DISTINCT FROM 'boolean' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.%s must be boolean',s,k); END IF;
        END LOOP;
        FOR r IN SELECT key,value FROM jsonb_each(j->'fields') LOOP
            IF r.key!~'^[A-Za-z][A-Za-z0-9_]{0,62}$' OR jsonb_typeof(r.value) IS DISTINCT FROM 'string' THEN
                RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('invalid mapping %s.fields.%s',s,r.key);
            END IF;
        END LOOP;
    END LOOP;

    cfg:=jsonb_set(spec,'{root,insert}',coalesce(spec#>'{root,insert}','false'::jsonb),true);
    cfg:=jsonb_set(cfg,'{root,update}',coalesce(cfg#>'{root,update}','false'::jsonb),true);
    cfg:=jsonb_set(cfg,'{root,delete}',coalesce(cfg#>'{root,delete}','false'::jsonb),true);
    IF cfg?'object' THEN cfg:=jsonb_set(cfg,'{object,etag}',coalesce(cfg#>'{object,etag}','true'::jsonb),true); END IF;
    IF cfg?'array' THEN
        cfg:=jsonb_set(cfg,'{array,insert}',coalesce(cfg#>'{array,insert}','false'::jsonb),true);
        cfg:=jsonb_set(cfg,'{array,update}',coalesce(cfg#>'{array,update}','false'::jsonb),true);
        cfg:=jsonb_set(cfg,'{array,delete}',coalesce(cfg#>'{array,delete}','false'::jsonb),true);
        cfg:=jsonb_set(cfg,'{array,order_by}',coalesce(cfg#>'{array,order_by}',to_jsonb(cfg#>>'{array,key}')),true);
    END IF;

    FOR s IN SELECT unnest(ARRAY['root','object','array']) LOOP
        IF NOT cfg?s THEN CONTINUE; END IF; j:=cfg->s;
        BEGIN tp:=parse_ident(j->>'table',true); EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.table must be schema.table',s); END;
        IF cardinality(tp)<>2 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.table must have two parts',s); END IF;
        SELECT c.oid,c.relkind INTO rel,kind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=tp[1] AND c.relname=tp[2];
        IF rel IS NULL OR kind<>ALL(ARRAY['r'::"char",'p'::"char"]) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.table is not an ordinary or partitioned table',s); END IF;
        SELECT a.attnum,a.attidentity,a.attgenerated,t.typname INTO att,ident,generated,typ FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE a.attrelid=rel AND a.attname=j->>'key' AND a.attnum>0 AND NOT a.attisdropped AND n.nspname='pg_catalog';
        IF att IS NULL OR generated<>'' OR typ<>ALL(ARRAY['text','varchar','bpchar','int2','int4','int8','numeric','bool','date','timestamp','uuid']) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('invalid or unsupported %s.key',s); END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=rel AND contype='p' AND conkey=ARRAY[att]::smallint[]) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s.key is not a single-column primary key',s); END IF;
        FOR r IN SELECT key,value#>>'{}' value FROM jsonb_each(j->'fields') LOOP
            SELECT a.attnum,a.attidentity,a.attgenerated,t.typname INTO att,ident,generated,typ FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE a.attrelid=rel AND a.attname=r.value AND a.attnum>0 AND NOT a.attisdropped AND n.nspname='pg_catalog';
            IF att IS NULL OR generated<>'' OR typ<>ALL(ARRAY['text','varchar','bpchar','int2','int4','int8','numeric','bool','date','timestamp','uuid']) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('invalid or unsupported %s.fields.%s',s,r.key); END IF;
            IF ident='a' AND (s='root' OR (s='array' AND r.value<>j->>'key')) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('GENERATED ALWAYS identity is not writable at %s.fields.%s',s,r.key); END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM jsonb_each_text(j->'fields') GROUP BY value HAVING count(*)>1) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('%s maps a column twice',s); END IF;
        IF s='root' THEN root_rel:=rel;root_schema:=tp[1];root_table:=tp[2];SELECT a.attnum,format_type(a.atttypid,a.atttypmod) INTO root_pk,root_key_type FROM pg_attribute a WHERE a.attrelid=rel AND a.attname=j->>'key' AND a.attnum>0 AND NOT a.attisdropped;
        ELSIF s='object' THEN object_rel:=rel;object_pk:=att;object_schema:=tp[1];object_table:=tp[2];
        ELSE array_rel:=rel;array_pk:=att;array_schema:=tp[1];array_table:=tp[2]; END IF;
    END LOOP;

    SELECT a.attnum,a.attidentity INTO root_pk,ident FROM pg_attribute a WHERE a.attrelid=root_rel AND a.attname=cfg#>>'{root,key}' AND a.attnum>0 AND NOT a.attisdropped;
    IF NOT EXISTS(SELECT 1 FROM jsonb_object_keys(cfg#>'{root,fields}')) AND NOT cfg?'object' AND ident='a' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='root has no column that can be rewritten';END IF;
    IF EXISTS(SELECT 1 FROM jsonb_each_text(cfg#>'{root,fields}') WHERE value IN (cfg#>>'{root,key}',cfg#>>'{object,root_fk}')) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='root.fields contains a reserved key or root_fk'; END IF;
    IF cfg?'object' THEN
        SELECT key INTO object_key_field FROM jsonb_each_text(cfg#>'{object,fields}') WHERE value=cfg#>>'{object,key}' LIMIT 1;
        IF object_key_field IS NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='object.key must be mapped'; END IF;
        SELECT attnum,attidentity,attgenerated INTO att,ident,generated FROM pg_attribute WHERE attrelid=root_rel AND attname=cfg#>>'{object,root_fk}' AND attnum>0 AND NOT attisdropped;
        IF att IS NULL OR ident='a' OR generated<>'' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='object.root_fk is not writable'; END IF;
        SELECT a.attnum INTO object_pk FROM pg_attribute a WHERE a.attrelid=object_rel AND a.attname=cfg#>>'{object,key}' AND a.attnum>0 AND NOT a.attisdropped;
        IF cfg#>>'{object,root_fk}'=cfg#>>'{root,key}' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='object.root_fk must not be the root primary key'; END IF;
        SELECT oid INTO fk_oid FROM pg_constraint WHERE conrelid=root_rel AND confrelid=object_rel AND contype='f' AND conkey=ARRAY[att]::smallint[] AND confkey=ARRAY[object_pk]::smallint[] AND NOT condeferrable LIMIT 1;
        IF fk_oid IS NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='object.root_fk requires an enforced non-deferrable foreign key'; END IF;
        IF current_setting('server_version_num')::integer>=180000 THEN
            EXECUTE 'SELECT conenforced FROM pg_catalog.pg_constraint WHERE oid=$1' INTO fk_enforced USING fk_oid;
            IF NOT fk_enforced THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='object.root_fk requires an enforced non-deferrable foreign key'; END IF;
        END IF;
    END IF;
    IF cfg?'array' THEN
        SELECT key INTO array_key_field FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,key}' LIMIT 1;
        IF array_key_field IS NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array.key must be mapped'; END IF;
        IF EXISTS(SELECT 1 FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,parent_fk}') THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array.fields contains parent_fk'; END IF;
        SELECT attnum,attidentity,attgenerated INTO att,ident,generated FROM pg_attribute WHERE attrelid=array_rel AND attname=cfg#>>'{array,parent_fk}' AND attnum>0 AND NOT attisdropped;
        SELECT a.attnum INTO array_pk FROM pg_attribute a WHERE a.attrelid=array_rel AND a.attname=cfg#>>'{array,key}' AND a.attnum>0 AND NOT a.attisdropped;
        IF att IS NULL OR ident='a' OR generated<>'' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array.parent_fk is not writable'; END IF;
        SELECT oid INTO fk_oid FROM pg_constraint WHERE conrelid=array_rel AND confrelid=root_rel AND contype='f' AND conkey=ARRAY[att]::smallint[] AND confkey=ARRAY[root_pk]::smallint[] AND NOT condeferrable AND confdeltype IN ('a','r') LIMIT 1;
        IF fk_oid IS NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array.parent_fk requires an enforced non-deferrable NO ACTION/RESTRICT foreign key'; END IF;
        IF current_setting('server_version_num')::integer>=180000 THEN
            EXECUTE 'SELECT conenforced FROM pg_catalog.pg_constraint WHERE oid=$1' INTO fk_enforced USING fk_oid;
            IF NOT fk_enforced THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array.parent_fk requires an enforced non-deferrable NO ACTION/RESTRICT foreign key'; END IF;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE a.attrelid=array_rel AND a.attname=cfg#>>'{array,order_by}' AND a.attnum>0 AND NOT a.attisdropped AND a.attgenerated='' AND n.nspname='pg_catalog' AND t.typname=ANY(ARRAY['text','varchar','bpchar','int2','int4','int8','numeric','bool','date','timestamp','uuid'])) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='array.order_by is invalid or unsupported'; END IF;
    END IF;

    names:=ARRAY(SELECT key FROM jsonb_each(cfg#>'{root,fields}'));
    IF cfg?'object' THEN names:=array_append(names,cfg#>>'{object,name}'); END IF;
    IF cfg?'array' THEN names:=array_append(names,cfg#>>'{array,name}'); END IF;
    FOREACH k IN ARRAY names LOOP IF k!~'^[A-Za-z][A-Za-z0-9_]{0,62}$' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE=format('invalid document field %s',k); END IF; END LOOP;
    IF cardinality(names)<>(SELECT count(DISTINCT x) FROM unnest(names)x) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='duplicate document-level field'; END IF;

    SELECT value INTO root_touch_column FROM jsonb_each_text(cfg#>'{root,fields}') ORDER BY key LIMIT 1;
    root_touch_column:=coalesce(root_touch_column,cfg#>>'{object,root_fk}',cfg#>>'{root,key}');

    FOR r IN SELECT key,value#>>'{}' value FROM jsonb_each(cfg#>'{root,fields}') LOOP root_pairs:=root_pairs||format(',%L,r.%I',r.key,r.value); END LOOP;
    IF cfg?'object' THEN
        FOR r IN SELECT key,value#>>'{}' value FROM jsonb_each(cfg#>'{object,fields}') LOOP object_pairs:=object_pairs||CASE WHEN object_pairs='' THEN '' ELSE ',' END||format('%L,o.%I',r.key,r.value); END LOOP;
        object_expr:=format('(SELECT jsonb_build_object(%s) FROM %I.%I o WHERE o.%I=r.%I)',object_pairs,object_schema,object_table,cfg#>>'{object,key}',cfg#>>'{object,root_fk}');
        object_lookup:=format('SELECT jsonb_build_object(%s) FROM %I.%I o WHERE o.%I=(SELECT x.%I FROM jsonb_populate_record(NULL::%I.%I,jsonb_build_object(%L,$1))x)',object_pairs,object_schema,object_table,cfg#>>'{object,key}',cfg#>>'{object,key}',object_schema,object_table,cfg#>>'{object,key}');
        root_pairs:=root_pairs||format(',%L,%s',cfg#>>'{object,name}',object_expr);
    END IF;
    IF cfg?'array' THEN
        FOR r IN SELECT key,value#>>'{}' value FROM jsonb_each(cfg#>'{array,fields}') LOOP array_pairs:=array_pairs||CASE WHEN array_pairs='' THEN '' ELSE ',' END||format('%L,a.%I',r.key,r.value); END LOOP;
        SELECT t.typname INTO typ FROM pg_attribute x JOIN pg_type t ON t.oid=x.atttypid WHERE x.attrelid=array_rel AND x.attname=cfg#>>'{array,order_by}';
        order_expr:=format('a.%I%s',cfg#>>'{array,order_by}',CASE WHEN typ IN ('text','varchar','bpchar') THEN ' COLLATE "C"' ELSE '' END);
        SELECT t.typname INTO typ FROM pg_attribute x JOIN pg_type t ON t.oid=x.atttypid WHERE x.attrelid=array_rel AND x.attname=cfg#>>'{array,key}';
        order_expr:=order_expr||format(',a.%I%s',cfg#>>'{array,key}',CASE WHEN typ IN ('text','varchar','bpchar') THEN ' COLLATE "C"' ELSE '' END);
        array_expr:=format('coalesce((SELECT jsonb_agg(jsonb_build_object(%s) ORDER BY %s) FROM %I.%I a WHERE a.%I=r.%I),''[]''::jsonb)',array_pairs,order_expr,array_schema,array_table,cfg#>>'{array,parent_fk}',cfg#>>'{root,key}');
        root_pairs:=root_pairs||format(',%L,%s',cfg#>>'{array,name}',array_expr);
    END IF;
    payload:=format('jsonb_build_object(''_id'',r.%I%s)',cfg#>>'{root,key}',root_pairs);
    etag_payload:='d.payload';
    IF cfg?'object' AND NOT (cfg#>>'{object,etag}')::boolean THEN
        etag_payload:=format('jsonb_set(d.payload,ARRAY[%L],CASE WHEN jsonb_typeof(d.payload->%L)=''object'' THEN jsonb_build_object(%L,d.payload#>ARRAY[%L,%L]) ELSE ''null''::jsonb END)',cfg#>>'{object,name}',cfg#>>'{object,name}',object_key_field,cfg#>>'{object,name}',object_key_field);
    END IF;
    qview:=format('%I.%I',vp[1],vp[2]);qinsert:=format('%I.%I',vp[1],vp[2]||'__insert');qupdate:=format('%I.%I',vp[1],vp[2]||'__update');qdelete:=format('%I.%I',vp[1],vp[2]||'__delete');qdelete_document:=format('%I.%I',vp[1],vp[2]||'__delete_document');
    FOR s IN SELECT unnest(ARRAY['root','object','array']) LOOP
        IF NOT cfg?s THEN CONTINUE; END IF;
        tp:=parse_ident(cfg#>>ARRAY[s,'table']);
        qrel:=format('%I.%I',tp[1],tp[2]);
        qbinding:=format('%I.%I',vp[1],vp[2]||'__'||s||'_binding');
        binding_columns:=ARRAY[cfg#>>ARRAY[s,'key']]||ARRAY(SELECT value FROM jsonb_each_text(cfg#>ARRAY[s,'fields']));
        IF s='root' AND cfg?'object' THEN binding_columns:=array_append(binding_columns,cfg#>>'{object,root_fk}'); END IF;
        IF s='array' THEN binding_columns:=binding_columns||ARRAY[cfg#>>'{array,parent_fk}',cfg#>>'{array,order_by}']; END IF;
        binding_columns:=ARRAY(SELECT DISTINCT x FROM unnest(binding_columns)x);
        SELECT c.conname INTO binding_pk_name
        FROM pg_constraint c JOIN pg_attribute a
          ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
        WHERE c.conrelid=to_regclass(qrel) AND c.contype='p'
          AND cardinality(c.conkey)=1 AND a.attname=cfg#>>ARRAY[s,'key'];
        SELECT array_agg(x.ordinal ORDER BY x.name) INTO binding_ordinals
        FROM (
            SELECT c.name,
                   (SELECT count(*) FROM pg_attribute b
                    WHERE b.attrelid=to_regclass(qrel) AND b.attnum>0
                      AND NOT b.attisdropped AND b.attnum<=a.attnum)::integer AS ordinal
            FROM unnest(binding_columns)c(name)
            JOIN pg_attribute a ON a.attrelid=to_regclass(qrel)
                               AND a.attname=c.name AND a.attnum>0 AND NOT a.attisdropped
        )x;
        binding_columns:=ARRAY(SELECT x FROM unnest(binding_columns)x ORDER BY x);
        binding_ddl:=binding_ddl||format('CREATE VIEW %s WITH (security_invoker=true) AS SELECT %s FROM %s binding_source WHERE false OFFSET 0;',qbinding,(SELECT string_agg(format('binding_source.%I',x),',') FROM unnest(binding_columns)x),qrel)||E'\n';
        relation_checks:=relation_checks||format('IF NOT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1] WHERE c.conrelid=to_regclass(%L) AND c.contype=''p'' AND cardinality(c.conkey)=1 AND c.conname=%L AND a.attname=%L) THEN RAISE EXCEPTION USING ERRCODE=''PT422'',MESSAGE=''source relation role changed; regenerate the duality view'';END IF;',qrel,binding_pk_name,cfg#>>ARRAY[s,'key']);
        relation_checks:=relation_checks||format('IF (SELECT count(*) FROM unnest(%L::text[],%L::integer[]) expected(attname,ordinal) JOIN LATERAL (SELECT a.attname,row_number() OVER (ORDER BY a.attnum)::integer ordinal,a.attnum FROM pg_attribute a WHERE a.attrelid=to_regclass(%L) AND a.attnum>0 AND NOT a.attisdropped) current USING(attname,ordinal) WHERE EXISTS(SELECT 1 FROM pg_rewrite rw JOIN pg_depend d ON d.classid=''pg_rewrite''::regclass AND d.objid=rw.oid AND d.refclassid=''pg_class''::regclass WHERE rw.ev_class=to_regclass(%L) AND d.refobjid=to_regclass(%L) AND d.refobjsubid=current.attnum))<>%s THEN RAISE EXCEPTION USING ERRCODE=''PT422'',MESSAGE=''source relation or column binding changed; regenerate the duality view'';END IF;',binding_columns::text,binding_ordinals::text,qrel,qbinding,qrel,cardinality(binding_columns));
    END LOOP;
    IF cfg?'object' THEN
        relation_checks:=relation_checks||format('IF NOT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_attribute fk ON fk.attrelid=c.conrelid AND fk.attnum=c.conkey[1] JOIN pg_attribute pk ON pk.attrelid=c.confrelid AND pk.attnum=c.confkey[1] WHERE c.conrelid=to_regclass(%L) AND c.confrelid=to_regclass(%L) AND c.contype=''f'' AND c.conkey=ARRAY[fk.attnum]::smallint[] AND c.confkey=ARRAY[pk.attnum]::smallint[] AND fk.attname=%L AND pk.attname=%L AND NOT c.condeferrable) THEN RAISE EXCEPTION USING ERRCODE=''PT422'',MESSAGE=''source relation role changed; regenerate the duality view'';END IF;',format('%I.%I',root_schema,root_table),format('%I.%I',object_schema,object_table),cfg#>>'{object,root_fk}',cfg#>>'{object,key}');
    END IF;
    IF cfg?'array' THEN
        relation_checks:=relation_checks||format('IF NOT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_attribute fk ON fk.attrelid=c.conrelid AND fk.attnum=c.conkey[1] JOIN pg_attribute pk ON pk.attrelid=c.confrelid AND pk.attnum=c.confkey[1] WHERE c.conrelid=to_regclass(%L) AND c.confrelid=to_regclass(%L) AND c.contype=''f'' AND c.conkey=ARRAY[fk.attnum]::smallint[] AND c.confkey=ARRAY[pk.attnum]::smallint[] AND fk.attname=%L AND pk.attname=%L AND NOT c.condeferrable AND c.confdeltype IN (''a'',''r'')) THEN RAISE EXCEPTION USING ERRCODE=''PT422'',MESSAGE=''source relation role changed; regenerate the duality view'';END IF;',format('%I.%I',array_schema,array_table),format('%I.%I',root_schema,root_table),cfg#>>'{array,parent_fk}',cfg#>>'{root,key}');
    END IF;
    ddl:=binding_ddl||format('CREATE VIEW %s WITH (security_invoker=true) AS SELECT r.%I AS _id,d.payload||jsonb_build_object(''_metadata'',jsonb_build_object(''etag'',upper(encode(substr(sha256(convert_to((%s)::text,''UTF8'')),1,16),''hex'')))) AS data FROM %I.%I r CROSS JOIN LATERAL (SELECT %s AS payload OFFSET 0)d;',qview,cfg#>>'{root,key}',etag_payload,root_schema,root_table,payload);

    body:=format($body$
DECLARE cfg constant jsonb:=%L::jsonb; doc jsonb; cur jsonb; el jsonb; oldel jsonb; bad text; unknown_field text; violation_schema text; violation_table text; violation_constraint text; idj jsonb; keyj jsonb; cols text[]; vals text[]; map jsonb; mapped jsonb; sets text; diff text; col_list text; select_list text; n int; expected int; new_explicit int:=0; locked_n int:=0; changed boolean:=false; all_full boolean:=false; root_id %I.%I.%I%%TYPE;
BEGIN
  %s
  IF TG_OP='INSERT' THEN
    IF NOT (cfg#>>'{root,insert}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='root insert is disabled'; END IF;
    doc:=NEW.data;
    IF jsonb_typeof(doc) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='document must be an object'; END IF;
    IF doc?'_id' AND doc->'_id'='null'::jsonb THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='_id must not be null'; END IF;
    IF NEW._id IS NOT NULL AND doc->'_id' IS DISTINCT FROM to_jsonb(NEW._id) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='_id differs'; END IF;
    SELECT x INTO bad FROM jsonb_object_keys(doc)x WHERE x<>ALL(ARRAY['_id','_metadata']||ARRAY(SELECT key FROM jsonb_each(cfg#>'{root,fields}'))||CASE WHEN cfg?'object' THEN ARRAY[cfg#>>'{object,name}'] ELSE ARRAY[]::text[] END||CASE WHEN cfg?'array' THEN ARRAY[cfg#>>'{array,name}'] ELSE ARRAY[]::text[] END) LIMIT 1;
    IF bad IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE=format('unknown field %%s',bad); END IF;
    IF doc?'_metadata' AND jsonb_typeof(doc->'_metadata') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='_metadata must be an object';END IF;
    IF cfg?'object' AND doc?(cfg#>>'{object,name}') THEN
      IF jsonb_typeof(doc->(cfg#>>'{object,name}')) NOT IN ('object','null') THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='object field has wrong type';END IF;
      IF jsonb_typeof(doc->(cfg#>>'{object,name}'))='object' THEN
        SELECT x INTO bad FROM jsonb_object_keys(doc->(cfg#>>'{object,name}'))x WHERE x<>ALL(ARRAY(SELECT key FROM jsonb_each(cfg#>'{object,fields}'))) LIMIT 1;
        IF bad IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE=format('unknown object field %%s',bad);END IF;
        SELECT key INTO bad FROM jsonb_each_text(cfg#>'{object,fields}') WHERE value=cfg#>>'{object,key}';
        IF NOT (doc->(cfg#>>'{object,name}'))?bad OR doc#>ARRAY[cfg#>>'{object,name}',bad]='null'::jsonb THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='object key is required';END IF;
      END IF;
    END IF;
    IF cfg?'array' AND doc?(cfg#>>'{array,name}') AND jsonb_typeof(doc->(cfg#>>'{array,name}')) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array field must be an array'; END IF;
    IF cfg?'array' AND jsonb_array_length(coalesce(doc->(cfg#>>'{array,name}'),'[]'))>0 AND NOT (cfg#>>'{array,insert}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array insert is disabled'; END IF;
    IF cfg?'array' THEN
      SELECT key INTO bad FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,key}';
      FOR el IN SELECT value FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]')) LOOP
        IF jsonb_typeof(el)<>'object' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array element must be an object';END IF;
        SELECT x INTO unknown_field FROM jsonb_object_keys(el)x WHERE x<>ALL(ARRAY(SELECT key FROM jsonb_each(cfg#>'{array,fields}'))) LIMIT 1;
        IF unknown_field IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE=format('unknown array field %%s',unknown_field);END IF;
        IF el?bad AND el->bad='null'::jsonb THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array key must not be null';END IF;
      END LOOP;
      IF (SELECT count(*) FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'))e WHERE e?bad)<>(SELECT count(DISTINCT e->bad) FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'))e WHERE e?bad) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='duplicate array key';END IF;
      SELECT coalesce(jsonb_agg(jsonb_build_object(cfg#>>'{array,key}',e.value->bad)),'[]'::jsonb) INTO mapped FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'))e WHERE e.value?bad;
      EXECUTE format('SELECT count(*)<>count(DISTINCT x.%%I) FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I IS NOT NULL',cfg#>>'{array,key}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}') INTO changed USING mapped;
      IF changed THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='duplicate typed array key';END IF;
    END IF;
    cols:=ARRAY[]::text[];map:='{}';
    IF doc?'_id' THEN cols:=array_append(cols,cfg#>>'{root,key}');map:=map||jsonb_build_object(cfg#>>'{root,key}',doc->'_id'); END IF;
    FOR bad,keyj IN SELECT key,value FROM jsonb_each(cfg#>'{root,fields}') LOOP IF doc?bad THEN cols:=array_append(cols,keyj#>>'{}');map:=map||jsonb_build_object(keyj#>>'{}',doc->bad); END IF; END LOOP;
    IF cfg?'object' AND doc?(cfg#>>'{object,name}') THEN cols:=array_append(cols,cfg#>>'{object,root_fk}');map:=map||jsonb_build_object(cfg#>>'{object,root_fk}',doc#>ARRAY[cfg#>>'{object,name}',%L]); END IF;
    IF cardinality(cols)=0 THEN EXECUTE format('INSERT INTO %%I.%%I DEFAULT VALUES RETURNING %%I',%L,%L,%L) INTO root_id;
    ELSE EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_record(NULL::%%I.%%I,$1) x RETURNING %%I',%L,%L,(SELECT string_agg(quote_ident(x),',') FROM unnest(cols)x),(SELECT string_agg('x.'||quote_ident(x),',') FROM unnest(cols)x),%L,%L,%L) USING map INTO root_id; END IF;
    idj:=to_jsonb(root_id);
    IF cfg?'array' THEN
      -- Validate element types before choosing the bulk or fallback path.
      IF EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'))e WHERE jsonb_typeof(e.value)<>'object') THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array element must be an object';END IF;
      SELECT key INTO bad FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,key}';
      -- A single recordset INSERT is safe when every non-key field is present.
      -- Otherwise retain the per-element path so omitted fields use defaults.
      all_full:=NOT EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'))e CROSS JOIN jsonb_each_text(cfg#>'{array,fields}')f WHERE f.value<>cfg#>>'{array,key}' AND NOT e.value?f.key);
      IF jsonb_array_length(coalesce(doc->(cfg#>>'{array,name}'),'[]'))=0 THEN
        NULL;
      ELSIF all_full AND jsonb_array_length(doc->(cfg#>>'{array,name}'))>1 THEN
        SELECT coalesce(jsonb_agg((SELECT coalesce(jsonb_object_agg(f.value,e.value->f.key),'{}'::jsonb) FROM jsonb_each_text(cfg#>'{array,fields}')f WHERE e.value?f.key)||jsonb_build_object(cfg#>>'{array,parent_fk}',idj)),'[]'::jsonb) INTO mapped FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'))e;
        SELECT count(*) FILTER(WHERE value?bad),count(*) FILTER(WHERE NOT value?bad) INTO n,expected FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]'));
        IF n>0 THEN
          SELECT format('%%I',cfg#>>'{array,parent_fk}')||','||string_agg(format('%%I',value),','),format('x.%%I',cfg#>>'{array,parent_fk}')||','||string_agg(format('x.%%I',value),',') INTO col_list,select_list FROM jsonb_each_text(cfg#>'{array,fields}');
          EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I IS NOT NULL',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],col_list,select_list,(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}') USING mapped;
        END IF;
        IF expected>0 THEN
          SELECT format('%%I',cfg#>>'{array,parent_fk}')||coalesce(','||string_agg(format('%%I',value),','),''),format('x.%%I',cfg#>>'{array,parent_fk}')||coalesce(','||string_agg(format('x.%%I',value),','),'') INTO col_list,select_list FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value<>cfg#>>'{array,key}';
          EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I IS NULL',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],col_list,select_list,(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}') USING mapped;
        END IF;
      ELSE
        FOR el IN SELECT value FROM jsonb_array_elements(coalesce(doc->(cfg#>>'{array,name}'),'[]')) LOOP
          cols:=ARRAY[cfg#>>'{array,parent_fk}'];map:=jsonb_build_object(cfg#>>'{array,parent_fk}',idj);
          FOR bad,keyj IN SELECT key,value FROM jsonb_each(cfg#>'{array,fields}') LOOP IF el?bad THEN cols:=array_append(cols,keyj#>>'{}');map:=map||jsonb_build_object(keyj#>>'{}',el->bad); END IF; END LOOP;
          EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_record(NULL::%%I.%%I,$1)x',%L,%L,(SELECT string_agg(quote_ident(x),',')FROM unnest(cols)x),(SELECT string_agg('x.'||quote_ident(x),',')FROM unnest(cols)x),%L,%L) USING map;
        END LOOP;
      END IF;
    END IF;
    EXECUTE format('SELECT _id,data FROM %%s WHERE _id=$1',%L) USING root_id INTO NEW._id,NEW.data;RETURN NEW;
  ELSIF TG_OP='UPDATE' THEN
    root_id:=OLD._id;doc:=NEW.data;IF NEW._id IS DISTINCT FROM OLD._id OR jsonb_typeof(doc)<>'object' OR doc->'_id' IS DISTINCT FROM to_jsonb(OLD._id) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='invalid document identity';END IF;
    SELECT x INTO bad FROM jsonb_object_keys(doc)x WHERE x<>ALL(ARRAY['_id','_metadata']||ARRAY(SELECT key FROM jsonb_each(cfg#>'{root,fields}'))||CASE WHEN cfg?'object' THEN ARRAY[cfg#>>'{object,name}'] ELSE ARRAY[]::text[] END||CASE WHEN cfg?'array' THEN ARRAY[cfg#>>'{array,name}'] ELSE ARRAY[]::text[] END) LIMIT 1;
    IF bad IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE=format('unknown field %%s',bad);END IF;
    SELECT x INTO bad FROM jsonb_object_keys(cfg#>'{root,fields}')x WHERE NOT doc?x LIMIT 1;
    IF bad IS NOT NULL OR (cfg?'object' AND NOT doc?(cfg#>>'{object,name}')) OR (cfg?'array' AND NOT doc?(cfg#>>'{array,name}')) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='document is incomplete';END IF;
    IF cfg?'object' THEN
      IF jsonb_typeof(doc->(cfg#>>'{object,name}')) NOT IN ('object','null') THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='object field has wrong type';END IF;
      IF jsonb_typeof(doc->(cfg#>>'{object,name}'))='object' THEN
        SELECT x INTO bad FROM jsonb_object_keys(doc->(cfg#>>'{object,name}'))x WHERE x<>ALL(ARRAY(SELECT key FROM jsonb_each(cfg#>'{object,fields}'))) LIMIT 1;
        IF bad IS NOT NULL OR NOT (doc->(cfg#>>'{object,name}'))?%L OR doc#>ARRAY[cfg#>>'{object,name}',%L]='null'::jsonb THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='invalid object field';END IF;
      END IF;
    END IF;
    IF cfg?'array' THEN
      SELECT key INTO bad FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,key}';
      IF jsonb_typeof(doc->(cfg#>>'{array,name}')) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array field must be an array';END IF;
      FOR el IN SELECT value FROM jsonb_array_elements(doc->(cfg#>>'{array,name}')) LOOP
        IF jsonb_typeof(el)<>'object' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array element must be an object';END IF;
        SELECT x INTO bad FROM jsonb_object_keys(el)x WHERE x<>ALL(ARRAY(SELECT key FROM jsonb_each(cfg#>'{array,fields}'))) LIMIT 1;
        IF bad IS NOT NULL OR (el?%L AND el->%L='null'::jsonb) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='invalid array element';END IF;
        IF el?%L AND EXISTS(SELECT 1 FROM jsonb_object_keys(cfg#>'{array,fields}')x WHERE x<>%L AND NOT el?x) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='existing array element is incomplete';END IF;
      END LOOP;
      IF (SELECT count(*) FROM jsonb_array_elements(doc->(cfg#>>'{array,name}'))e WHERE e?%L)<>(SELECT count(DISTINCT e->%L) FROM jsonb_array_elements(doc->(cfg#>>'{array,name}'))e WHERE e?%L) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='duplicate array key';END IF;
      SELECT key INTO bad FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,key}';
      SELECT coalesce(jsonb_agg(jsonb_build_object(cfg#>>'{array,key}',e.value->bad)),'[]'::jsonb) INTO mapped FROM jsonb_array_elements(doc->(cfg#>>'{array,name}'))e WHERE e.value?bad;
      EXECUTE format('SELECT count(*)<>count(DISTINCT x.%%I) FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I IS NOT NULL',cfg#>>'{array,key}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}') INTO changed USING mapped;
      IF changed THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='duplicate typed array key';END IF;
    END IF;
    IF doc?'_metadata' AND jsonb_typeof(doc->'_metadata') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='_metadata must be an object';END IF;
    IF jsonb_typeof(doc#>'{_metadata,etag}') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION USING ERRCODE='PT428',MESSAGE='etag is required';END IF;
    n:=NULL;EXECUTE format('SELECT 1 FROM %%I.%%I WHERE %%I=$1 FOR UPDATE',%L,%L,%L) INTO n USING root_id;
    IF n IS NULL THEN EXECUTE format('SELECT EXISTS(SELECT 1 FROM %%I.%%I WHERE %%I=$1)',%L,%L,%L) INTO changed USING root_id;IF changed THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking root';ELSE RAISE EXCEPTION USING ERRCODE='PT412',MESSAGE='document no longer exists';END IF;END IF;
    locked_n:=0;IF cfg?'array' AND jsonb_array_length(OLD.data->(cfg#>>'{array,name}'))>0 THEN EXECUTE format('SELECT count(*) FROM (SELECT %%I FROM %%I.%%I WHERE %%I=$1 ORDER BY %%I FOR UPDATE)s',%L,%L,%L,%L,%L) INTO locked_n USING root_id;END IF;
    EXECUTE format('SELECT data FROM %%s WHERE _id=$1',%L) USING root_id INTO cur;
    IF cur IS NULL OR cur#>>'{_metadata,etag}' IS DISTINCT FROM doc#>>'{_metadata,etag}' THEN RAISE EXCEPTION USING ERRCODE='PT412',MESSAGE='etag does not match';END IF;
    IF cfg?'object' AND (cfg#>>'{object,etag}')::boolean AND jsonb_typeof(doc->(cfg#>>'{object,name}'))='object' THEN EXECUTE %L INTO el USING doc#>ARRAY[cfg#>>'{object,name}',%L];IF el IS NOT NULL AND el IS DISTINCT FROM doc->(cfg#>>'{object,name}') THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='object fields do not match referenced row';END IF;END IF;
    IF (doc-'_metadata') IS DISTINCT FROM (cur-'_metadata') THEN
      IF NOT (cfg#>>'{root,update}')::boolean AND (EXISTS(SELECT 1 FROM jsonb_each(cfg#>'{root,fields}')f WHERE doc->f.key IS DISTINCT FROM cur->f.key) OR (cfg?'object' AND doc#>ARRAY[cfg#>>'{object,name}',%L] IS DISTINCT FROM cur#>ARRAY[cfg#>>'{object,name}',%L])) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='root update is disabled';END IF;
      cols:=ARRAY[]::text[];map:='{}';FOR bad,keyj IN SELECT key,value FROM jsonb_each(cfg#>'{root,fields}') LOOP cols:=array_append(cols,keyj#>>'{}');map:=map||jsonb_build_object(keyj#>>'{}',doc->bad);END LOOP;
      IF cfg?'object' AND doc->(cfg#>>'{object,name}') IS DISTINCT FROM cur->(cfg#>>'{object,name}') THEN cols:=array_append(cols,cfg#>>'{object,root_fk}');map:=map||jsonb_build_object(cfg#>>'{object,root_fk}',doc#>ARRAY[cfg#>>'{object,name}',%L]);END IF;
      IF cardinality(cols)>0 THEN EXECUTE format('UPDATE %%I.%%I t SET (%%s)=(SELECT %%s FROM jsonb_populate_record(NULL::%%I.%%I,$1)x) WHERE t.%%I=$2',%L,%L,(SELECT string_agg(quote_ident(x),',')FROM unnest(cols)x),(SELECT string_agg('x.'||quote_ident(x),',')FROM unnest(cols)x),%L,%L,%L) USING map,root_id;
      ELSE EXECUTE format('UPDATE %%I.%%I SET %%I=%%I WHERE %%I=$1',%L,%L,%L,%L,%L) USING root_id;END IF;
      GET DIAGNOSTICS n=ROW_COUNT;IF n<>1 THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='root update denied';END IF;
      -- Array reconciliation is key based and never rewrites unchanged elements.
      IF cfg?'array' THEN
        IF doc->(cfg#>>'{array,name}') IS DISTINCT FROM cur->(cfg#>>'{array,name}') AND locked_n<jsonb_array_length(cur->(cfg#>>'{array,name}')) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE=format('row-level security prevents locking changed array elements (locked %%s of %%s)',locked_n,jsonb_array_length(cur->(cfg#>>'{array,name}')));END IF;
        IF doc->(cfg#>>'{array,name}') IS DISTINCT FROM cur->(cfg#>>'{array,name}') THEN
        -- Convert the incoming array once to typed physical records.  Key
        -- membership and DML use database equality, including numeric keys.
        SELECT key INTO bad FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value=cfg#>>'{array,key}';
        SELECT coalesce(jsonb_agg((SELECT coalesce(jsonb_object_agg(f.value#>>'{}',e.value->f.key),'{}'::jsonb) FROM jsonb_each(cfg#>'{array,fields}')f WHERE e.value?f.key)||jsonb_build_object(cfg#>>'{array,parent_fk}',to_jsonb(root_id))),'[]'::jsonb) INTO mapped FROM jsonb_array_elements(doc->(cfg#>>'{array,name}'))e;
        EXECUTE format('SELECT coalesce(bool_or(t.%%I IS NOT NULL AND t.%%I IS DISTINCT FROM $2),false),count(*) FILTER(WHERE n.%%I IS NOT NULL AND t.%%I IS NULL) FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)n LEFT JOIN %%I.%%I t ON t.%%I=n.%%I',cfg#>>'{array,key}',cfg#>>'{array,parent_fk}',cfg#>>'{array,key}',cfg#>>'{array,key}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}',cfg#>>'{array,key}') INTO changed,new_explicit USING mapped,root_id;
        IF changed THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array key does not belong to document';END IF;
        IF new_explicit>0 AND NOT (cfg#>>'{array,insert}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array insert is disabled';END IF;
        IF new_explicit>0 AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=to_regclass(cfg#>>'{array,table}') AND a.attname=cfg#>>'{array,key}' AND a.attidentity='a' AND a.attnum>0 AND NOT a.attisdropped) THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='explicit value is not allowed for a GENERATED ALWAYS array key';END IF;

        EXECUTE format('SELECT count(*) FROM %%I.%%I t WHERE t.%%I=$2 AND NOT EXISTS(SELECT 1 FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)n WHERE n.%%I=t.%%I)',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,parent_fk}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}',cfg#>>'{array,key}') INTO expected USING mapped,root_id;
        IF expected>0 THEN
          IF NOT (cfg#>>'{array,delete}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array delete is disabled';END IF;
          EXECUTE format('DELETE FROM %%I.%%I t WHERE t.%%I=$2 AND NOT EXISTS(SELECT 1 FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I=t.%%I)',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,parent_fk}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}',cfg#>>'{array,key}') USING mapped,root_id;
          GET DIAGNOSTICS n=ROW_COUNT;IF n<>expected THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='array delete denied';END IF;
        END IF;

        SELECT string_agg(format('%%I=x.%%I',value,value),','),string_agg(format('t.%%I IS DISTINCT FROM x.%%I',value,value),' OR ') INTO sets,diff FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value<>cfg#>>'{array,key}';
        IF sets IS NOT NULL THEN
          EXECUTE format('SELECT count(*) FROM %%I.%%I t JOIN jsonb_populate_recordset(NULL::%%I.%%I,$1)x ON x.%%I=t.%%I WHERE t.%%I=$2 AND (%%s)',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}',cfg#>>'{array,key}',cfg#>>'{array,parent_fk}',diff) INTO expected USING mapped,root_id;
          IF expected>0 THEN
            IF NOT (cfg#>>'{array,update}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array update is disabled';END IF;
            EXECUTE format('UPDATE %%I.%%I t SET %%s FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE t.%%I=$2 AND t.%%I=x.%%I AND (%%s)',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],sets,(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,parent_fk}',cfg#>>'{array,key}',cfg#>>'{array,key}',diff) USING mapped,root_id;
            GET DIAGNOSTICS n=ROW_COUNT;IF n<>expected THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='array update denied';END IF;
          END IF;
        END IF;

        -- A supplied key may identify either an existing element or a new
        -- business-key element.  Insert only globally unused keys; a conflict
        -- after the ownership check is a concurrent ownership change.
        IF new_explicit>0 THEN
          SELECT format('%%I',cfg#>>'{array,parent_fk}')||','||string_agg(format('%%I',value),','),format('x.%%I',cfg#>>'{array,parent_fk}')||','||string_agg(format('x.%%I',value),',') INTO col_list,select_list FROM jsonb_each_text(cfg#>'{array,fields}');
          BEGIN
            EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I IS NOT NULL AND NOT EXISTS(SELECT 1 FROM %%I.%%I t WHERE t.%%I=x.%%I)',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],col_list,select_list,(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}',cfg#>>'{array,key}') USING mapped;
            GET DIAGNOSTICS n=ROW_COUNT;IF n<>new_explicit THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='array insert denied';END IF;
          EXCEPTION WHEN unique_violation THEN
            GET STACKED DIAGNOSTICS violation_schema=SCHEMA_NAME,violation_table=TABLE_NAME,violation_constraint=CONSTRAINT_NAME;
            IF EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_class rel ON rel.oid=c.conrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1] WHERE (c.conrelid=to_regclass(cfg#>>'{array,table}') OR c.conrelid IN (SELECT tree.relid FROM pg_partition_tree(to_regclass(cfg#>>'{array,table}')) tree)) AND c.contype='p' AND cardinality(c.conkey)=1 AND c.conname=violation_constraint AND ns.nspname=violation_schema AND rel.relname=violation_table AND a.attname=cfg#>>'{array,key}') THEN
              RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array key does not belong to document';
            ELSE
              RAISE;
            END IF;
          END;
        END IF;

        -- New elements have no key.  Bulk-insert complete groups, while one
        -- element or a partial shape keeps the default-preserving fallback.
        SELECT count(*),NOT EXISTS(SELECT 1 FROM jsonb_array_elements(doc->(cfg#>>'{array,name}'))e CROSS JOIN jsonb_each_text(cfg#>'{array,fields}')f WHERE NOT e.value?bad AND f.value<>cfg#>>'{array,key}' AND NOT e.value?f.key) INTO expected,all_full FROM jsonb_array_elements(doc->(cfg#>>'{array,name}')) WHERE NOT value?bad;
        IF expected>0 AND NOT (cfg#>>'{array,insert}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='array insert is disabled';END IF;
        IF expected>1 AND all_full THEN
          SELECT format('%%I',cfg#>>'{array,parent_fk}')||coalesce(','||string_agg(format('%%I',value),','),''),format('x.%%I',cfg#>>'{array,parent_fk}')||coalesce(','||string_agg(format('x.%%I',value),','),'') INTO col_list,select_list FROM jsonb_each_text(cfg#>'{array,fields}') WHERE value<>cfg#>>'{array,key}';
          EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_recordset(NULL::%%I.%%I,$1)x WHERE x.%%I IS NULL',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],col_list,select_list,(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,key}') USING mapped;
        ELSE
          FOR el IN SELECT value FROM jsonb_array_elements(doc->(cfg#>>'{array,name}')) WHERE NOT value?bad LOOP
            cols:=ARRAY[cfg#>>'{array,parent_fk}'];map:=jsonb_build_object(cfg#>>'{array,parent_fk}',to_jsonb(OLD._id));FOR bad,idj IN SELECT key,value FROM jsonb_each(cfg#>'{array,fields}') LOOP IF el?bad THEN cols:=array_append(cols,idj#>>'{}');map:=map||jsonb_build_object(idj#>>'{}',el->bad);END IF;END LOOP;
            EXECUTE format('INSERT INTO %%I.%%I(%%s) SELECT %%s FROM jsonb_populate_record(NULL::%%I.%%I,$1)x',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],(SELECT string_agg(quote_ident(x),',')FROM unnest(cols)x),(SELECT string_agg('x.'||quote_ident(x),',')FROM unnest(cols)x),(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2]) USING map;
          END LOOP;
        END IF;
        END IF;
      END IF;
    END IF;
    EXECUTE format('SELECT data FROM %%s WHERE _id=$1',%L) USING root_id INTO NEW.data;RETURN NEW;
  ELSE
    IF NOT (cfg#>>'{root,delete}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='root delete is disabled';END IF;
    root_id:=OLD._id;
    n:=NULL;EXECUTE format('SELECT 1 FROM %%I.%%I WHERE %%I=$1 FOR UPDATE',%L,%L,%L) INTO n USING root_id;IF n IS NULL THEN EXECUTE format('SELECT EXISTS(SELECT 1 FROM %%I.%%I WHERE %%I=$1)',%L,%L,%L) INTO changed USING root_id;IF changed THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking root';ELSE RETURN NULL;END IF;END IF;
    IF cfg?'object' AND (cfg#>>'{object,etag}')::boolean AND jsonb_typeof(OLD.data->(cfg#>>'{object,name}'))='object' THEN
      EXECUTE format('SELECT count(*) FROM (SELECT o.%%I FROM %%I.%%I o JOIN %%I.%%I r ON o.%%I=r.%%I WHERE r.%%I=$1 FOR UPDATE OF o)s',cfg#>>'{object,key}',(parse_ident(cfg#>>'{object,table}'))[1],(parse_ident(cfg#>>'{object,table}'))[2],(parse_ident(cfg#>>'{root,table}'))[1],(parse_ident(cfg#>>'{root,table}'))[2],cfg#>>'{object,key}',cfg#>>'{object,root_fk}',cfg#>>'{root,key}') INTO n USING root_id;
      IF n<>1 THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking referenced object';END IF;
    END IF;
    expected:=0;IF cfg?'array' THEN EXECUTE format('SELECT count(*) FROM (SELECT %%I FROM %%I.%%I WHERE %%I=$1 ORDER BY %%I FOR UPDATE)s',%L,%L,%L,%L,%L) INTO expected USING root_id;END IF;
    EXECUTE format('SELECT data FROM %%s WHERE _id=$1',%L) USING root_id INTO cur;
    IF cur IS NULL OR cur#>>'{_metadata,etag}' IS DISTINCT FROM OLD.data#>>'{_metadata,etag}' THEN RAISE EXCEPTION USING ERRCODE='PT412',MESSAGE='etag does not match after locking';END IF;
    IF cfg?'array' AND expected<jsonb_array_length(cur->(cfg#>>'{array,name}')) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking array elements';END IF;
    IF cfg?'array' AND expected>0 THEN IF (cfg#>>'{array,delete}')::boolean THEN EXECUTE format('DELETE FROM %%I.%%I WHERE %%I=$1',%L,%L,%L) USING root_id;ELSE EXECUTE format('UPDATE %%I.%%I SET %%I=NULL WHERE %%I=$1',%L,%L,%L,%L) USING root_id;END IF;GET DIAGNOSTICS n=ROW_COUNT;IF n<>expected THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security filtered array delete or unlink';END IF;END IF;
    EXECUTE format('DELETE FROM %%I.%%I WHERE %%I=$1',%L,%L,%L) USING root_id;GET DIAGNOSTICS n=ROW_COUNT;IF n<>1 THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='root delete denied';END IF;RETURN OLD;
  END IF;
END
$body$,
 cfg,
	 root_schema,
	 root_table,
	 cfg#>>'{root,key}',
	 relation_checks,
 object_key_field,
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 root_schema,
 root_table,
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 array_schema,
 array_table,
 array_schema,
 array_table,
 qview,
 object_key_field,
 object_key_field,
 array_key_field,
 array_key_field,
 array_key_field,
 array_key_field,
 array_key_field,
 array_key_field,
 array_key_field,
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 cfg#>>'{array,key}',
 array_schema,
 array_table,
 cfg#>>'{array,parent_fk}',
 cfg#>>'{array,key}',
 qview,
 object_lookup,
 object_key_field,
 object_key_field,
	 object_key_field,
	 object_key_field,
 root_schema,
 root_table,
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 root_schema,
 root_table,
	 root_touch_column,
	 root_touch_column,
 cfg#>>'{root,key}',
 qview,
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 root_schema,
 root_table,
 cfg#>>'{root,key}',
 cfg#>>'{array,key}',
 array_schema,
 array_table,
 cfg#>>'{array,parent_fk}',
 cfg#>>'{array,key}',
 qview,
 array_schema,
 array_table,
 cfg#>>'{array,parent_fk}',
 array_schema,
 array_table,
 cfg#>>'{array,parent_fk}',
 cfg#>>'{array,parent_fk}',
 root_schema,
 root_table,
 cfg#>>'{root,key}');
    delete_body:=format($delete_body$
DECLARE cfg constant jsonb:=%L::jsonb; cur jsonb; n int; expected int; changed boolean;
BEGIN
  %s
  IF expected_etag IS NULL THEN RAISE EXCEPTION USING ERRCODE='PT428',MESSAGE='expected_etag is required';END IF;
  IF NOT (cfg#>>'{root,delete}')::boolean THEN RAISE EXCEPTION USING ERRCODE='PT422',MESSAGE='root delete is disabled';END IF;
  n:=NULL;EXECUTE format('SELECT 1 FROM %%I.%%I WHERE %%I=$1 FOR UPDATE',(parse_ident(cfg#>>'{root,table}'))[1],(parse_ident(cfg#>>'{root,table}'))[2],cfg#>>'{root,key}') INTO n USING expected_id;
  IF n IS NULL THEN EXECUTE format('SELECT EXISTS(SELECT 1 FROM %%I.%%I WHERE %%I=$1)',(parse_ident(cfg#>>'{root,table}'))[1],(parse_ident(cfg#>>'{root,table}'))[2],cfg#>>'{root,key}') INTO changed USING expected_id;IF changed THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking root';ELSE RAISE EXCEPTION USING ERRCODE='PT412',MESSAGE='document no longer exists';END IF;END IF;
  IF cfg?'object' AND (cfg#>>'{object,etag}')::boolean THEN
    EXECUTE format('SELECT count(*) FROM (SELECT o.%%I FROM %%I.%%I o JOIN %%I.%%I r ON o.%%I=r.%%I WHERE r.%%I=$1 FOR UPDATE OF o)s',cfg#>>'{object,key}',(parse_ident(cfg#>>'{object,table}'))[1],(parse_ident(cfg#>>'{object,table}'))[2],(parse_ident(cfg#>>'{root,table}'))[1],(parse_ident(cfg#>>'{root,table}'))[2],cfg#>>'{object,key}',cfg#>>'{object,root_fk}',cfg#>>'{root,key}') INTO n USING expected_id;
  END IF;
  expected:=0;IF cfg?'array' THEN
    EXECUTE format('SELECT count(*) FROM (SELECT %%I FROM %%I.%%I WHERE %%I=$1 ORDER BY %%I FOR UPDATE)s',cfg#>>'{array,key}',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,parent_fk}',cfg#>>'{array,key}') INTO expected USING expected_id;
  END IF;
  EXECUTE format('SELECT data FROM %%s WHERE _id=$1',%L) USING expected_id INTO cur;
  IF cur IS NULL OR cur#>>'{_metadata,etag}' IS DISTINCT FROM expected_etag THEN RAISE EXCEPTION USING ERRCODE='PT412',MESSAGE='etag does not match';END IF;
  IF cfg?'object' AND (cfg#>>'{object,etag}')::boolean AND jsonb_typeof(cur->(cfg#>>'{object,name}'))='object' AND n<>1 THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking referenced object';END IF;
  IF cfg?'array' AND expected<jsonb_array_length(cur->(cfg#>>'{array,name}')) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security prevents locking array elements';END IF;
  IF cfg?'array' AND expected>0 THEN
    IF (cfg#>>'{array,delete}')::boolean THEN EXECUTE format('DELETE FROM %%I.%%I WHERE %%I=$1',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,parent_fk}') USING expected_id;ELSE EXECUTE format('UPDATE %%I.%%I SET %%I=NULL WHERE %%I=$1',(parse_ident(cfg#>>'{array,table}'))[1],(parse_ident(cfg#>>'{array,table}'))[2],cfg#>>'{array,parent_fk}',cfg#>>'{array,parent_fk}') USING expected_id;END IF;
    GET DIAGNOSTICS n=ROW_COUNT;IF n<>expected THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='row-level security filtered array delete or unlink';END IF;
  END IF;
  EXECUTE format('DELETE FROM %%I.%%I WHERE %%I=$1',(parse_ident(cfg#>>'{root,table}'))[1],(parse_ident(cfg#>>'{root,table}'))[2],cfg#>>'{root,key}') USING expected_id;GET DIAGNOSTICS n=ROW_COUNT;
  IF n<>1 THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='root delete denied';END IF;
  RETURN cur;
END
$delete_body$,cfg,relation_checks,qview);
    ddl:=ddl||E'\n'||format('CREATE FUNCTION %s() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS %L;',qinsert,body)||E'\n'||format('CREATE FUNCTION %s() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS %L;',qupdate,body)||E'\n'||format('CREATE FUNCTION %s() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS %L;',qdelete,body)||E'\n'||format('CREATE FUNCTION %s(expected_id %s,expected_etag text) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS %L;',qdelete_document,root_key_type,delete_body)||E'\n'||format('CREATE TRIGGER %I INSTEAD OF INSERT ON %s FOR EACH ROW EXECUTE FUNCTION %s();',vp[2]||'__insert',qview,qinsert)||E'\n'||format('CREATE TRIGGER %I INSTEAD OF UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION %s();',vp[2]||'__update',qview,qupdate)||E'\n'||format('CREATE TRIGGER %I INSTEAD OF DELETE ON %s FOR EACH ROW EXECUTE FUNCTION %s();',vp[2]||'__delete',qview,qdelete)||E'\n'||format('COMMENT ON VIEW %s IS %L;',qview,'pg_jsontailor duality view spec v1: '||cfg::text);
    RETURN ddl;
END
$generator$;

CREATE FUNCTION jsontailor.create_duality_view(view_name text,spec jsonb)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp
AS $creator$
DECLARE ddl text;
BEGIN
  IF view_name IS NULL OR spec IS NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='view_name and spec must not be SQL NULL';END IF;
  ddl:=jsontailor.duality_view_ddl(view_name,spec);EXECUTE ddl;
END
$creator$;

GRANT USAGE ON SCHEMA jsontailor TO PUBLIC;
