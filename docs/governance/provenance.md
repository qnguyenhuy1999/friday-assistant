# Provenance Rules

Friday Agent OS is a **greenfield, clean-room implementation**. This document
records the rules governing how ideas and code from other systems may (or may
not) inform this repository.

## Status as of Phase 0

No upstream code has been imported into this repository. No files have been
copied, forked, or migrated from Javis OS, Hermes Agent, Graphify, or any
other project.

## Reference vs. Reuse

- **Javis OS** may be used only as a *behavioral or product reference*
  (i.e. discussing what it does or how it behaves). Copying its source code
  requires explicit reuse permission from its owner and a license
  compatibility check before any such copy is made.
- **Hermes Agent** concepts or source may only be ported into this repository
  later, and only with exact file-level provenance and preservation of its
  original license terms.
- **Graphify** may be integrated later as a dependency, accessed through an
  internal adapter layer rather than embedded directly.

## Requirements for Any Future Copied or Adapted Code

Every source file that is copied or adapted from an external repository must
document, at minimum:

- Upstream repository
- Upstream commit
- Original path
- Local path
- License
- Modification status (unmodified / modified, and how)
- Reason for reuse

This documentation should live alongside the adapted code or in a provenance
log referenced from it.

## Default Preference

When licensing terms for a piece of external code or design are unclear or
unverified, **clean-room reimplementation is the default**, not copying. This
avoids inheriting unresolved legal risk into Friday Agent OS.

See also [license-decision-required.md](license-decision-required.md) for the
repository's current license status, and
[repository-rules.md](repository-rules.md) for broader contribution rules.
