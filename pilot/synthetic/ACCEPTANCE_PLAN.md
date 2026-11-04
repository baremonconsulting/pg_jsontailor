# Synthetic pre-pilot acceptance plan

This rehearsal exercises the currently supported thin slice: one order root, one read-only customer object, and one array of order items. It does not claim compatibility with an unknown client schema.

## Acceptance scenarios

1. Read back five seeded documents and compare semantic JSON after excluding generated `_metadata`.
2. Filter by status and apply paging.
3. Insert a document without root or child identifiers and receive generated identifiers.
4. Replace a complete document and verify the relational table state.
5. Let two clients write from the same ETag; the first must succeed and the second must receive 412.
6. Reject a missing ETag with 428 and incomplete/unknown documents with 422.
7. Keep base tables out of the REST surface while allowing the generated duality view.
8. Delete a document and its children atomically.
9. Complete 20 local operational reads without errors; timings are diagnostic only, not a benchmark.
10. Restore the pre-pilot snapshot into a fresh database and recover five roots, ten children, and five documents.

The decision is `GO` only if every check passes. A GO authorises progression to a controlled pilot with anonymised client data; it does not authorise production deployment.
