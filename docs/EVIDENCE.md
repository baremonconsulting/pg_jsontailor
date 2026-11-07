# Candidate and release evidence

Candidate validation is not release authority. A candidate dispatch can prove
the implementation on its exact immutable commit, but cannot satisfy the
protected-main `push/main` gate. Freeze the complete candidate first. Any later
commit invalidates exact-SHA evidence, including nightly and benchmark runs.

## Collection and confidentiality

The matrix reserves 25 minutes for product tests: PG17/18 package installation
has a 480-second global budget, 180-second command limits, bounded network and
package-lock waits, and a nine-minute step limit. PG19 has a 15-minute build
step limit. The complete matrix job has a 45-minute limit. Installation errors
are retained, not replaced by results from a different PostgreSQL major or SHA.

`collect_evidence.py` runs with `if: always()` after database shutdown. It
requires exact reviewed suite inventories, regression/isolation outputs,
set-based reconciliation, environment/provenance and coverage. The separate
stale-return witness is mandatory on PG17/18/19; it does not change the nightly
five-property/six-mutation campaign. Standalone, quick-start, upgrade and public
contract suites are mandatory per-major inputs too.

Public artifacts contain only allow-listed structured result fields, checked
regression/isolation fixtures, coverage, environment metadata and a digest
manifest. Full structured observations, HTTP transcripts, installation and
PostgreSQL logs are retained as separate OpenSSL CMS encrypted diagnostics.
Never print raw PostgreSQL or HTTP logs into public Actions output. The
encrypted diagnostics artifact is separate from the public evidence artifact.
Actions artifacts have an explicit 30-day retention; they are transport, not
the durable release evidence store.

Before enabling the workflow, the release owner must provision a dedicated
encryption certificate and store its PEM public certificate in the repository
variable `EVIDENCE_DIAGNOSTICS_CERTIFICATE`. Keep its private key outside GitHub,
under the release owner's restricted storage and recovery controls. Missing or
invalid configuration fails collection closed, while public partial results
remain available. Test decryption with a synthetic diagnostic before approval:

```sh
openssl cms -decrypt -binary -inform DER -in postgresql.log.p7m \
  -recip evidence-recipient.pem -inkey evidence-recipient.key -out postgresql.log
```

Restrict decrypted destinations to the owner and authorised reviewers. Rotate
the certificate under reviewed change control; preserve the old private key for
the retention period, record old/new certificate fingerprints, test new
encryption/decryption, then change the repository variable. Recover lost access
using the owner's independently protected key backup; never re-upload plaintext
or claim that an undecryptable diagnostic was reviewed.

## Approved index and durable storage

The release owner downloads all eight GitHub ZIP artifacts: public and encrypted
diagnostics for CI17/18/19, plus both nightly18 artifacts. Preserve the original
download bytes. Extract public files under `ci-pg17/public/`, etc., with each
`bundle.json` beside `public/`; decrypting is not needed for digest validation.
Place encrypted diagnostics under each bundle's `diagnostics/` directory.

The version-1 index binds the exact commit, release archive checksum, each
workflow ID/path/event/ref/run/attempt, every bundle/file digest, GitHub artifact
ID and the original downloaded ZIP digest. It rejects mixed attempts, missing
majors, incomplete nightly inventories, changed outputs, ambiguous JSON, failed
or unreferenced rows and ZIP/member mismatches. Fixture tests prove the verifier,
not live workflow success or human approval.

Create an artifact manifest containing eight objects of this form:

```json
{"bundle_sha256":"<bundle.json SHA-256>","access":"public","github_artifact_id":123,"download":{"path":"downloads/ci17-public.zip","sha256":"<downloaded ZIP SHA-256>"}}
```

Use `access: diagnostics` for the encrypted artifact. Build the index outside the
frozen source commit, supplying all four `--bundle` arguments:

```sh
python3 tools/evidence_index.py build STORE/evidence-index.json --sha COMMIT \
  --root STORE --archive STORE/candidate.tar.gz \
  --bundle STORE/ci-pg17/bundle.json --bundle STORE/ci-pg18/bundle.json \
  --bundle STORE/ci-pg19/bundle.json --bundle STORE/nightly-pg18/bundle.json \
  --artifact-manifest STORE/artifact-manifest.json
```

After independent review, the protected evidence store holds an approval object
with exactly `schema_version: 1`, `index_sha256`, a named `approver` and an ISO
8601 timezone-qualified `approved_at`. Approval must come from that trusted
owner-controlled store, never from the unapproved producer's artifact. The
signed rehearsal/release record supplies the separate approval authority.
Both rehearsal and publish must call:

```sh
python3 tools/evidence_index.py verify STORE/evidence-index.json --sha COMMIT \
  --root STORE --approval TRUSTED/approval.json
python3 tools/evidence_index.py retain STORE/evidence-index.json --sha COMMIT \
  --root STORE --approval TRUSTED/approval.json --destination DURABLE/COMMIT
```

`retain` refuses to overwrite a destination, copies indexed files and original
ZIPs, then reads back and independently verifies every digest and approval.
Keep the approved index, redacted evidence and signed record in the dedicated
repository-controlled release-evidence store, separate from the frozen source
commit. Commit and back up that store under release-owner change control before
Actions expiry. Keep encrypted/decrypted diagnostics in restricted owner
storage, with a minimum two-year retention and independently tested retrieval;
do not put decryption keys or plaintext diagnostics in the public store. A
named release owner in the signed record owns retrieval, key recovery and
withdrawal. Record store commit/object identity and successful read-back in the
release record; a local temporary-directory fixture is not durable retention.

## Open live acceptance criteria

Pending: final exact-SHA complete PG17 CI; final-SHA nightly 5/6 and separate
stale-return runs on all majors; live certificate provisioning and recovery;
approved index and original GitHub artifact digests; repository-controlled durable
copy and restricted-store retrieval. Publication and protected-main authority
remain separately authorised gates. No release approval is implied here.
