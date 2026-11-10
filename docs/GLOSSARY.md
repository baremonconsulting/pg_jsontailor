# Glossary

**Document view** — a PostgreSQL view exposing a relational aggregate as one JSONB document.

**Root** — the table supplying document identity and top-level fields.

**Object mapping** — an optional read-only many-to-one object referenced by the root.
INSERT ignores edited non-key object fields; reads return referenced values.
Replacement checks them with `object.etag=true` and otherwise ignores them;
the object key can change the reference. See [API object semantics](API.md#errors).

**Array mapping** — an optional ordered collection of child rows.

**ETag** — the optimistic-concurrency token in `_metadata.etag`.

**Generator database** — a database with pg_jsontailor installed, used to create a standalone bundle.

**Target database** — the database where the generated view is deployed; the standalone route needs no extension there.

**Pilot gate** — functional, security and customer-specific checks for a controlled pilot.

**Publication gate** — additional benchmark, provenance and release-authority checks for public release or performance claims.

**Binding view** — an internal zero-row view that retains source relation and mapped-column dependencies.

**Bundle** — the standalone `generated.sql` and complete `GENERATED_SQL_NOTICE` handed over together.

**Capability** — an explicit reviewed permission, such as array-reader or delete-RPC EXECUTE; it does not grant source-table access.

**Regeneration** — transactional replacement from a stored specification, distinct from recreation with an updated specification.

**Invoker rights** — execution using the caller's privileges and row visibility, not elevated owner privileges.
