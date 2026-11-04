# GitHub repository setup

The repository contains the workflows and templates needed for routine development. The following repository settings must be enabled in the GitHub user interface after the initial push.

## General

- Set `main` as the default branch.
- Add the description: `Writable JSON document views over relational PostgreSQL tables.`
- Add topics: `postgresql`, `postgresql-extension`, `json`, `postgrest`, `database-migration`, `optimistic-concurrency`.
- Enable Issues and Discussions if community support is planned.
- Allow squash merging and automatically delete merged branches.
- Disable merge commits if a linear public history is preferred.

## Main branch ruleset

Create an active ruleset targeting `main`:

- require a pull request before merging;
- require one approval when a second maintainer is available;
- dismiss stale approvals after new commits;
- require all conversations to be resolved;
- require branches to be up to date before merging;
- require linear history;
- block force pushes and branch deletion;
- do not allow bypass except for an emergency maintainer role.

After the workflows have run once, require these uniquely named checks:

- `Repository checks`;
- `PostgreSQL 17`;
- `PostgreSQL 18`;
- `PostgreSQL 19`;
- `CodeQL (Python)`;
- `Dependency review`.

## Tag ruleset

Protect tags matching `v*`. Restrict creation and deletion to maintainers. A release tag must match the `default_version` in `pg_jsontailor.control`; the release workflow enforces this and publishes a source archive with a SHA-256 checksum.

## Security

- Enable the dependency graph and Dependabot alerts.
- Enable Dependabot security updates.
- Enable secret scanning and push protection where available.
- Enable private vulnerability reporting.
- Keep CodeQL default setup disabled while the repository's advanced workflow is active, to avoid duplicate analyses.

## Actions

- Allow actions from GitHub and verified creators, or use an explicit allow-list.
- Keep the default workflow token permission at read-only.
- Do not allow workflows to create or approve pull requests unless a future release process requires it.
- Require actions to be pinned to full commit SHAs. Dependabot maintains the pinned references.

## Release process

1. Update `pg_jsontailor.control`, `CHANGELOG.md` and `CITATION.cff` to the same version.
2. Merge through a pull request after all required checks pass.
3. Create and push an annotated tag such as `v0.2.0`.
4. Verify the generated GitHub release, source archive and checksum.
