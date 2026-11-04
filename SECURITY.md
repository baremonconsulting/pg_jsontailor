# Security policy

## Supported versions

The current 0.2.x development line receives security fixes. Earlier validation versions are not supported.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through the contact details published at [baremon.eu](https://www.baremon.eu/). Do not open a public issue before the report has been assessed.

Include the affected version, PostgreSQL version, reproduction steps and expected impact. Remove credentials, personal data and customer identifiers from all reports.

## Deployment note

Generated views and triggers use invoker rights. Deployers remain responsible for table privileges, row-level security policies, PostgREST exposure, authentication, TLS, backups and operational monitoring.
