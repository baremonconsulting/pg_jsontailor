"""Reviewed SQL workload; direct SQL deliberately provides a lower contract."""
SIZES = (0, 1, 10, 100, 1000)
OPERATIONS = ("read", "insert", "root_update", "update", "delete",
              "merge_insert", "merge_update", "merge_delete")
IMPLEMENTATIONS = ("direct", "duality")

def statements(operation, implementation, size):
    source_id=1000+size; insert_id=100000+size
    if operation == "read":
        if implementation == "duality":
            return [f"SELECT pg_column_size(data) FROM bench_dv.orders_dv WHERE _id={source_id}"]
        return [f"""SELECT pg_column_size(jsonb_build_object('_id',o.order_id,'status',o.status,'created',o.created_at,'customer',jsonb_build_object('customerId',c.customer_id,'name',c.name),'items',COALESCE((SELECT jsonb_agg(jsonb_build_object('itemId',i.item_id,'product',i.product,'qty',i.quantity,'price',i.unit_price) ORDER BY i.item_id) FROM bench_dv.items i WHERE i.order_id=o.order_id),'[]'::jsonb))) FROM bench_dv.orders o LEFT JOIN bench_dv.customers c ON c.customer_id=o.customer_id WHERE o.order_id={source_id}"""]
    if operation == "update":
        if implementation == "duality":
            return [f"UPDATE bench_dv.orders_dv SET data=(SELECT update_doc FROM bench_dv.payloads WHERE size={size}) WHERE _id={source_id}"]
        return [f"UPDATE bench_dv.orders o SET status=p.update_doc->>'status',created_at=(p.update_doc->>'created')::date,customer_id=(p.update_doc#>>'{{customer,customerId}}')::bigint FROM bench_dv.payloads p WHERE p.size={size} AND o.order_id={source_id}",
                f"UPDATE bench_dv.items i SET product=x.product,quantity=x.qty,unit_price=x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.update_doc->'items') x(\"itemId\" bigint,product text,qty integer,price numeric) WHERE p.size={size} AND i.order_id={source_id} AND i.item_id=x.\"itemId\""]
    if operation == "root_update":
        if implementation == "duality":
            return [f"UPDATE bench_dv.orders_dv SET data=(SELECT root_doc FROM bench_dv.payloads WHERE size={size}) WHERE _id={source_id}"]
        return [f"UPDATE bench_dv.orders o SET status=p.root_doc->>'status' FROM bench_dv.payloads p WHERE p.size={size} AND o.order_id={source_id}"]
    if operation == "insert":
        if implementation == "duality":
            return [f"INSERT INTO bench_dv.orders_dv(data) SELECT insert_doc FROM bench_dv.payloads WHERE size={size}"]
        return [f"INSERT INTO bench_dv.orders(order_id,customer_id,status,created_at) SELECT insert_id,(insert_doc#>>'{{customer,customerId}}')::bigint,insert_doc->>'status',(insert_doc->>'created')::date FROM bench_dv.payloads WHERE size={size}",
                f"INSERT INTO bench_dv.items(item_id,order_id,product,quantity,unit_price) SELECT x.\"itemId\",p.insert_id,x.product,x.qty,x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.insert_doc->'items') x(\"itemId\" bigint,product text,qty integer,price numeric) WHERE p.size={size}"]
    if operation == "delete":
        if implementation == "duality":
            return [f"SELECT bench_dv.orders_dv__delete_document({source_id},(SELECT data#>>'{{_metadata,etag}}' FROM bench_dv.orders_dv WHERE _id={source_id}))"]
        return [f"DELETE FROM bench_dv.items WHERE order_id={source_id}",f"DELETE FROM bench_dv.orders WHERE order_id={source_id}"]
    if operation == "merge_insert":
        if implementation == "duality":
            return [f"""MERGE INTO bench_dv.orders_dv AS target
USING (SELECT insert_id AS root_id,insert_doc AS doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target._id=source.root_id
WHEN NOT MATCHED THEN INSERT(data) VALUES(source.doc)"""]
        return [f"""MERGE INTO bench_dv.orders AS target
USING (SELECT insert_id,insert_doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.insert_id
WHEN NOT MATCHED THEN INSERT(order_id,customer_id,status,created_at)
VALUES(source.insert_id,(source.insert_doc#>>'{{customer,customerId}}')::bigint,source.insert_doc->>'status',(source.insert_doc->>'created')::date)""",
                f"""MERGE INTO bench_dv.items AS target
USING (SELECT x."itemId",p.insert_id,x.product,x.qty,x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.insert_doc->'items') x("itemId" bigint,product text,qty integer,price numeric) WHERE p.size={size}) AS source
ON target.item_id=source."itemId"
WHEN NOT MATCHED THEN INSERT(item_id,order_id,product,quantity,unit_price)
VALUES(source."itemId",source.insert_id,source.product,source.qty,source.price)"""]
    if operation == "merge_update":
        if implementation == "duality":
            return [f"""MERGE INTO bench_dv.orders_dv AS target
USING (SELECT source_id AS root_id,update_doc AS doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target._id=source.root_id
WHEN MATCHED THEN UPDATE SET data=source.doc"""]
        return [f"""MERGE INTO bench_dv.orders AS target
USING (SELECT source_id,update_doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.source_id
WHEN MATCHED THEN UPDATE SET status=source.update_doc->>'status',created_at=(source.update_doc->>'created')::date,customer_id=(source.update_doc#>>'{{customer,customerId}}')::bigint""",
                f"""MERGE INTO bench_dv.items AS target
USING (SELECT p.source_id,x."itemId",x.product,x.qty,x.price FROM bench_dv.payloads p CROSS JOIN LATERAL jsonb_to_recordset(p.update_doc->'items') x("itemId" bigint,product text,qty integer,price numeric) WHERE p.size={size}) AS source
ON target.order_id=source.source_id AND target.item_id=source."itemId"
WHEN MATCHED THEN UPDATE SET product=source.product,quantity=source.qty,unit_price=source.price"""]
    if operation == "merge_delete":
        if implementation == "duality":
            return [f"""MERGE INTO bench_dv.orders_dv AS target
USING (SELECT source_id AS root_id,root_doc AS doc FROM bench_dv.payloads WHERE size={size}) AS source
ON target._id=source.root_id
WHEN MATCHED AND target.data#>>'{{_metadata,etag}}'=source.doc#>>'{{_metadata,etag}}' THEN DELETE"""]
        return [f"""MERGE INTO bench_dv.items AS target
USING (SELECT source_id FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.source_id
WHEN MATCHED THEN DELETE""",
                f"""MERGE INTO bench_dv.orders AS target
USING (SELECT source_id FROM bench_dv.payloads WHERE size={size}) AS source
ON target.order_id=source.source_id
WHEN MATCHED THEN DELETE"""]
    raise ValueError(operation)
