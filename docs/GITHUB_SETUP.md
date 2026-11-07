# GitHub repository setup

The repository contains workflows and executable governance read-back checks.
See [release authority](RELEASE_AUTHORITY.md) for the complete trust chain.

## General

- Set `main` as the default branch.
- Add the description: `Generate writable JSON views over relational PostgreSQL tables, with full-document writes and optimistic concurrency.`
- Add topics: `postgresql`, `postgresql-extension`, `json`, `postgrest`, `database-migration`, `optimistic-concurrency`.
- Enable Issues and Discussions if community support is planned.
- Allow squash merging and automatically delete merged branches.
- Disable merge commits if a linear public history is preferred.

## Main branch ruleset

The protected `main` branch requires:

- require a pull request before merging;
- require one approval when a second maintainer is available;
- dismiss stale approvals after new commits;
- require all conversations to be resolved;
- require branches to be up to date before merging;
- require linear history;
- block force pushes and branch deletion;
- enforce the rules for administrators, with no standing bypass.

The verified personal-repository owner is `baremonconsulting` (account
263133107). The approving-review count remains zero until an independent
maintainer is available; a single owner is not asked to approve their own pull
request. All six required checks remain mandatory for administrators.

Emergency recovery is an owner settings operation, not a normal merge bypass.
Record the incident, exact SHA, reason and owner identity first; only the named
owner may temporarily change **main admin enforcement** if the protected process
is unusable. Restore it immediately and retain before/after read-back and the
incident record. Never enable force pushes or branch deletion, and never alter
immutable-tag rules or immutable existing releases during recovery. Account
recovery follows GitHub's owner account recovery procedure.

After the workflows have run once, require these uniquely named checks:

- `Repository checks`;
- `PostgreSQL 17`;
- `PostgreSQL 18`;
- `PostgreSQL 19`;
- `CodeQL (Python)`;
- `Dependency review`.

## Tag ruleset

The active `immutable-version-tags` ruleset targets `refs/tags/v*`, denies updates
and deletion, and has no bypass actors, including administrators. Creation is not
blocked by that ruleset; publication never creates a tag. A separately authorised
existing lightweight tag must match the final version and verified main SHA.
Immutable releases are enabled, so new published release assets are locked too.

## Security

- Enable the dependency graph and Dependabot alerts.
- Enable Dependabot security updates.
- Enable secret scanning and push protection where available.
- Keep private vulnerability reporting enabled; SECURITY and SUPPORT link to the actual confidential reporting channel.
- Keep CodeQL default setup disabled while the repository's advanced workflow is active, to avoid duplicate analyses.

## Actions

- Use the exact-SHA action allow-list in `.github/governance-policy.json`; broad GitHub-owned/verified-creator allowances are disabled.
- Keep the default workflow token permission at read-only.
- Do not allow workflows to create or approve pull requests unless a future release process requires it.
- Require actions to be pinned to full commit SHAs. Dependabot maintains the pinned references.

## Release process

Follow [RELEASE_AUTHORITY.md](RELEASE_AUTHORITY.md). Candidate rehearsal is manual,
read-only and must demonstrate rejection by strict protected-main authority.
Publication uses a separate manual workflow and a protected `release` environment
with independent review, self-review prevention, protected-branch restrictions
and no admin bypass. It requires final dated metadata, exact-SHA push/main plus
nightly and benchmark evidence, a signed approved record, an already existing
immutable version tag and PG17/18/19 extracted-archive validation. Candidate
dispatch or product-equivalent evidence never replaces exact-SHA authority.

Read back settings without changing them:

```sh
python3 tools/repository_governance.py --evidence /secure/store/governance-readback.json
python3 tools/repository_governance.py --check-release-environment
```

`--apply` first verifies authenticated admin capability and the named owner,
persists preflight evidence, repeats the read to detect intervening changes, then
reads back every applied control. It never creates environments without a real
named independent reviewer, tags, branches or releases. Stronger existing tag
rules are never silently replaced. Release environment creation remains pending
until an independent reviewer is named.

The authorised sequence also requires version agreement across
`pg_jsontailor.control`, `CHANGELOG.md` and `CITATION.cff`; a pull request with all
required checks; all six `main` checks on the exact squash SHA; final PG17/18/19
benchmark evidence for that exact immutable candidate; an existing annotated
version tag on the verified `main` commit; and verification of the published
release, source archive and checksum. Product-equivalence fingerprints never
replace exact-SHA runs. The `Dependency review` push run compares the previous
`main` commit with the new squash commit and fails closed for an invalid or missing
comparison base.

GitHub documents [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
and the [repository immutability API](https://docs.github.com/en/rest/repos/repos#enable-immutable-releases).
