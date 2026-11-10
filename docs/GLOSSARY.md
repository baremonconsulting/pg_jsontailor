# Glossary

**Document view** — a PostgreSQL view exposing a relational aggregate as one JSONB document.

**Root** — the table supplying document identity and top-level fields.

**Object mapping** — an optional read-only many-to-one object referenced by the root.

**Array mapping** — an optional ordered collection of child rows.

**ETag** — the optimistic-concurrency token in `_metadata.etag`.

**Generator database** — a database with pg_jsontailor installed, used to create a standalone bundle.

**Target database** — the database where the generated view is deployed; the standalone route needs no extension there.

**Pilot gate** — functional, security and customer-specific checks for a controlled pilot.

**Publication gate** — additional benchmark, provenance and release-authority checks for public release or performance claims.
