# Release authority and nonpublishing rehearsal

Candidate validation is not release approval. No workflow in the candidate
rehearsal creates a tag or a GitHub release. Publication is a separate manual
workflow, restricted to protected `main`, an independently approved `release`
environment and final metadata. It only accepts an already existing lightweight
version tag on the exact verified SHA; `--verify-tag` prevents implicit creation.

## Trust chain

`.github/release-policy.json` pins repository, workflow IDs and paths, GitHub
Actions app ID and complete job inventories. `tools/release_authority.py` reads
all counted API pages, rejects incomplete/duplicate/conflicting responses and
selects the latest eligible run and its current attempt. Every job must succeed
in that attempt, link to the trusted check suite and match the exact SHA. A
successful historical attempt cannot conceal a later failed run or rerun.

Release authority requires `push` on `main`, protected-main ancestry and admin
enforcement. The independent nightly policy permits only schedule/dispatch on
the exact SHA/ref and the approved nightly workflow. The approved evidence index
requires the complete PG17/18/19 matrix, nightly 5/6 campaign, separate stale-return
mutant and standalone results. Both gates consume that index; a bare check name
or an unapproved producer JSON file is insufficient. Fixtures test the positive
algorithm but never supply live approval.

Live authority reads use the separately provisioned
`RELEASE_AUTHORITY_READ_TOKEN` Actions secret, exposed only to read-only
validation steps as `GH_AUTHORITY_TOKEN`. There is no fallback to `GITHUB_TOKEN`,
`GH_TOKEN` or a runner's saved CLI credentials: a missing secret fails before the
first API request. Use a repository-scoped fine-grained personal access token or
GitHub App installation token with **read-only** Administration, Actions, Checks,
Contents and Environments permissions (and automatically granted Metadata).
Do not grant any write permission. GitHub requires
[Administration: read for branch-protection reads](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection).
The owner must provision, rotate and verify access with that identity before
rehearsal; no credential is created or inferred by the tooling. Short-lived App
tokens must be supplied fresh and remain valid for the validation job.

The publication step receives only the separate `github.token` with
`contents: write`; it does not receive the authority secret. Authority and
environment read-back run immediately beforehand in a separate step. The
post-publication asset verifier uses the publication token for read-only API
requests and does not need Administration access.

`tools/prepare_release.py` is shared by rehearsal and release preparation. It
rebuilds `git archive`, requires the approved archive checksum, verifies the full
generated-SQL legal notice, consumes the index and approvals, checks the signed
record and independently verifies benchmark installation evidence. A candidate
requires dispatch CI/nightly evidence and explicitly proves that strict main
authority rejects it for the expected candidate-only reason. API outages or
other errors do not count as successful rejection.

## Metadata before freeze

`release-metadata.json` is authoritative. Valid same-version transitions are
`development -> candidate -> final`; reverse and skipped transitions are denied.
A later version starts again in development. Unreleased states have no date.
Final metadata requires a dated CHANGELOG heading, matching control/CITATION
version, CITATION date and state, matching README state and no stale unreleased
claim. CITATION records state as a comment to preserve the CFF schema.

Finish all source changes, including real trusted public keys, before freeze
commit `S`. Any later source commit creates `S2` and invalidates required exact-SHA
evidence. Store evidence outside `S`.

## Signed records and durable storage

The `EVIDENCE_STORE_REPOSITORY` repository variable names the approved durable,
repository-controlled store. Dispatch supplies its immutable full commit ID;
never use a moving ref. Use one exact-SHA evidence root containing
`evidence-index.json`, `approval.json`, indexed bundles/archive/downloads,
`record.json`, `record.sig`, `signer.pem` and
`benchmark/pg17`, `benchmark/pg18`, `benchmark/pg19`. Benchmark directories include
their installation attestations/logs and full verified benchmark outputs.
Provision read access to this store without granting publication write access.
Retain approved public evidence and signed records permanently. Retain encrypted
diagnostics for at least two years under the named release owner, with restricted
decryption keys and a documented retrieval test; Actions artifact expiry is not
the retention mechanism. Never commit unencrypted sensitive diagnostics publicly.
Customer pilot samples and diagnostics are not release evidence and must not be
copied into release-evidence retention. Their access, retention and verified
deletion follow the separate written Pilot scope record.

`tools/evidence_index.py retain` verifies and copies indexed material to a new,
non-overwriting durable destination. The release owner must also retain the
signed record/signature/public key and benchmark evidence, commit the approved
store and read it back through the dispatch commit. The signed record binds
exact source SHA, version, evidence-index digest, archive digest, approvals,
signer identity, timestamp and named withdrawal owner.

`tools/release_record.py sign` signs canonical JSON with an external RSA private
key using SHA-256; `verify` independently checks its signature and key digest
against `.github/release-signers.json`. The checked-in candidate keys are
technically provisioned, but they do not become approved trust roots until the
release owner verifies their fingerprints, accepts the stated signing-service
identity and records private-key custody outside the repository. Never trust a
key solely because it arrived in the evidence bundle. `benchmark_key` separately
pins a checked-in `.github/` public-key path and SHA-256 digest for Ed25519
installation attestations. Publication records require an independent release
reviewer; candidate records explicitly set `release_approval: false` and cannot
be reused.

Example verification, using a previously approved record and public key:

```sh
python3 tools/release_record.py verify evidence/record.json \
  --signature evidence/record.sig --key evidence/signer.pem \
  --sha "$S" --index evidence/evidence-index.json \
  --archive evidence/pg_jsontailor-0.2.0.tar.gz --kind candidate
```

## Archive and publication checks

The reusable archive workflow extracts the checksummed archive into a non-Git
tree and runs its own sources on PostgreSQL 17, 18 and 19: notice and source
inventory, standalone CRUD/ETags/delete/MERGE/ACL/RLS/dump-restore, PGXS regression
and isolation, core operations, MERGE and security. Missing majors fail the job
matrix. Source extraction rejects unsafe paths and non-regular members.

Preparation and archive jobs have read-only repository permissions. Only the
approval-protected publication job receives `contents: write`; checkout does not
persist credentials. It repeats all validators immediately before publication
and compares the reconstructed package with the archive-tested artifact. The
signed publication record must already exist: workflows do not manufacture
human approvals or signing identities. Immutable releases must be enabled.

After publication, `tools/verify_postpublication.py` verifies the immutable tag,
release state, exact four-asset inventory, API SHA-256 digests, actual downloaded
bytes, checksum, notice and signed publication record. Negative fixtures cover
tag movement, mutable/draft releases and asset/signature/approval corruption.
Actual publication and live post-publication results remain pending until a
separately authorised release. Local fixtures must never be relabelled as live.

## Withdrawal without rewriting history

The signed record names the withdrawal owner. For a security or correctness
incident, publish a security advisory or repository notice identifying the
affected version, checksum and superseding version. Leave immutable tags and
assets untouched; do not delete, move or replace a version. Corrected source is
a new later version with new approvals and evidence. Retain incident and
withdrawal records alongside the original signed evidence.

## Live conditions still requiring owner action

Name an independent collaborator to review releases. The release owner must
verify and accept the provisioned public-key fingerprints and take custody of
the corresponding external private keys before signing evidence. Create/read
back the `release` environment with required independent review, prevention of
self-review, no admin bypass and protected-branch deployment restrictions. The
checker fails closed while it is absent. Provision and verify the read-only
authority secret described above. Then collect all frozen-SHA
CI/nightly/benchmark/archive evidence and an approved signed candidate record.
Protected-main positive authority, a final publication record, actual
publication and live post-publication verification remain separate pending
acceptance criteria; no tag or release is created by this implementation task.
