# 12. MERGE

Pair by identity in `ON`; place the client ETag in `WHEN MATCHED AND`, including DELETE. Use `WHEN NOT MATCHED THEN INSERT(data)` unless a supplied column identity is confirmed by the same document `_id`. See [MERGE](../MERGE.md).

