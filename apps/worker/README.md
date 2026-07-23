# Worker App

## Owns

- The worker delivery process's composition root (`main.py`).
- Wiring together `friday.infrastructure`, `friday.application`, and
  `friday.domain` once those layers have real behavior.

## Must Not Own

- Domain rules, use cases, or infrastructure adapters — those belong in
  `src/friday/`.
- Queue libraries, execution loops, or scheduling logic.

## May Compose

- `friday.infrastructure`
- `friday.application`
- `friday.domain`

## Current Status

No actual runtime exists yet. `main()` returns a static identification
string only. No execution loop, queue library, or scheduling code is
present.
