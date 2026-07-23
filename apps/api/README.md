# API App

## Owns

- The API delivery process's composition root (`main.py`).
- Wiring together `friday.infrastructure`, `friday.application`, and
  `friday.domain` once those layers have real behavior.

## Must Not Own

- Domain rules, use cases, or infrastructure adapters — those belong in
  `src/friday/`.
- Framework code, HTTP routing, or database access.

## May Compose

- `friday.infrastructure`
- `friday.application`
- `friday.domain`

## Current Status

No actual runtime exists yet. `main()` returns a static identification
string only. No framework, networking, or database access is present.
