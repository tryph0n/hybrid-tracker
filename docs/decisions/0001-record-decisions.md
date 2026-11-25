# 0001. Record decisions

Status: Accepted.
Date: 2026-11-25.

## Context

The project depends on choices of service, storage and framework. The reasons
for them are in commit messages, tickets and memory, and they are lost with
time. A later reader cannot tell if a reason still holds, so they cannot tell
if the choice can change.

## Decision

We keep one short record for each decision of this kind, in `docs/decisions/`.
Each record follows [the template](template.md): context, decision,
alternatives rejected, consequences. The maintainer guide says when a record is
required.

## Alternatives rejected

- Write the reason in the commit message or the pull request only. It is hard
  to find later, and it is not part of the documentation.
- Write the reasons in a single page. The page grows without limit, and a
  change of one decision means an edit in the middle of unrelated text.
- Keep no record. This is the situation that the records are meant to end.

## Consequences

- A change of service, storage layer, schema major version or framework needs
  a record in the same pull request.
- Records are small files in the docs build, so a record that is not listed in
  the log fails the build.
- A decision that was taken before this record has no record, until a change
  touches it.
