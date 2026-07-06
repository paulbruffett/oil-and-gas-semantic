# Sealed change-request custody uses a deterministic sha256 file-manifest digest, not a tar-archive hash (refines 0015)

**Context.** ADR 0015 clause (3) fixes the sealed change-request set's custody as "the archive's
sha256 committed publicly pre-tag." A literal tar/zip archive hash is machine-dependent — member
ordering, mtimes, uid/gid, and (for `.gz`) a compression timestamp all bleed into the bytes — so the
same sealed contents can hash differently on two machines, which would make the pre-tag commitment
un-reproducible and the custody claim un-auditable.

**Decision.** The committed digest is a **`sha256-file-manifest-v1`**: sha256 over the sorted list of
`"<POSIX-relpath>\0<sha256(file-contents)>"` lines for every file in the sealed directory (issue #24,
`oag_harness.round2.seal_digest`). It is content-addressed, has no archive metadata, is byte-identical
across machines, and is re-derivable by hand; the manifest records the algorithm alongside the digest,
and `oag-seal verify` reproduces it from the released directory at round close.

**Why.** The whole point of pre-tag hashing is that anyone can later verify "the set was not tailored
to observed outputs" (ADR 0015) — that requires a digest that reproduces deterministically, which a raw
archive hash does not. A content-manifest hash keeps the exact same custody guarantee while removing the
archive-timestamp/order skew that would otherwise break reproduction.
