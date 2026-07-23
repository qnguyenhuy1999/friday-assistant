# Web App

## Owns

- Browser control-plane delivery only.

## Must Not Own

- Reusable domain, application, or infrastructure logic.

## Current Status

TypeScript package shell only. No React, JSX, DOM access, routes,
components, CSS, state management, API calls, or build tooling exists yet.
`src/index.ts` exports a static metadata object to validate workspace
layout and type-checking.
