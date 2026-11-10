# Security policy

## Supported versions

The unreleased 0.2.0 development line is the only maintained line. It is not a
released product and carries no support-period or response-time commitment.
Security assessment and fixes are provided on a best-effort basis without a
warranty that every report will result in a fix or a particular release date.

## Reporting a vulnerability

Please report suspected vulnerabilities through the enabled [GitHub private vulnerability reporting channel](https://github.com/baremonconsulting/pg_jsontailor/security/advisories/new). Do not open a public issue before the report has been assessed. The repository owner receives confidential reports and arranges assessment; this is not a response-time guarantee.

Include the affected version, PostgreSQL version, reproduction steps and expected impact. Remove credentials, personal data and customer identifiers from all reports.

## Deployment note

Generated views and triggers use invoker rights. Deployers remain responsible for table privileges, row-level security policies, PostgREST exposure, authentication, TLS, backups and operational monitoring.

Read [the security model and residual risks](docs/SECURITY_MODEL.md) before a
pilot. Raw PostgREST must not be exposed outside the controlled network path.

The normalised mapping specification and generated SQL are visible to roles
that can inspect PostgreSQL catalogue comments and function definitions. They
must not contain secrets. Under RLS, attempting to use an explicit child key
that belongs to a hidden row can return `PT422`; as with a primary-key conflict,
this may reveal that the key is already allocated. Use `GENERATED ALWAYS`
identities when key existence is sensitive, and do not expose catalogue access
or arbitrary write errors more broadly than the application requires.

Lifecycle inventory treats catalogue comments as untrusted discovery metadata.
It validates normalised specifications against trusted catalogue queries and
does not execute discovered health functions. Views owned by another role are
reported as `SKIPPED`; administrators must not execute similarly named callbacks
merely because a comment marks an object as managed. Changing role or session
authorisation is not an isolation boundary for owner-supplied code.
