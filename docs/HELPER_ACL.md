# Helper capability policy

Generated functions start owner-only, including under custom default privileges.
Trigger, guard and check helpers must remain owner-only. Runtime view triggers
do not require an application EXECUTE grant.

The delete RPC requires both EXECUTE on its exact signature and DELETE on the
generated view, as well as the existing source privileges and RLS policies.
Source DML privileges alone never authorise the RPC.

For a reviewed, persistent runtime grant, the view owner runs:

```sql
SELECT jsontailor.grant_delete_rpc('api.orders_dv', 'application_role', false);
```

The final boolean explicitly records whether the role may delegate EXECUTE.
The view metadata records the role name, role OID, owner/grantor OID and grant
option. Regeneration preserves only an existing owner-issued RPC grant matching
that record exactly. It never resurrects a revoked grant. Delegated grants cause
regeneration to fail closed rather than losing grantor provenance.

On upgrade, legacy PUBLIC, custom-default and unreviewed direct helper grants
are not retained. Review intended RPC roles with this operation before
regenerating; never copy all old helper ACLs. Table/column view grants retain
their existing regeneration policy. Ownership changes and role recreation
require a new review. Standalone exported SQL enforces the same fresh ACLs but
does not provide the extension's review/regeneration administration API.
