# SDK Package

Owns the TypeScript client-facing SDK surface.

## Policy

- Will consume canonical contracts from `@friday/contracts`.
- Must not redefine backend business rules.
- Generated output must be reproducible.
- Generated files must be clearly separated from handwritten wrappers.
- No SDK API exists yet — no client methods, fetch wrappers, or HTTP
  abstractions.
- No generated artifacts should be committed until the generation
  workflow exists.

## Current Status

Package shell only. `src/index.ts` exports a static metadata object to
validate workspace layout and type-checking.
