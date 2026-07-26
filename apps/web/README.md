# Web App

## Owns

- Browser control-plane delivery only.

## Must Not Own

- Reusable domain, application, or infrastructure logic.

## Current Status

Vite + React + TanStack Query control plane. Query-parameter routing serves
Tasks, Run Detail, and the run-scoped Approvals view. The app talks to the API
only through `@friday/sdk`'s `FridayClient`, uses named SSE events plus
bounded polling for live state, and renders approval authorization intent
verbatim before an explicit human decision.
